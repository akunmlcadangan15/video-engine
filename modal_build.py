"""Ephemeral Modal app for a final engine build.

scripts/engine-build-modal loads this module and enters App.run (the same lifecycle
as `modal run`). This file does not call App.deploy. CPU and memory are (request, limit)
tuples read from the environment at import, so the wrapper can size one run without a
deployed app. The default is CPU only; a GPU is attached only when VIDEO_ENGINE_MODAL_GPU
is L4.

The image installs the lockfile toolchain (or the versions passed as Docker build args,
taken from the local doctor when it can run) and a pinned FFmpeg 8.1 static GPL build.
"""
from __future__ import annotations

import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _build_args() -> dict[str, str]:
    try:
        from video_engine.modal_cli import ffmpeg_pin, image_build_args, toolchain_snapshot, version_pins
        args = image_build_args(toolchain_snapshot(), version_pins())
        pin = ffmpeg_pin()
        args["FFMPEG_MAJOR"] = str(pin.get("major") or 8)
        return args
    except Exception:
        return {}


_CPU = float(_int_env("VIDEO_ENGINE_MODAL_CPU", 8))
_MEM = _int_env("VIDEO_ENGINE_MODAL_MEMORY_MB", 16384)
_TIMEOUT = _int_env("VIDEO_ENGINE_MODAL_TIMEOUT_S", 10800)
_DISK = _int_env("VIDEO_ENGINE_MODAL_DISK_MB", 102400)
_GPU = os.environ.get("VIDEO_ENGINE_MODAL_GPU", "none")
_VOLUME = os.environ.get("VIDEO_ENGINE_MODAL_VOLUME", "video-engine-builds")

import modal  # noqa: E402  (resource env is read before the app is constructed)

app = modal.App("video-engine-build")
image = modal.Image.from_dockerfile(
    path=Path(__file__).with_name("Dockerfile.modal"),
    context_dir=Path(__file__).resolve().parent,
    build_args=_build_args(),
)
volume = modal.Volume.from_name(_VOLUME, create_if_missing=True, version=2)

_gpu = {} if _GPU in ("", "none") else {"gpu": _GPU}


@app.function(
    image=image,
    cpu=(_CPU, _CPU),
    memory=(_MEM, _MEM),
    timeout=_TIMEOUT,
    retries=0,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    single_use_containers=True,
    ephemeral_disk=_DISK,
    volumes={"/vol": volume},
    **_gpu,
)
def build_remote(run_id: str, selftest: bool = False, resources: dict | None = None) -> dict:
    if selftest:
        return _selftest()
    return _build(run_id, resources or {})


@app.local_entrypoint()
def main(selftest: bool = False, run_id: str = "") -> None:
    """Manual entry. Production runs go through scripts/engine-build-modal, which uses App.run."""
    if not selftest:
        raise SystemExit("use scripts/engine-build-modal <project> for a final build; drafts stay local")
    print(build_remote.remote(run_id, True, {}))


def _selftest() -> dict:
    import json
    from video_engine.modal_cli import components_from_checks
    from video_engine import doctor
    checks = doctor.check()
    serialized = [{"name": item.name, "ok": item.ok, "want": item.want, "got": item.got, "path": item.path}
                  for item in checks]
    pin = Path("/opt/video-engine/toolchain.json")
    recorded = json.loads(pin.read_text(encoding="utf-8")) if pin.is_file() else {}
    print("\n".join(item.line() for item in checks), flush=True)
    return {
        "lines": [item.line() for item in checks],
        "components": components_from_checks(serialized),
        "toolchain_file": recorded,
        "image_id": os.environ.get("MODAL_IMAGE_ID", ""),
    }


def _build(run_id: str, resources: dict) -> dict:
    import json
    import subprocess
    import time
    from video_engine.modal_closure import materialize
    from video_engine.modal_transfer import annotate_reports, publish_outputs, remote_record
    layout_path = Path("/vol/runs") / run_id / "layout.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    materialize(layout, Path("/vol/cas/blobs"))
    env = dict(os.environ)
    env.update(layout.get("env") or {})
    cpu = int(resources.get("cpu") or _CPU)
    env["VIDEO_ENGINE_THREADS"] = str(max(1, cpu))
    env["PYTHONUNBUFFERED"] = "1"
    started = time.perf_counter()
    proc = subprocess.run(["engine", "build", "/work/project"], env=env, check=False)
    wall = time.perf_counter() - started
    project = Path("/work/project")
    record = remote_record(
        host=_host(), image_id=os.environ.get("MODAL_IMAGE_ID", ""),
        toolchain_pin_sha256=_pin_sha(), cpu=(float(resources.get("cpu") or _CPU), float(resources.get("cpu") or _CPU)),
        memory_mb=(int(resources.get("memory_mb") or _MEM), int(resources.get("memory_mb") or _MEM)),
        gpu=str(resources.get("gpu") or "none"), wall_s=wall, peak_rss_mb=_peak_rss_mb(),
        vcpu=os.cpu_count(), timeout_s=int(resources.get("timeout_s") or _TIMEOUT),
        toolchain=_remote_toolchain(), mismatches=_mismatches(layout),
    )
    annotate_reports(project, record)
    publish_outputs(project, Path("/vol/runs") / run_id / "fetch", _chunk_bytes())
    volume.commit()
    return {"returncode": proc.returncode, "wall_s": wall, "remote": record}


def _chunk_bytes() -> int:
    try:
        mb = int(os.environ.get("VIDEO_ENGINE_MODAL_CHUNK_MB", "64"))
    except ValueError:
        mb = 64
    return max(1, mb) * 1024 * 1024


def _host() -> dict:
    info = os.uname()
    return {"sysname": info.sysname, "nodename": info.nodename, "release": info.release, "machine": info.machine}


def _peak_rss_mb() -> float | None:
    for candidate in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            raw = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw.isdigit():
            return round(int(raw) / (1024 * 1024), 1)
    return None


def _pin_sha() -> str:
    import hashlib
    path = Path("/opt/video-engine/toolchain.json")
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remote_toolchain() -> dict:
    try:
        from video_engine import doctor
        from video_engine.modal_cli import components_from_checks
        checks = [{"name": item.name, "got": item.got} for item in doctor.check()]
        return components_from_checks(checks)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _mismatches(layout: dict) -> list[str]:
    try:
        from video_engine.modal_cli import compare_components
    except Exception:
        return []
    local = (layout.get("local_toolchain") or {}).get("components") or {}
    remote = _remote_toolchain()
    if not local or remote.get("error"):
        return []
    return compare_components(local, remote)
