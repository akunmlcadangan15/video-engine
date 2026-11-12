"""Argument parsing, local checks and dry-run text for scripts/engine-build-modal.

The real upload and the Modal call live in video_engine.modal_launch and are imported
only when a build, fetch or self-test is actually started. Dry-run and unit tests
do not import Modal.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from . import discover
from .modal_closure import Closure, resolve_closure

DEFAULT_CPU = 8
DEFAULT_MEMORY_MB = 16384
DEFAULT_TIMEOUT_S = 10800
DEFAULT_CHUNK_MB = 64
VOLUME_NAME = "video-engine-builds"
GPU_CHOICES = ("none", "L4")
COMPARE = ("ffmpeg", "node", "hyperframes", "puppeteer-core", "three", "chrome")
# Package versions are compared in full. ffmpeg, Node and Chrome are compared by major number:
# a local ffmpeg 8.0 and the image's ffmpeg 8.1 are the same major component.
_EXACT = {"hyperframes", "puppeteer-core", "three"}


class UsageError(Exception):
    exit_code = 2


class LockBusy(Exception):
    exit_code = 75

    def __init__(self, path: Path):
        self.path = path
        super().__init__(path)


class DiskLow(Exception):
    exit_code = 74


@dataclass
class Args:
    project: Path | None
    cpu: int = DEFAULT_CPU
    memory_mb: int = DEFAULT_MEMORY_MB
    timeout_s: int = DEFAULT_TIMEOUT_S
    gpu: str = "none"
    keep_volume: bool = False
    dry_run: bool = False
    selftest: bool = False
    fetch: str | None = None
    chunk_bytes: int = DEFAULT_CHUNK_MB * 1024 * 1024
    volume: str = VOLUME_NAME
    min_free_bytes: int = 8 * 1024 ** 3


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _cpu(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("cpu must be an integer") from None
    if not 1 <= value <= 64:
        raise argparse.ArgumentTypeError("cpu must be from 1 to 64")
    return value


def _memory(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("memory must be an integer number of MB") from None
    if not 512 <= value <= 524288:
        raise argparse.ArgumentTypeError("memory must be from 512 to 524288 MB")
    return value


def _timeout(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be an integer number of seconds") from None
    if not 1 <= value <= 86400:
        raise argparse.ArgumentTypeError("timeout must be from 1 to 86400 seconds")
    return value


def parse_args(argv: list[str]) -> Args:
    parser = _Parser(
        prog="engine-build-modal",
        description="Run a final engine build on Modal and fetch out/ back. Draft builds stay local.",
    )
    parser.add_argument("project", nargs="?", help="project directory (master_timeline.json)")
    parser.add_argument("--cpu", type=_cpu, default=DEFAULT_CPU,
                        help=f"CPU request and limit, as the tuple (N, N) (default {DEFAULT_CPU})")
    parser.add_argument("--memory", type=_memory, default=DEFAULT_MEMORY_MB, metavar="MB", dest="memory_mb",
                        help=f"memory request and limit in MB, as the tuple (MB, MB) (default {DEFAULT_MEMORY_MB})")
    parser.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT_S, metavar="S", dest="timeout_s",
                        help=f"hard container timeout in seconds (default {DEFAULT_TIMEOUT_S})")
    parser.add_argument("--gpu", choices=GPU_CHOICES, default="none",
                        help="GPU type, or none (default). The build is CPU-bound; GPU is off unless this is set")
    parser.add_argument("--keep-volume", action="store_true",
                        help="leave this run's volume directory in place after a successful fetch")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate, preflight and print the closure; do not upload or call Modal")
    parser.add_argument("--selftest", action="store_true",
                        help="run engine doctor in the image and fail if major components differ from this machine")
    parser.add_argument("--fetch", metavar="RUN_ID",
                        help="resume the download of a finished run into the project, then delete the run unless --keep-volume")
    ns = parser.parse_args(argv)
    if ns.dry_run and ns.fetch:
        raise UsageError("--dry-run and --fetch cannot be combined")
    if ns.dry_run and ns.selftest:
        raise UsageError("--dry-run and --selftest cannot be combined")
    if ns.fetch and ns.selftest:
        raise UsageError("--fetch and --selftest cannot be combined")
    if not ns.selftest and not ns.project:
        raise UsageError("project directory is required")
    project = Path(ns.project).expanduser() if ns.project else None
    if project is not None and not project.is_dir():
        raise UsageError(f"project directory not found: {project}")
    return Args(
        project=project, cpu=ns.cpu, memory_mb=ns.memory_mb, timeout_s=ns.timeout_s, gpu=ns.gpu,
        keep_volume=ns.keep_volume, dry_run=ns.dry_run, selftest=ns.selftest, fetch=ns.fetch,
    )


def apply_runtime_env(args: Args, environ: dict | None = None) -> Args:
    """Chunk size, volume name and free-disk reserve. Invalid values are usage errors."""
    env = environ if environ is not None else os.environ
    raw_chunk = env.get("VIDEO_ENGINE_MODAL_CHUNK_MB", str(DEFAULT_CHUNK_MB))
    try:
        chunk_mb = int(raw_chunk)
    except ValueError:
        raise UsageError(f"VIDEO_ENGINE_MODAL_CHUNK_MB must be an integer, got {raw_chunk!r}") from None
    if chunk_mb < 1 or chunk_mb > 1024:
        raise UsageError("VIDEO_ENGINE_MODAL_CHUNK_MB must be from 1 to 1024")
    raw_free = env.get("VIDEO_ENGINE_MIN_FREE_GB", "8")
    try:
        free_gb = float(raw_free)
    except ValueError:
        raise UsageError(f"VIDEO_ENGINE_MIN_FREE_GB must be a number of GB, got {raw_free!r}") from None
    if free_gb < 0:
        raise UsageError("VIDEO_ENGINE_MIN_FREE_GB must be >= 0")
    args.chunk_bytes = chunk_mb * 1024 * 1024
    args.volume = env.get("VIDEO_ENGINE_MODAL_VOLUME", VOLUME_NAME) or VOLUME_NAME
    args.min_free_bytes = 0 if free_gb == 0 else int(free_gb * 1024 ** 3)
    return args


def load_env(environ: dict | None = None) -> dict[str, str]:
    """Load engine.env the way scripts/engine-build does. An explicit missing file is an error.

    Values fill only variables that are not already set.
    """
    env = environ if environ is not None else os.environ
    explicit = env.get("VIDEO_ENGINE_ENV_FILE")
    if explicit and not Path(explicit).is_file():
        raise UsageError(f"VIDEO_ENGINE_ENV_FILE={explicit} does not exist")
    found = discover.load_engine_env(env)
    for key, value in found.items():
        env.setdefault(key, value)
    return found


def render_lock_path(environ: dict | None = None, *, shared_root: Path | None = None) -> Path:
    """Same default as scripts/engine-build.

    VIDEO_ENGINE_RENDER_LOCK wins. Otherwise the lock is `<shared>/.render.lock` when that
    directory exists (the directory name is assembled, matching the shell wrapper), else a
    per-user runtime path. `shared_root` overrides the directory check in tests.
    """
    env = environ if environ is not None else os.environ
    if env.get("VIDEO_ENGINE_RENDER_LOCK"):
        return Path(env["VIDEO_ENGINE_RENDER_LOCK"])
    shared = shared_root if shared_root is not None else Path("/") / "workspace"
    if shared.is_dir():
        return shared / ".render.lock"
    base = env.get("XDG_RUNTIME_DIR") or env.get("TMPDIR") or "/tmp"
    return Path(base) / "video-engine.render.lock"


class RenderLock:
    """Exclusive non-blocking flock held for the whole remote build and the fetch."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> "RenderLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LockBusy(self.path) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def assert_free(path: Path, min_bytes: int) -> None:
    """Refuse when the filesystem of `path` has fewer than min_bytes free. min_bytes 0 disables it.

    Matches scripts/engine-build: free < reserve refuses, free == reserve is allowed.
    """
    if min_bytes <= 0:
        return
    target = path if path.exists() else path.parent
    free = shutil.disk_usage(target).free
    if free < min_bytes:
        have = free / 1024 ** 3
        need = min_bytes / 1024 ** 3
        raise DiskLow(f"refusing to build: only {have:.1f} GB free on the filesystem of {target}, "
                      f"need at least {need:.0f} GB (VIDEO_ENGINE_MIN_FREE_GB)")


def run_local_checks(project: Path) -> int:
    """`engine validate` then `engine preflight` in release mode. Returns the first non-zero status."""
    engine = shutil.which("engine")
    if engine is None:
        print("engine-build-modal: 'engine' is not on PATH (activate the venv: source .venv/bin/activate)", flush=True)
        return 127
    for sub in ("validate", "preflight"):
        result = subprocess.run([engine, sub, str(project)], check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def major_token(name: str, got: str) -> str:
    import re
    text = got or ""
    if name in _EXACT:
        match = re.search(r"\d+\.\d+\.\d+", text)
        return match.group(0) if match else text.strip()
    if name == "node":
        match = re.search(r"v?(\d+)", text)
        return match.group(1) if match else text.strip()
    match = re.search(r"\d+", text)
    return match.group(0) if match else text.strip()


def components_from_checks(checks: list[dict]) -> dict[str, str]:
    by = {str(item.get("name")): str(item.get("got") or "") for item in checks}
    out: dict[str, str] = {}
    if by.get("ffmpeg-version"):
        out["ffmpeg"] = by["ffmpeg-version"]
    elif by.get("ffmpeg"):
        out["ffmpeg"] = by["ffmpeg"]
    for name in ("node", "hyperframes", "puppeteer-core", "three", "chrome"):
        if by.get(name):
            out[name] = by[name]
    return out


def compare_components(local: dict, remote: dict) -> list[str]:
    """Empty when every major component agrees. ffmpeg, Node and Chrome by major; the three
    npm packages by full x.y.z."""
    bad = []
    for name in COMPARE:
        left, right = local.get(name), remote.get(name)
        if not left or not right:
            bad.append(f"{name}: missing local={left!r} remote={right!r}")
            continue
        a, b = major_token(name, left), major_token(name, right)
        if a != b:
            bad.append(f"{name}: local {left} ({a}) != remote {right} ({b})")
    return bad


def ffmpeg_major(text: str) -> int | None:
    import re
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def image_build_args(snapshot: dict, pins: dict) -> dict[str, str]:
    """Docker build args for the image: local doctor versions when they parse, else the lockfile."""
    components = snapshot.get("components") or {}
    node_pin = pins["node"]["version"]
    npm = pins["npm"]
    chrome_pin = pins["chrome"]["version"]

    def node(value: str) -> str:
        import re
        match = re.search(r"v\d+\.\d+\.\d+", value or "")
        return match.group(0) if match else node_pin

    def semver(value: str, fallback: str) -> str:
        import re
        match = re.search(r"\d+\.\d+\.\d+", value or "")
        return match.group(0) if match else fallback

    def chrome(value: str) -> str:
        import re
        match = re.search(r"\d+\.\d+\.\d+\.\d+", value or "")
        return match.group(0) if match else chrome_pin

    return {
        "NODE_VERSION": node(str(components.get("node") or "")),
        "HF_VERSION": semver(str(components.get("hyperframes") or ""), npm["hyperframes"]),
        "PUPPETEER_VERSION": semver(str(components.get("puppeteer-core") or ""), npm["puppeteer-core"]),
        "THREE_VERSION": semver(str(components.get("three") or ""), npm["three"]),
        "CHROME_VERSION": chrome(str(components.get("chrome") or "")),
    }


def toolchain_snapshot() -> dict:
    """Local `engine doctor` versions. A machine without the native module returns an error key
    and empty components; `--selftest` then fails closed."""
    try:
        from . import doctor
    except Exception as exc:  # noqa: BLE001  (missing native module or a broken checkout)
        return {"error": f"{type(exc).__name__}: {exc}", "components": {}, "checks": []}
    try:
        checks = [{"name": item.name, "ok": item.ok, "want": item.want, "got": item.got, "path": item.path}
                  for item in doctor.check()]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "components": {}, "checks": []}
    return {"components": components_from_checks(checks), "checks": checks}


def ffmpeg_pin() -> dict:
    path = Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg-static.json"
    return json.loads(path.read_text(encoding="utf-8"))


def version_pins() -> dict:
    path = Path(__file__).resolve().parents[1] / "vendor" / "VERSIONS.json"
    return json.loads(path.read_text(encoding="utf-8"))


def format_dry_run(closure: Closure, args: Args, *, toolchain: dict | None = None) -> str:
    lines = [
        "engine-build-modal: dry run",
        f"project: {closure.project}",
        f"files: {len(closure.files)}",
        f"upload_bytes: {closure.upload_bytes}",
        f"digest: {closure.digest}",
        f"missing: {len(closure.missing)}",
        f"resources: cpu=({args.cpu}, {args.cpu}) memory=({args.memory_mb}, {args.memory_mb}) "
        f"gpu={args.gpu} timeout={args.timeout_s}s retries=0 min_containers=0 "
        f"scaledown_window=2 single_use_containers=true",
        f"volume: {args.volume}",
        f"keep_volume: {str(args.keep_volume).lower()}",
        "env:",
    ]
    for key in sorted(closure.env):
        lines.append(f"  {key}={closure.env[key]}")
    lines.append("files:")
    for item in closure.files:
        kind = "upload" if item.ship else "image"
        link = "yes" if item.symlink else "no"
        lines.append(f"  {kind}\t{item.role}\t{item.root}\t{item.rel}\t{item.sha256}\t{item.size}\tsymlink={link}")
    if closure.missing:
        lines.append("missing:")
        for missing in closure.missing:
            lines.append(f"  {missing['role']}\t{missing['ref']}\t{missing['reason']}")
    if toolchain is not None:
        components = toolchain.get("components") or {}
        if toolchain.get("error"):
            lines.append(f"toolchain: unavailable ({toolchain['error']})")
        else:
            rendered = ", ".join(f"{name}={components[name]}" for name in COMPARE if components.get(name))
            lines.append(f"toolchain: {rendered or 'unknown'}")
    lines.append("modal: not called")
    return "\n".join(lines) + "\n"


def _die(exc: Exception) -> int:
    code = getattr(exc, "exit_code", 1)
    print(f"engine-build-modal: {exc}", flush=True)
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(list(sys_argv(argv)))
        load_env()
        args = apply_runtime_env(args)
    except UsageError as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 2
    if args.selftest:
        return _selftest(args)
    assert args.project is not None
    project = args.project.resolve()
    if args.fetch:
        return _fetch(args, project)
    try:
        if not args.dry_run:
            assert_free(project / "out" if (project / "out").exists() else project, args.min_free_bytes)
    except DiskLow as exc:
        return _die(exc)
    if args.dry_run:
        code = run_local_checks(project)
        if code != 0:
            return code
        closure = resolve_closure(project)
        print(format_dry_run(closure, args, toolchain=toolchain_snapshot()), end="")
        return 0 if closure.ok else 1
    try:
        with RenderLock(render_lock_path()):
            code = run_local_checks(project)
            if code != 0:
                return code
            closure = resolve_closure(project)
            if not closure.ok:
                print(format_dry_run(closure, args), end="")
                return 1
            from .modal_launch import run_build
            return run_build(args, project, closure)
    except LockBusy as exc:
        print(f"engine-build-modal: another render holds the lock {exc.path} "
              f"(check: flock -n {exc.path} true)", flush=True)
        return 75
    except DiskLow as exc:
        return _die(exc)


def sys_argv(argv: list[str] | None) -> list[str]:
    import sys
    return list(sys.argv[1:] if argv is None else argv)


def _selftest(args: Args) -> int:
    local = toolchain_snapshot()
    if local.get("error") or not local.get("components"):
        print(f"engine-build-modal: local engine doctor is not available ({local.get('error') or 'no components'})",
              flush=True)
        return 1
    from .modal_launch import run_selftest
    remote = run_selftest(args)
    mismatches = compare_components(local.get("components") or {}, remote.get("components") or {})
    floor = int(ffmpeg_pin().get("major") or 8)
    got = ffmpeg_major((remote.get("components") or {}).get("ffmpeg", ""))
    if got is None or got < floor:
        mismatches.append(f"ffmpeg: image major {got} is below the pinned static build major {floor}")
    print("engine-build-modal: local doctor", flush=True)
    for name in COMPARE:
        value = (local.get("components") or {}).get(name)
        if value:
            print(f"  {name}: {value}", flush=True)
    print("engine-build-modal: image doctor", flush=True)
    for line in remote.get("lines") or []:
        print(line, flush=True)
    if mismatches:
        print("engine-build-modal: toolchain mismatch", flush=True)
        for line in mismatches:
            print(f"  {line}", flush=True)
        return 1
    print("engine-build-modal: major components match", flush=True)
    return 0


def _fetch(args: Args, project: Path) -> int:
    try:
        assert_free(project / "out" if (project / "out").exists() else project, args.min_free_bytes)
        with RenderLock(render_lock_path()):
            from .modal_launch import resume_fetch
            return resume_fetch(args, project, args.fetch or "")
    except LockBusy as exc:
        print(f"engine-build-modal: another render holds the lock {exc.path}", flush=True)
        return 75
    except DiskLow as exc:
        return _die(exc)
