"""macOS path of video_engine.resources (no /proc): sysctl RAM size and the ps snapshot, tested with canned output."""
import os
import subprocess

import pytest

from video_engine import resources

PS = """\
    1     0  12345     2-01:02:03.00 /sbin/launchd
  100     1   2048        0:01.23 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  101   100 204800    12:34.56 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  200     1  51200     1:02:03.00 /opt/homebrew/Cellar/ffmpeg/7.1/bin/ffmpeg
  201   200      0     0:00.00 ps
garbage line
  abc     1      1     0:00.00 broken
  300     1   1024     0:00.10 node
"""


def test_good_cputime_formats():
    assert resources.parse_cputime("0:01.23") == pytest.approx(1.23)
    assert resources.parse_cputime("12:34.56") == pytest.approx(12 * 60 + 34.56)
    assert resources.parse_cputime("1:02:03.00") == pytest.approx(3723.0)
    assert resources.parse_cputime("2-01:02:03") == pytest.approx(2 * 86400 + 3723)
    assert resources.parse_cputime("  0:00.00 ") == 0.0
    assert resources.parse_cputime("1:02,50") == pytest.approx(62.5)      # comma decimal separator


@pytest.mark.parametrize("bad", ["", "x:y", "1-", "1:2:3:x"])
def test_bad_cputime_raises(bad):
    with pytest.raises(ValueError):
        resources.parse_cputime(bad)


def test_good_parse_ps_comm_paths_with_spaces_and_units():
    t = resources.parse_ps(PS)
    assert set(t) == {1, 100, 101, 200, 201, 300}                       # garbage and non-numeric lines skipped
    assert t[1] == (0, "launchd", 2 * 86400 + 3723.0, 12345 * 1024)
    assert t[100][1] == "Google Chrome" and t[100][2] == pytest.approx(1.23) and t[100][3] == 2048 * 1024
    assert t[101][0] == 100 and t[101][1] == "Google Chrome Helper (Renderer)"
    assert t[200][1] == "ffmpeg" and t[200][2] == pytest.approx(3723.0)
    assert t[300][1] == "node"
    assert resources.parse_ps("") == {}


def test_good_kinds_of_parsed_comms():
    t = resources.parse_ps(PS)
    assert [resources._kind(t[p][1]) for p in (100, 101, 200, 300, 1)] == \
        ["hyperframes", "hyperframes", "ffmpeg", "hyperframes", "other"]


@pytest.mark.parametrize("text,gb", [("17179869184\n", 16.0), ("8589934592", 8.0), ("38654705664\n", 36.0),
                                     ("15032385536", 14.0)])
def test_good_parse_memsize(text, gb):
    assert resources.parse_memsize(text) == gb


@pytest.mark.parametrize("text", ["", "abc", "0", "-5"])
def test_bad_parse_memsize(text):
    assert resources.parse_memsize(text) is None


class Done:
    def __init__(self, out): self.stdout = out


def test_good_ram_gb_uses_sysctl_without_proc(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return Done("17179869184\n")
    monkeypatch.setattr(resources, "HAVE_PROC", False)
    monkeypatch.setattr(resources.subprocess, "run", run)
    assert resources.ram_gb() == 16.0
    assert calls == [["sysctl", "-n", "hw.memsize"]]


def test_good_ram_gb_falls_back_to_usr_sbin_then_default(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[0])
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(resources, "HAVE_PROC", False)
    monkeypatch.setattr(resources.subprocess, "run", run)
    assert resources.ram_gb() == 8.0 and seen == ["sysctl", "/usr/sbin/sysctl"]


def test_good_ram_gb_reads_proc_on_linux(monkeypatch):
    if not os.path.exists("/proc/meminfo"):
        pytest.skip("no /proc")
    monkeypatch.setattr(resources, "HAVE_PROC", True)
    monkeypatch.setattr(resources.subprocess, "run", lambda *a, **k: pytest.fail("sysctl on the /proc path"))
    assert resources.ram_gb() > 0


class FakePopen:
    def __init__(self, cmd, **kw):
        FakePopen.cmd, FakePopen.pid = cmd, 201
        FakePopen.n = getattr(FakePopen, "n", 0) + 1

    def communicate(self, timeout=None):
        return PS, ""


def test_good_ps_snapshot_one_call_children_and_self_excluded(monkeypatch):
    FakePopen.n = 0
    monkeypatch.setattr(resources.subprocess, "Popen", FakePopen)
    kids, stats = resources._ps_snapshot()
    assert FakePopen.n == 1 and tuple(FakePopen.cmd) == resources.PS_ARGS
    assert kids == {0: [1], 1: [100, 200, 300], 100: [101]}
    assert 201 not in stats and stats[200] == ("ffmpeg", pytest.approx(3723.0), 51200 * 1024)


def test_bad_ps_missing_or_failing_degrades_to_empty(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ps")
    monkeypatch.setattr(resources.subprocess, "Popen", boom)
    assert resources._ps_snapshot() == ({}, {})

    class Slow(FakePopen):
        def communicate(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired("ps", timeout)
            return "", ""
        def kill(self): pass
    monkeypatch.setattr(resources.subprocess, "Popen", Slow)
    assert resources._ps_snapshot() == ({}, {})


def test_good_monitor_samples_process_tree_from_one_ps_snapshot_per_sample(monkeypatch):
    me, ff, ch = os.getpid(), 900001, 900002
    cpu = {"ff": 1.0}
    n = []

    def snap():
        n.append(1)
        return ({me: [ff], ff: [ch]},
                {me: ("python3", 5.0, 100 << 20), ff: ("ffmpeg", cpu["ff"], 50 << 20), ch: ("helper", 0.5, 10 << 20)})
    monkeypatch.setattr(resources, "HAVE_PROC", False)
    monkeypatch.setattr(resources, "_ps_snapshot", snap)
    monkeypatch.setattr(resources, "SAMPLE_S", 3600)             # only begin()/end() sample
    m = resources.Monitor()
    try:
        m.begin("s")
        cpu["ff"] = 3.5
        row = m.end("s")
    finally:
        m.close()
    assert len(n) == 2                                          # one ps per sample, not per pid
    assert row["peak_rss_mb_by_kind"] == {"engine": 100.0, "ffmpeg": 50.0, "other": 10.0}
    assert row["peak_rss_mb"] == 160.0
    assert row["cpu_sampled_s"]["ffmpeg"] == pytest.approx(2.5)  # growth since the stage began
    assert "hyperframes" not in row["cpu_sampled_s"]


def test_good_monitor_survives_ps_failure(monkeypatch):
    monkeypatch.setattr(resources, "HAVE_PROC", False)
    monkeypatch.setattr(resources, "_ps_snapshot", lambda: ({}, {}))
    monkeypatch.setattr(resources, "SAMPLE_S", 3600)
    m = resources.Monitor()
    try:
        m.begin("s")
        row = m.end("s")
    finally:
        m.close()
    assert row["peak_rss_mb"] == 0 and row["cpu_sampled_s"] == {}


def test_good_linux_path_unchanged_with_proc():
    if not resources.HAVE_PROC:
        pytest.skip("no /proc")
    st = resources._stat(os.getpid())
    assert st is not None and st[2] > 0
    assert os.getpid() in {p for ps in resources._children().values() for p in ps}
