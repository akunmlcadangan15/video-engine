"""The test suite leaves no scratch in /dev/shm and never removes a pre-existing scratch dir of a real build."""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from conftest import SHM_PARENT, TEST_ROOT, cleanup_scratch, remove_dead_session_roots

ROOT = Path(__file__).resolve().parent.parent


def _nested() -> subprocess.Popen:
    """A nested pytest session; its scratch root is SHM_PARENT / f"pytest-{pid}" (pid of the returned process)."""
    return subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "tests/test_timeline_frames.py"], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=os.environ.copy())


def test_good_nested_run_removes_its_root_and_keeps_existing_parent():
    sentinel = SHM_PARENT / f"real_build_sentinel-{os.getpid()}-{uuid.uuid4().hex}"
    sentinel.write_text("x")
    try:
        proc = _nested()
        out, _ = proc.communicate(timeout=600)
        assert proc.returncode == 0, out[-2000:]
        assert sentinel.exists()                                   # parent pre-existed: kept with its content
        assert TEST_ROOT.exists()                                  # this (outer) session's root is untouched
        assert not (SHM_PARENT / f"pytest-{proc.pid}").exists()    # the nested session removed its own root
    finally:
        sentinel.unlink(missing_ok=True)


def test_bad_two_simultaneous_sessions_leave_each_other_and_a_foreign_root_alone():
    sentinel = SHM_PARENT / f"real_build_sentinel-{os.getpid()}-{uuid.uuid4().hex}"
    foreign = SHM_PARENT / f"pytest-foreign-{uuid.uuid4().hex}"   # another live session's root
    sentinel.write_text("x")
    (foreign / "tmp").mkdir(parents=True)
    try:
        procs = [_nested() for _ in range(2)]        # run at the same time
        outs = [p.communicate(timeout=600)[0] for p in procs]
        assert [p.returncode for p in procs] == [0, 0], [o[-2000:] for o in outs]
        assert all(not (SHM_PARENT / f"pytest-{p.pid}").exists() for p in procs)
        assert sentinel.exists() and (foreign / "tmp").is_dir() and TEST_ROOT.exists()
    finally:
        sentinel.unlink(missing_ok=True)
        shutil.rmtree(foreign, ignore_errors=True)


def test_good_cleanup_removes_created_parent_when_empty(tmp_path):
    parent = tmp_path / "video_engine"
    root = parent / "pytest-1"
    (root / "tmp").mkdir(parents=True)
    cleanup_scratch(root, parent, parent_preexisted=False)
    assert not parent.exists()


def test_bad_cleanup_keeps_preexisting_or_nonempty_parent(tmp_path):
    parent = tmp_path / "video_engine"
    (parent / "pytest-1").mkdir(parents=True)
    cleanup_scratch(parent / "pytest-1", parent, parent_preexisted=True)
    assert parent.exists() and not (parent / "pytest-1").exists()
    (parent / "pytest-2").mkdir()
    (parent / "build_x").mkdir()
    cleanup_scratch(parent / "pytest-2", parent, parent_preexisted=False)
    assert (parent / "build_x").exists()


def test_good_dead_session_roots_are_removed_live_and_foreign_kept(tmp_path):
    parent = tmp_path / "video_engine"
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()                                                    # a session killed before it cleaned up
    (parent / f"pytest-{gone.pid}" / "tmp").mkdir(parents=True)
    (parent / f"pytest-{os.getpid()}").mkdir()                     # a live session
    (parent / "pytest-foreign-x").mkdir()                          # not a pytest-<pid> root
    (parent / "build_x").mkdir()
    remove_dead_session_roots(parent)
    assert sorted(d.name for d in parent.iterdir()) == sorted([f"pytest-{os.getpid()}", "pytest-foreign-x", "build_x"])
