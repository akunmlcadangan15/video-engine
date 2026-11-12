"""Content-addressed BLAKE3 cache on disk (survives /dev/shm cleanup). Keys are hashes of the full input config.

Leases: inside `session()` (every CLI command and `build.build`) each cache file the process resolves or is about
to write is leased before its existence is checked, under a shared lock of the cache root. Eviction (`prune`, LRU),
`clear`, `engine cache --prune` and the sound cache prune hold the exclusive lock while they scan and delete, skip
every leased file and report it. A lease lives in <root>/.leases/<pid>-<start>.json as JSON lines, one
{"pid", "start", "paths": [path relative to the root]} per acquired path (append-only, so a store never rewrites
the whole set); start = field 22 of /proc/<pid>/stat (process start time in clock ticks; on macOS, with no /proc, the `ps -o lstart=`
time with whitespace as "_" — an opaque token either way, written and compared on the same OS). The session removes its
file on exit (success, failure, exception, AwaitingAgent). A pruner reclaims (deletes) a lease file whose pid is
gone or runs a different process (other start time).
"""
from __future__ import annotations

import contextlib
import fcntl
import itertools
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config


KINDS = ("words", "pauses", "segments", "maps", "models", "dom", "composite", "audio", "toolchain", "local_test_sfx")   # entry directories directly under the cache root
LEASES = ".leases"                      # lease files: <root>/.leases/<pid>-<start>.json
LOCK = ".lock"                          # flock: shared while leasing, exclusive while evicting
# LRU housekeeping is not run on every stored file: a full cache scan is O(total cache) under the exclusive lock.
# put_file prunes when the entry alone exceeds the budget, after PRUNE_EVERY_PUTS stores, or every PRUNE_INTERVAL_S.
PRUNE_EVERY_PUTS = 32
PRUNE_INTERVAL_S = 30.0
_prune_state: dict[str, dict] = {}   # root -> {puts, last monotonic time}; per process, throttles put_file pruning


# ---------------------------------------------------------------------------------------------------- leases

def _have_proc() -> bool:
    return Path("/proc/self/stat").exists()


_own_ps_start: dict[int, str | None] = {}   # darwin: this process's start token (ps is spawned once per pid)


def _ps_start(pid: int) -> str | None:
    """Start time of pid from `ps -o lstart=` ("Mon Nov  2 10:00:00 2026", whitespace joined by "_": no spaces in the
    lease file name), None when no such process or ps is unavailable. Used where there is no /proc (macOS)."""
    own = pid == os.getpid()
    if own and pid in _own_ps_start:
        return _own_ps_start[pid]
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return None                     # not cached: ps may work next time
    start = "_".join(out.split()) or None
    if own:
        _own_ps_start[pid] = start
    return start


def proc_start(pid: int) -> str | None:
    """Start time of process pid (field 22 of /proc/<pid>/stat; without /proc the `ps` lstart), None when no such
    process (or, without /proc, when ps cannot tell)."""
    if not _have_proc():
        return _ps_start(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    return stat[stat.rindex(")") + 2:].split()[19]


def holder_alive(pid: int, start: str) -> bool:
    """The process that wrote a lease (pid, start) still runs: same pid with the same start time. Without /proc
    (macOS) the start is the ps lstart; when it is unknown on either side (lease written while ps failed: "None")
    the pid merely has to exist."""
    if not _have_proc():
        cur = _ps_start(pid)
        if cur is not None and str(start) not in ("None", ""):
            return cur == str(start)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    return proc_start(pid) == str(start)


_held = threading.local()   # per thread: {root: depth} of the flocks it holds


@contextlib.contextmanager
def locked(root: Path, exclusive: bool):
    """flock on <root>/.lock (re-entrant within a thread)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    depth = getattr(_held, "d", None)
    if depth is None:
        depth = _held.d = {}
    key = str(root.resolve())
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    with open(root / LOCK, "a+") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0
            fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass
class Session:
    """This process's leases and the evictions it ran, for the build report."""
    depth: int = 0
    paths: dict[str, set[str]] = field(default_factory=dict)        # root -> leased paths relative to it
    evicted: dict[str, None] = field(default_factory=dict)          # ordered sets of <root>/<rel> strings
    skipped: dict[str, None] = field(default_factory=dict)
    reclaimed: dict[str, None] = field(default_factory=dict)

    def report(self) -> dict:
        return {"leased": sum(len(v) for v in self.paths.values()), "evicted": list(self.evicted),
                "skipped_leased": list(self.skipped), "reclaimed_leases": list(self.reclaimed)}


_mu = threading.RLock()
_session = Session()


def _lease_file(root: Path) -> Path:
    pid = os.getpid()
    return root / LEASES / f"{pid}-{proc_start(pid)}.json"


@contextlib.contextmanager
def session():
    """Lease scope of a build or command; the outermost exit releases every lease of this process."""
    with _mu:
        _session.depth += 1
    try:
        yield _session
    finally:
        with _mu:
            _session.depth -= 1
            if _session.depth == 0:
                for r in list(_session.paths):
                    _lease_file(Path(r)).unlink(missing_ok=True)
                _session.paths.clear()
                for d in (_session.evicted, _session.skipped, _session.reclaimed):
                    d.clear()


def acquire(path: Path, root: Path | None = None) -> Path:
    """Lease `path` (a file under the cache root) for the session, before the caller checks or uses it. Outside a
    session this does nothing."""
    root = Path(root or config.CACHE_DIR)
    with _mu:
        if _session.depth == 0:
            return path
        try:
            rel = str(Path(path).relative_to(root))
        except ValueError:
            return path
        with locked(root, exclusive=False):
            have = _session.paths.setdefault(str(root), set())
            if rel not in have:
                have.add(rel)
                f = _lease_file(root)
                f.parent.mkdir(parents=True, exist_ok=True)
                pid = os.getpid()
                with open(f, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"pid": pid, "start": proc_start(pid), "paths": [rel]},
                                        separators=(",", ":")) + "\n")
    return path


def leased(root: Path) -> tuple[set[str], list[str]]:
    """(paths leased by running processes, reclaimed lease files). Call under the exclusive lock: lease files of
    dead holders (pid gone or reused) are deleted."""
    d = Path(root) / LEASES
    paths, reclaimed = set(), []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            pid_s, start = f.stem.split("-", 1)
            pid = int(pid_s)
        except ValueError:
            continue
        if not holder_alive(pid, start):
            f.unlink(missing_ok=True)
            reclaimed.append(f.name)
            continue
        try:
            text = f.read_text()
        except FileNotFoundError:
            continue
        try:
            rows = [json.loads(text)]
        except json.JSONDecodeError:
            rows = []
            for line in text.splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for row in rows:
            try:
                paths |= set(row["paths"])
            except (KeyError, TypeError):
                continue
    return paths, reclaimed


def _maybe_prune(dst: Path, root: Path) -> None:
    """Throttled LRU prune after a store: an entry larger than the budget is evicted at once; otherwise housekeeping
    waits for PRUNE_EVERY_PUTS stores or PRUNE_INTERVAL_S (a build is not a cache-scan loop)."""
    max_bytes = config.CACHE_MAX_BYTES
    try:
        size = dst.stat().st_size
    except FileNotFoundError:
        return
    st = _prune_state.setdefault(str(root), {"puts": 0, "last": 0.0})
    st["puts"] += 1
    now = time.monotonic()
    due = (size > max_bytes or st["puts"] >= PRUNE_EVERY_PUTS
           or (st["last"] and now - st["last"] >= PRUNE_INTERVAL_S))
    if not due:
        return
    prune(protect=(dst,), root=root)
    st["puts"], st["last"] = 0, now


def _note(root: Path, evicted=(), skipped=(), reclaimed=()) -> None:
    with _mu:
        if _session.depth:
            for src, dst in ((evicted, _session.evicted), (skipped, _session.skipped), (reclaimed, _session.reclaimed)):
                for x in src:
                    dst[f"{root}/{x}"] = None


def _is_entry(rel: Path) -> bool:
    """A cache file eviction may consider: not lease bookkeeping, not a writer's temp file."""
    return rel.parts[0] not in (LEASES, LOCK) and not rel.name.endswith(".tmp")


# ---------------------------------------------------------------------------------------------------- store

def clear(root: Path | None = None) -> dict:
    """Delete the files of the entry directories (KINDS) under the cache root, except leased ones; other directories
    under it are kept. Returns {"deleted", "skipped_leased", "reclaimed_leases"} (paths relative to the root)."""
    root = Path(root or config.CACHE_DIR)
    out = {"deleted": [], "skipped_leased": [], "reclaimed_leases": []}
    if not root.is_dir():
        return out
    with locked(root, exclusive=True):
        keep, out["reclaimed_leases"] = leased(root)
        for k in KINDS:
            d = root / k
            if not d.is_dir():
                continue
            for f in sorted(p for p in d.rglob("*") if p.is_file()):
                rel = str(f.relative_to(root))
                if rel in keep:
                    out["skipped_leased"].append(rel)
                else:
                    f.unlink(missing_ok=True)
                    out["deleted"].append(rel)
            if not any(d.rglob("*")):
                shutil.rmtree(d, ignore_errors=True)
    return out


_tmp_counter = itertools.count()  # monotonic per-process sequence; never reused, unlike threading.get_ident()


def tmp_name(dst: Path) -> Path:
    """Writer-unique temp path next to dst (concurrent writers of the same key never share a temp file)."""
    return dst.with_name(f"{dst.name}.{os.getpid()}.{next(_tmp_counter)}.tmp")


def _p(kind: str, key: str, ext: str) -> Path:
    d = config.CACHE_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}{ext}"


def get_file(kind: str, key: str, ext: str = ".mp4") -> Path | None:
    p = acquire(_p(kind, key, ext))
    try:
        if p.stat().st_size > 0:
            os.utime(p)  # LRU touch
            return p
    except FileNotFoundError:
        pass
    return None


def put_file(kind: str, key: str, src: Path, ext: str = ".mp4") -> Path:
    dst = acquire(_p(kind, key, ext))
    tmp = acquire(tmp_name(dst))
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)  # atomic: a crash never leaves a half file under a valid key
    _maybe_prune(dst, Path(config.CACHE_DIR))
    return dst


def get_json(kind: str, key: str):
    p = acquire(_p(kind, key, ".json"))
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return None
    try:
        os.utime(p)  # LRU touch, as get_file: an entry in use is not the first one evicted
    except FileNotFoundError:
        pass
    return data


def put_json(kind: str, key: str, obj) -> None:
    p = acquire(_p(kind, key, ".json"))
    tmp = acquire(tmp_name(p))
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, p)


@dataclass
class Pruned:
    bytes: int = 0
    deleted: list[str] = field(default_factory=list)          # paths relative to the cache root
    skipped_leased: list[str] = field(default_factory=list)
    reclaimed_leases: list[str] = field(default_factory=list)
    skipped_pinned: list[str] = field(default_factory=list)   # sound files a project lock still names


def _pinned(root: Path) -> set[str]:
    """Sound-cache paths a project lock still names (video_engine.sound.pins). Empty when the registry is absent."""
    try:
        from .sound import pins
    except ModuleNotFoundError:
        return set()
    return pins.protected_rels(root)


def prune(max_bytes: int | None = None, protect=(), root: Path | None = None) -> Pruned:
    """Drop least-recently-used files until the cache fits, skipping leased files, pinned sound files and
    `protect` (the entry being stored). A pinned file stays even when the cache is over `max_bytes`; only
    unreferenced files are removed. Returns what was deleted, skipped and reclaimed."""
    max_bytes = config.CACHE_MAX_BYTES if max_bytes is None else max_bytes
    root = Path(root or config.CACHE_DIR)
    res = Pruned()
    if not root.is_dir():
        return res
    with locked(root, exclusive=True):
        keep, res.reclaimed_leases = leased(root)
        keep |= {str(Path(p).relative_to(root)) for p in protect}
        pinned = _pinned(root)
        files = []
        for p in root.rglob("*"):
            try:  # concurrent writers rename/remove temp files while we scan
                rel = p.relative_to(root)
                if _is_entry(rel) and p.is_file():
                    st = p.stat()
                    files.append((st.st_mtime, st.st_size, p, str(rel)))
            except FileNotFoundError:
                continue
        total = sum(f[1] for f in files)
        for _, s, p, rel in sorted(files, key=lambda f: f[0]):
            if total <= max_bytes:
                break
            if rel in pinned:
                res.skipped_pinned.append(rel)
                continue
            if rel in keep:
                res.skipped_leased.append(rel)
                continue
            p.unlink(missing_ok=True)
            total -= s
            res.bytes += s
            res.deleted.append(rel)
    _note(root, res.deleted, res.skipped_leased, res.reclaimed_leases)
    return res
