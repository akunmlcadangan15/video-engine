"""Sequential batch runner: one queue, one project lock per build, no two runs of the same project.

The lock lives at <project>/out/.batch.lock and is held with `flock` for the whole build; `engine build` takes the
same lock. A second build or batch that names the same project fails with an EngineError instead of racing its `out/`
manifest and credits. The queue is
a JSON array, a JSON object with `projects`, or one path per line; relative paths resolve against the queue file.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from .util import EngineError

LOCK_NAME = ".batch.lock"


class ProjectLock:
    """Exclusive, non-blocking lock of one project directory."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.path = self.project / "out" / LOCK_NAME
        self.fd: int | None = None

    def acquire(self) -> "ProjectLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise EngineError(f"project {self.project} is already locked by another build or batch") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self.fd = fd
        return self

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self) -> "ProjectLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def load_queue(path: Path) -> list[Path]:
    """Project paths from a queue file: a JSON array, a JSON object with `projects`, or one path per line."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise EngineError(f"cannot read queue {p}: {e}") from None
    data = None
    if p.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise EngineError(f"queue {p} is not valid JSON: {e}") from None
    if isinstance(data, dict):
        data = data.get("projects", data.get("queue"))
    if isinstance(data, list):
        items = [str(x) for x in data]
    elif data is not None:
        raise EngineError(f"queue {p}: expected a list or an object with `projects`")
    else:
        items = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not items:
        raise EngineError(f"queue {p} names no projects")
    return [Path(x).expanduser() if Path(x).is_absolute() else (p.parent / x) for x in items]


def _default_build(project: Path, *, run_label: str, use_cache: bool, relock: bool):
    from . import build
    return build.build(project, use_cache=use_cache, run_label=run_label, raise_on_fail=False, relock=relock)


def run(projects: list[Path], *, build_fn=None, fail_fast: bool = False, run_label: str = "batch",
        use_cache: bool = True, relock: bool = False) -> dict:
    """Build every project in order. A gate fail or exception is recorded; `fail_fast` stops after the first one."""
    build_fn = build_fn or (lambda p: _default_build(p, run_label=run_label, use_cache=use_cache, relock=relock))
    seen, queue = set(), []
    for p in projects:
        rp = Path(p).resolve()
        if rp not in seen:
            seen.add(rp)
            queue.append(rp)
    rows, stopped = [], False
    for p in queue:
        t0 = time.monotonic()
        row = {"project": str(p), "ok": False, "final": None, "failed": [], "error": None}
        try:
            with ProjectLock(p):
                r = build_fn(p)
            row["ok"] = bool(getattr(r, "ok", True))
            row["final"] = str(final) if (final := getattr(r, "final", None)) else None
            row["failed"] = list(getattr(r, "failed", []) or [])
        except Exception as e:  # noqa: BLE001  (one project must not abort the queue)
            row["error"] = f"{type(e).__name__}: {e}"
        row["seconds"] = round(time.monotonic() - t0, 3)
        rows.append(row)
        if not row["ok"] and fail_fast:
            stopped = True
            break
    return {"ok": all(r["ok"] for r in rows), "stopped": stopped, "projects": rows,
            "queue": [str(p) for p in queue], "ran": len(rows)}
