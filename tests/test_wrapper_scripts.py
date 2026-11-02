"""scripts/engine-build and scripts/engine-batch: lock, disk threshold, env file, exit codes (with a stub `engine`)."""
import fcntl
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(shutil.which("sh") is None or shutil.which("nice") is None, reason="needs sh and nice")


@pytest.fixture
def box(tmp_path):
    """A stub `engine` on PATH that records its argv and exits with $STUB_EXIT, and a project with out/."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "engine"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB_LOG"\nprintf "%s\\n" "${STUB_MARK:-}" >> "$STUB_LOG"\nexit "${STUB_EXIT:-0}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    proj = tmp_path / "proj"
    (proj / "out").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIDEO_ENGINE_")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", STUB_LOG=str(tmp_path / "stub.log"),
               VIDEO_ENGINE_RENDER_LOCK=str(tmp_path / "render.lock"))

    def run(script, *args, **extra):
        e = {**env, **{k: str(v) for k, v in extra.items()}}
        return subprocess.run([str(SCRIPTS / script), *args], env=e, cwd=tmp_path, capture_output=True, text=True, timeout=60)

    run.proj, run.tmp, run.log = proj, tmp_path, tmp_path / "stub.log"
    return run


def test_scripts_are_executable():
    for name in ("engine-build", "engine-batch"):
        assert os.access(SCRIPTS / name, os.X_OK)


def test_build_runs_engine_build_with_args(box):
    r = box("engine-build", str(box.proj), "--mode", "draft")
    assert r.returncode == 0, r.stderr
    assert box.log.read_text().split() == ["build", str(box.proj), "--mode", "draft"]


def test_batch_runs_engine_batch(box):
    r = box("engine-batch", "--queue", "q.txt", "--fail-fast")
    assert r.returncode == 0, r.stderr
    assert box.log.read_text().split()[:3] == ["batch", "--queue", "q.txt"]


@pytest.mark.parametrize("code", [1, 2, 3, 42])
def test_exit_code_passes_through(box, code):
    assert box("engine-build", str(box.proj), STUB_EXIT=code).returncode == code


def test_lock_busy_refuses_with_message(box):
    fd = os.open(box.tmp / "render.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = box("engine-build", str(box.proj))
    finally:
        os.close(fd)
    assert r.returncode == 75
    assert "another render holds the lock" in r.stderr and "render.lock" in r.stderr
    assert not box.log.exists()
    assert box("engine-build", str(box.proj)).returncode == 0          # released: runs again


def test_lock_is_held_while_engine_runs(box):
    probe = ("import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDWR)\n"
             "try:\n    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept OSError:\n    print('held')\n")
    (box.tmp / "probe.py").write_text(probe)
    stub = box.tmp / "bin" / "engine"
    stub.write_text(f'#!/bin/sh\n"{sys.executable}" "{box.tmp / "probe.py"}" "$VIDEO_ENGINE_RENDER_LOCK" > "$STUB_LOG"\n')
    assert box("engine-build", str(box.proj)).returncode == 0
    assert box.log.read_text().strip() == "held"


def test_low_disk_refuses(box):
    r = box("engine-build", str(box.proj), VIDEO_ENGINE_MIN_FREE_GB=999999999)
    assert r.returncode == 74
    assert "refusing to build" in r.stderr and "VIDEO_ENGINE_MIN_FREE_GB" in r.stderr
    assert not box.log.exists()


def test_threshold_zero_disables_check_and_junk_is_rejected(box):
    assert box("engine-build", str(box.proj), VIDEO_ENGINE_MIN_FREE_GB=0).returncode == 0
    r = box("engine-build", str(box.proj), VIDEO_ENGINE_MIN_FREE_GB="eight")
    assert r.returncode == 2 and "must be a number" in r.stderr


def test_env_file_is_loaded(box):
    envf = box.tmp / "my.env"
    envf.write_text("STUB_MARK=from-env-file\nVIDEO_ENGINE_MIN_FREE_GB=0\n")
    assert box("engine-build", str(box.proj), VIDEO_ENGINE_ENV_FILE=envf).returncode == 0
    assert box.log.read_text().split()[-1] == "from-env-file"
    (box.tmp / "engine.env").write_text("STUB_MARK=from-cwd\n")                # ./engine.env is picked up too
    assert box("engine-build", str(box.proj)).returncode == 0
    assert box.log.read_text().split()[-1] == "from-cwd"


def test_missing_env_file_is_an_error(box):
    r = box("engine-build", str(box.proj), VIDEO_ENGINE_ENV_FILE=box.tmp / "nope.env")
    assert r.returncode == 2 and "does not exist" in r.stderr


def test_missing_engine_reports_127(box):
    (box.tmp / "bin" / "engine").unlink()
    r = box("engine-build", str(box.proj), PATH="/usr/bin:/bin")
    assert r.returncode == 127 and "not on PATH" in r.stderr
