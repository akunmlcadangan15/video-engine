"""Batch runner: queue loading, per-project lock, order, fail-fast and CLI wiring."""
import json
import os
from types import SimpleNamespace

import pytest

from video_engine import batch, cli
from video_engine.util import EngineError


def _proj(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return p


def test_good_run_builds_in_queue_order(tmp_path):
    a, b = _proj(tmp_path, "a"), _proj(tmp_path, "b")
    seen = []

    def build(p):
        seen.append(p.name)
        return SimpleNamespace(ok=True, final=p / "out" / "final.mp4", failed=[])

    r = batch.run([b, a, b], build_fn=build)
    assert r["ok"] and r["ran"] == 2 and seen == ["b", "a"]
    assert [x["project"] for x in r["projects"]] == [str(b.resolve()), str(a.resolve())]
    assert all(x["ok"] and x["final"].endswith("final.mp4") for x in r["projects"])


def test_bad_run_reports_gate_fail_and_continues(tmp_path):
    a, b = _proj(tmp_path, "a"), _proj(tmp_path, "b")

    def build(p):
        return SimpleNamespace(ok=p.name == "b", final=None, failed=["T02: bad"] if p.name == "a" else [])

    r = batch.run([a, b], build_fn=build)
    assert not r["ok"] and r["ran"] == 2
    assert r["projects"][0]["failed"] == ["T02: bad"] and r["projects"][1]["ok"]


def test_bad_run_fail_fast_stops_after_the_first_failure(tmp_path):
    a, b = _proj(tmp_path, "a"), _proj(tmp_path, "b")
    seen = []

    def build(p):
        seen.append(p.name)
        return SimpleNamespace(ok=False, final=None, failed=["X"])

    r = batch.run([a, b], build_fn=build, fail_fast=True)
    assert not r["ok"] and r["stopped"] and seen == ["a"]


def test_bad_same_project_is_locked(tmp_path):
    p = _proj(tmp_path, "p")
    called = []
    with batch.ProjectLock(p):
        r = batch.run([p], build_fn=lambda q: called.append(q))
    assert not r["ok"] and "already locked" in r["projects"][0]["error"] and called == []
    # the lock is released when the context exits
    assert batch.run([p], build_fn=lambda q: SimpleNamespace(ok=True, final=None, failed=[]))["ok"]


def test_good_load_queue_json_list_object_and_lines(tmp_path):
    q = tmp_path / "q.json"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    q.write_text(json.dumps(["a", str(tmp_path / "b")]))
    assert batch.load_queue(q) == [tmp_path / "a", tmp_path / "b"]
    q.write_text(json.dumps({"projects": ["a"]}))
    assert batch.load_queue(q) == [tmp_path / "a"]
    lines = tmp_path / "q.txt"
    lines.write_text("# comment\na\n\n" + str(tmp_path / "b") + "\n")
    assert batch.load_queue(lines) == [tmp_path / "a", tmp_path / "b"]


def test_bad_load_queue_invalid_json_and_empty(tmp_path):
    q = tmp_path / "q.json"
    q.write_text("{broken")
    with pytest.raises(EngineError, match="not valid JSON"):
        batch.load_queue(q)
    q.write_text("[]")
    with pytest.raises(EngineError, match="names no projects"):
        batch.load_queue(q)


def test_good_cli_batch_with_queue(tmp_path, monkeypatch, capsys):
    a, b = _proj(tmp_path, "a"), _proj(tmp_path, "b")
    monkeypatch.setattr(batch, "run", lambda projects, **kw: {"ok": True, "ran": len(projects), "projects": [],
                                                              "queue": [str(x) for x in projects], "stopped": False})
    q = tmp_path / "q.txt"
    q.write_text("a\nb\n")
    assert cli.main(["batch", "--queue", str(q)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] == 2 and out["queue"] == [str(a), str(b)]


def _dead_pid():
    import subprocess
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_good_stale_lock_file_cleaned_automatically(tmp_path):
    """A stale lockfile whose owner PID is dead is cleaned up on acquire instead of blocking."""
    p = _proj(tmp_path, "p")
    lock = p / "out" / ".batch.lock"
    lock.parent.mkdir()
    lock.write_text(f"{_dead_pid()}\n")
    with batch.ProjectLock(p):
        assert lock.is_file() and lock.read_text().strip() == str(os.getpid())
    assert not lock.exists()


def test_good_lock_file_removed_after_release(tmp_path):
    p = _proj(tmp_path, "p")
    with batch.ProjectLock(p) as lk:
        assert lk.path.is_file()
    assert not lk.path.exists()


def test_bad_live_lock_still_blocks_acquire(tmp_path):
    p = _proj(tmp_path, "p")
    with batch.ProjectLock(p):
        with pytest.raises(EngineError, match="already locked"):
            batch.ProjectLock(p).acquire()


def test_good_lock_is_retaken_when_the_file_was_unlinked_between_open_and_flock(tmp_path, monkeypatch):
    """A holder that finishes between another build's open and its flock unlinks the file: the flock then granted
    is on a file nobody else can reach, so acquire must notice and lock the file the path names now."""
    import fcntl
    p = _proj(tmp_path, "p")
    lock = p / "out" / ".batch.lock"
    real, calls = fcntl.flock, []

    def flock(fd, op):
        if not calls:
            lock.unlink()                       # the earlier holder's release, right after our open
        calls.append(op)
        return real(fd, op)
    monkeypatch.setattr(batch.fcntl, "flock", flock)
    with batch.ProjectLock(p) as lk:
        assert (os.fstat(lk.fd).st_ino, lock.read_text().strip()) == (os.stat(lock).st_ino, str(os.getpid()))
        assert len(calls) == 2                  # locked the unlinked file once, then the real one
        monkeypatch.undo()
        with pytest.raises(EngineError, match="already locked"):
            batch.ProjectLock(p).acquire()


def test_good_stale_cleanup_never_removes_a_held_lock(tmp_path):
    """The file of a running build is not removed even when its recorded pid reads as dead (a build that has the
    flock but has not written its pid yet): cleanup needs the flock itself."""
    p = _proj(tmp_path, "p")
    with batch.ProjectLock(p) as lk:
        lk.path.write_text(f"{_dead_pid()}\n")
        batch._clean_stale_lock(lk.path)
        assert lk.path.is_file()
    lk.path.parent.mkdir(exist_ok=True)
    lk.path.write_text(f"{_dead_pid()}\n")      # a crashed owner's leftover: removed
    batch._clean_stale_lock(lk.path)
    assert not lk.path.exists()
