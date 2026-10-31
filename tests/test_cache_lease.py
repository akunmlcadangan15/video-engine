"""Cache leases: a file a build has resolved stays on disk until that build exits, whoever evicts (LRU after a
put, `engine cache --prune`, `clean --cache`, the sound cache prune), across processes; leases of dead processes
are reclaimed; unleased entries are evicted as before; every skip is reported."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from video_engine import build, cache, cache_prune, config
from video_engine.ai.base import AwaitingAgent
from video_engine.sound import cache as sound_cache
from video_engine.util import EngineError

REPO = Path(__file__).resolve().parent.parent

HOLDER = """
import sys, time
from pathlib import Path
from video_engine import cache
from video_engine.sound import cache as sound_cache
d = Path(sys.argv[1])
with cache.session():
    seg = cache.get_file("segments", "held")
    snd = sound_cache.lookup("sfx", "pop", "epidemic")
    assert seg is not None and snd is not None
    (d / "ready").write_text(str(seg))
    while not (d / "go").exists():
        time.sleep(0.02)
    print(seg.read_text(), snd.path.read_text())     # the paused build uses what it resolved
"""

PRUNER = """
import json
from video_engine import cache, cache_prune
lru = cache.prune(max_bytes=0)
print(json.dumps({"lru": lru.__dict__, "cli": cache_prune.prune([])}))
"""

CLI_HOLDER = """
import sys, time
from pathlib import Path
from video_engine import cli, cache
d = Path(sys.argv[1])
def run(a):
    seg = cache.get_file("segments", "held")
    (d / "ready").write_text("1")
    while not (d / "go").exists():
        time.sleep(0.02)
    print(seg.read_text())
    return 0
cli.cmd_doctor = run                 # any engine command: cli.main runs it in a lease session
sys.argv = ["engine", "doctor"]
sys.exit(cli.main())
"""

KILLED = """
import os, signal
from video_engine import cache
with cache.session():
    cache.get_file("segments", "held")
    os.kill(os.getpid(), signal.SIGKILL)
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "cache"
    monkeypatch.setattr(config, "CACHE_DIR", r)
    return r


def env(root):
    return {**os.environ, "VIDEO_ENGINE_CACHE": str(root),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")]))}


def py(code, root, *args, **kw):
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], env=env(root), capture_output=True,
                          text=True, timeout=120, **kw)


def entry(root, kind, name, text="x" * 100, age=0):
    f = root / kind / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    if age:
        t = time.time() - age
        os.utime(f, (t, t))
    return f


def sound(tmp_path, key="pop", body="tone"):
    src = tmp_path / f"{key}.wav"
    src.write_text(body)
    return sound_cache.put("sfx", key, src, {"source": "epidemic", "id": key, "title": "T", "artist": "A",
                                             "credit": sound_cache.credit_line("epidemic", key, "T", "A")}, 200)


def leases(root):
    d = root / cache.LEASES
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


def wait_for(f, proc):
    for _ in range(3000):
        if f.exists():
            return
        assert proc.poll() is None, proc.communicate()
        time.sleep(0.02)
    raise AssertionError(f"{f} never appeared")


# ---------------------------------------------------------------------------------------------------------- good
def test_good_prune_in_another_process_keeps_files_a_paused_build_process_leased(root, tmp_path):
    held = entry(root, "segments", "held.mp4", "segment", age=1000)       # oldest: first in line for LRU
    snd = sound(tmp_path)
    loose = [entry(root, "segments", f"loose{i}.mp4", age=500) for i in range(3)] + [entry(root, "maps", "m.jpg")]
    sync = tmp_path / "sync"
    sync.mkdir()
    a = subprocess.Popen([sys.executable, "-c", HOLDER, str(sync)], env=env(root), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    try:
        wait_for(sync / "ready", a)
        b = py(PRUNER, root)
        assert b.returncode == 0, b.stderr
        rep = json.loads(b.stdout)
        side = snd.path.with_suffix(".json").relative_to(root)
        idx = root / "sound" / "index" / "sfx-pop.json"
        assert "segments/held.mp4" in rep["lru"]["skipped_leased"]
        assert {str(snd.path.relative_to(root)), str(side), str(idx.relative_to(root))} <= set(rep["lru"]["skipped_leased"])
        assert held.exists() and snd.path.exists() and (root / side).exists() and idx.exists()
        assert not any(f.exists() for f in loose)                        # unleased entries are evicted
        assert rep["cli"]["skipped_leased"] == ["segments/held.mp4"] and rep["cli"]["deleted"] == []
        (sync / "go").write_text("1")
        out, err = a.communicate(timeout=60)
        assert a.returncode == 0, err
        assert out.split() == ["segment", "tone"]                        # the paused build read its inputs intact
    finally:
        a.kill()
    assert leases(root) == []                                            # released on exit
    after = cache.prune(max_bytes=0)
    assert "segments/held.mp4" in after.deleted and after.skipped_leased == [] and not held.exists()


def test_good_engine_command_process_keeps_its_resolved_files_against_a_prune_from_another_process(root, tmp_path):
    entry(root, "segments", "held.mp4", "segment")
    a = subprocess.Popen([sys.executable, "-c", CLI_HOLDER, str(tmp_path)], env=env(root), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    try:
        wait_for(tmp_path / "ready", a)
        assert py("from video_engine import cache; cache.prune(max_bytes=0)", root).returncode == 0
        (tmp_path / "go").write_text("1")
        out, err = a.communicate(timeout=60)
    finally:
        a.kill()
    assert a.returncode == 0 and out.strip() == "segment", err
    assert leases(root) == []


def test_good_engine_cache_prune_cli_lists_the_skipped_leased_entries(root, project):
    p, _ = project()
    held = entry(root, "segments", "held.mp4")
    gone = entry(root, "words", "stale.json")
    with cache.session():
        cache.get_file("segments", "held")
        r = subprocess.run([sys.executable, "-m", "video_engine.cli", "cache", "--prune", "--project", str(p)],
                           env=env(root), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["skipped_leased"] == ["segments/held.mp4"] and out["deleted"] == ["words/stale.json"]
    assert out["reclaimed_leases"] == [] and held.exists() and not gone.exists()


def test_good_lease_of_a_killed_process_is_reclaimed_and_its_entry_becomes_prunable(root):
    held = entry(root, "segments", "held.mp4")
    r = py(KILLED, root)
    assert r.returncode == -signal.SIGKILL
    stale = leases(root)
    assert len(stale) == 1 and "segments/held.mp4" in (root / cache.LEASES / stale[0]).read_text()
    res = cache.prune(max_bytes=0)
    assert res.reclaimed_leases == stale and res.deleted == ["segments/held.mp4"] and res.skipped_leased == []
    assert not held.exists() and leases(root) == []


def test_good_lease_of_a_reused_pid_is_reclaimed(root):
    """A live pid whose start time differs from the lease's is a different process: its lease is stale."""
    held = entry(root, "segments", "held.mp4")
    (root / cache.LEASES).mkdir(parents=True)
    pid = os.getppid()
    real = cache.proc_start(pid)
    assert real is not None
    reused = f"{pid}-{int(real) + 1}.json"
    (root / cache.LEASES / reused).write_text(json.dumps({"pid": pid, "start": str(int(real) + 1),
                                                          "paths": ["segments/held.mp4"]}))
    res = cache.prune(max_bytes=0)
    assert res.reclaimed_leases == [reused] and res.deleted == ["segments/held.mp4"] and not held.exists()


def test_good_lease_of_a_live_process_with_its_start_time_is_honoured(root):
    held = entry(root, "segments", "held.mp4")
    (root / cache.LEASES).mkdir(parents=True)
    pid = os.getppid()
    (root / cache.LEASES / f"{pid}-{cache.proc_start(pid)}.json").write_text(
        json.dumps({"pid": pid, "start": cache.proc_start(pid), "paths": ["segments/held.mp4"]}))
    res = cache.prune(max_bytes=0)
    assert res.skipped_leased == ["segments/held.mp4"] and res.reclaimed_leases == [] and held.exists()


def test_good_unleased_entries_are_evicted_lru_first_while_others_are_leased(root):
    held = entry(root, "segments", "held.mp4", age=300)
    first = entry(root, "segments", "first.mp4", age=200)
    second = entry(root, "segments", "second.mp4", age=100)
    with cache.session():
        cache.get_file("segments", "held")                               # the LRU touch makes held the most recent
        res = cache.prune(max_bytes=0)
    assert res.skipped_leased == ["segments/held.mp4"] and res.deleted == ["segments/first.mp4", "segments/second.mp4"]
    assert held.exists() and not first.exists() and not second.exists()


def test_good_put_keeps_an_entry_larger_than_the_budget_and_the_session_reports_the_skips(root, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAX_BYTES", 10)
    used = entry(root, "maps", "used.jpg", age=100)
    src = tmp_path / "seg.mp4"
    src.write_text("y" * 1000)
    with cache.session() as s:
        assert cache.get_file("maps", "used", ".jpg") == used
        out = cache.put_file("segments", "big", src)
        rep = s.report()
    assert out.exists() and out.read_text() == "y" * 1000 and used.exists()
    assert sorted(rep["skipped_leased"]) == [f"{root}/maps/used.jpg", f"{root}/segments/big.mp4"]
    assert rep["evicted"] == [] and rep["leased"] >= 3


def test_good_session_releases_its_leases_when_the_body_raises(root):
    held = entry(root, "segments", "held.mp4")
    with pytest.raises(RuntimeError):
        with cache.session():
            cache.get_file("segments", "held")
            assert len(leases(root)) == 1
            raise RuntimeError("boom")
    assert leases(root) == [] and cache.prune(max_bytes=0).deleted == ["segments/held.mp4"] and not held.exists()


def test_good_nested_sessions_release_at_the_outermost_exit(root):
    entry(root, "segments", "held.mp4")
    with cache.session():
        with cache.session():
            cache.get_file("segments", "held")
        assert len(leases(root)) == 1 and cache.prune(max_bytes=0).skipped_leased == ["segments/held.mp4"]
    assert leases(root) == []


def test_good_clear_keeps_leased_entries_and_reports_them(root):
    held = entry(root, "segments", "held.mp4")
    other = entry(root, "segments", "other.mp4")
    with cache.session():
        cache.get_file("segments", "held")
        r = cache.clear()
    assert r == {"deleted": ["segments/other.mp4"], "skipped_leased": ["segments/held.mp4"], "reclaimed_leases": []}
    assert held.exists() and not other.exists()


def test_good_sound_prune_skips_leased_temp_files_of_a_running_writer(root, tmp_path):
    a = sound(tmp_path)
    orphan = a.path.parent / "x.wav.1.2.tmp"
    orphan.write_text("x")
    writing = a.path.parent / "y.wav.3.4.tmp"
    writing.write_text("y")
    with cache.session():
        cache.acquire(writing)
        r = sound_cache.prune(cache_dir=root)
    assert r["deleted"] == ["files/x.wav.1.2.tmp"] and r["skipped_leased"] == ["files/y.wav.3.4.tmp"]
    assert writing.exists() and not orphan.exists()


@pytest.fixture
def stub_preflight(monkeypatch):
    class Rep:
        ok, timings, subtimings, fixes, ai, caption_y_overrides, findings = True, {}, {}, [], None, [], []

        def summary(self):
            return "preflight: OK (stub)"
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: Rep())


def test_good_build_succeeds_with_a_cache_budget_smaller_than_its_outputs(root, project, stub_preflight, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAX_BYTES", 1)
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    r = build.build(p)
    assert r.ok and r.final.is_file() and r.cache["skipped_leased"] and r.cache["leased"]
    assert json.loads(r.report.read_text())["cache"] == r.cache
    assert leases(root) == []
    assert cache.prune(max_bytes=0).skipped_leased == []                 # everything is prunable after the build


@pytest.mark.parametrize("exc", [EngineError("injected mixer error"), AwaitingAgent(Path("/x"), "resume")],
                         ids=["failure", "awaiting-agent"])
def test_good_build_releases_its_leases_on_failure_and_awaiting_agent(root, project, stub_preflight, monkeypatch, exc):
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    entry(root, "segments", "held.mp4")
    seen = []

    def plan(*a, **k):
        cache.get_file("segments", "held")
        seen.append(leases(root))
        raise exc
    monkeypatch.setattr(build.mixer, "plan", plan)
    with pytest.raises(type(exc)):
        build.build(p)
    assert len(seen[0]) == 1 and leases(root) == []
    assert cache.prune(max_bytes=0).deleted == ["segments/held.mp4"]


# ----------------------------------------------------------------------------------------------------------- bad
def test_bad_unleased_paused_reader_loses_its_file_to_eviction(root, tmp_path):
    """Outside a session nothing is leased: what keeps a build's inputs is the lease, not luck."""
    held = entry(root, "segments", "held.mp4")
    assert cache.get_file("segments", "held") == held
    assert py(PRUNER, root).returncode == 0
    assert not held.exists() and cache.get_file("segments", "held") is None


def test_bad_lease_file_with_garbage_name_or_body_protects_nothing(root):
    held = entry(root, "segments", "held.mp4")
    d = root / cache.LEASES
    d.mkdir(parents=True)
    (d / "nonsense.json").write_text("{}")
    pid = os.getppid()
    (d / f"{pid}-{cache.proc_start(pid)}.json").write_text("not json")
    res = cache.prune(max_bytes=0)
    assert res.deleted == ["segments/held.mp4"] and res.skipped_leased == [] and not held.exists()


def test_bad_acquire_outside_the_cache_root_or_outside_a_session_writes_no_lease(root, tmp_path):
    cache.acquire(root / "segments" / "a.mp4")
    with cache.session():
        cache.acquire(tmp_path / "elsewhere.mp4")
    assert leases(root) == []


def test_good_put_file_defers_the_cache_scan_until_the_configured_interval(root, tmp_path, monkeypatch):
    """put_file no longer scans the whole cache on every store: with a budget it does not exceed, housekeeping waits
    for PRUNE_EVERY_PUTS stores (or PRUNE_INTERVAL_S)."""
    monkeypatch.setattr(config, "CACHE_MAX_BYTES", 50)
    monkeypatch.setattr(cache, "PRUNE_EVERY_PUTS", 3)
    stale = entry(root, "words", "stale.json")
    src = tmp_path / "seg.mp4"
    src.write_text("x" * 10)
    cache.put_file("segments", "a", src)
    cache.put_file("segments", "b", src)
    assert stale.exists()                                                # two stores: no full scan yet
    cache.put_file("segments", "c", src)
    assert not stale.exists()                                            # third store: housekeeping ran


def test_good_put_file_prunes_at_once_when_the_entry_alone_exceeds_the_budget(root, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAX_BYTES", 10)
    stale = entry(root, "words", "stale.json", age=100)
    src = tmp_path / "big.mp4"
    src.write_text("x" * 1000)
    out = cache.put_file("segments", "big", src)
    assert out.exists() and not stale.exists()                           # oversized store triggers the LRU scan
