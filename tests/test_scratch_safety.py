"""Scratch dirs: project ids cannot name SHM_ROOT or anything outside it, every invocation gets its own dir under
SHM_ROOT, a build removes only the dir it created, and timeline writes use writer-unique temp files."""
import os
import threading

import pytest

from video_engine import build, cli, config, timeline
from video_engine.gates import runner
from video_engine.util import EngineError


@pytest.fixture
def shm(tmp_path, monkeypatch):
    root = tmp_path / "video_engine"
    root.mkdir()
    monkeypatch.setattr(config, "SHM_ROOT", root)
    return root


DOT_IDS = [".", "..", "...", ".hidden"]
PATH_IDS = ["a/b", "../x", "/abs", "a\\b", "", "-x"]


# ------------------------------------------------------------------------------------------------- project ids
@pytest.mark.parametrize("pid", ["demo_surabaya", "geoverum-short", "a.b", "_x", "7"])
def test_good_project_id_names_a_dir_inside_shm_root(shm, pid):
    d = config.workdir(pid)
    assert d.is_dir() and d.parent == shm.resolve() and d.name.startswith(f"{pid}-{os.getpid()}-")


@pytest.mark.parametrize("pid", DOT_IDS + PATH_IDS)
def test_bad_dot_and_path_like_project_ids_are_rejected(shm, project, pid):
    with pytest.raises(ValueError):
        config.workdir(pid)
    with pytest.raises(EngineError):
        project(lambda d: d.update(project_id=pid))
    assert list(shm.iterdir()) == []


def test_good_safe_project_id_from_dot_dir_names():
    for name in [".", "..", "...", ".x", "a b", "-q"]:
        assert config.check_project_id(config.safe_project_id(name))


# ------------------------------------------------------------------------------------------ strict descendant
def test_good_work_dir_shm_inside_root_gets_a_fresh_dir_per_build(shm, project):
    _, tl = project(lambda d: d["settings"].update(work_dir_shm=str(shm / "jobs")))
    a, b = build.project_workdir(tl), build.project_workdir(tl)
    assert a != b and a.parent == b.parent == (shm / "jobs").resolve()


def test_bad_work_dir_shm_sibling_prefix_is_rejected(shm, project):
    sibling = shm.parent / (shm.name + "_other")          # /dev/shm/video_engine_other next to /dev/shm/video_engine
    sibling.mkdir()
    _, tl = project(lambda d: d["settings"].update(work_dir_shm=str(sibling / "x")))
    with pytest.raises(EngineError, match="not inside"):
        build.project_workdir(tl)
    assert list(sibling.iterdir()) == []


@pytest.mark.parametrize("rel", ["", ".", "x/..", "../video_engine"])
def test_bad_work_dir_shm_equal_to_root_is_rejected(shm, project, rel):
    _, tl = project(lambda d: d["settings"].update(work_dir_shm=str(shm / rel) if rel else str(shm)))
    with pytest.raises(EngineError, match="not inside"):
        build.project_workdir(tl)


def test_bad_work_dir_shm_symlink_escape_is_rejected(shm, project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    (shm / "link").symlink_to(outside, target_is_directory=True)
    _, tl = project(lambda d: d["settings"].update(work_dir_shm=str(shm / "link" / "sub")))
    with pytest.raises(EngineError, match="not inside"):
        build.project_workdir(tl)
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]


def test_good_inside_shm_follows_symlinks_that_stay_inside(shm):
    (shm / "real").mkdir()
    (shm / "alias").symlink_to(shm / "real", target_is_directory=True)
    assert config.inside_shm(shm / "alias") == (shm / "real").resolve()


# ---------------------------------------------------------------------- build removes only the dir it created
def _fake_stages(monkeypatch, on_preflight):
    monkeypatch.setattr(build.audio, "captions_for", lambda *a, **k: ([], "none"))
    def pre(tl, project_dir, words, workdir=None, ai_qa=None):
        on_preflight(workdir)
        raise EngineError("stop after preflight")
    monkeypatch.setattr(build, "run_preflight", pre)


def test_good_two_concurrent_same_id_builds_keep_each_others_files(shm, project, monkeypatch):
    p, _ = project()
    gate, first_done = threading.Barrier(2), threading.Event()
    seen, survived, errors = {}, {}, []

    def on_preflight(wd):
        name = threading.current_thread().name
        (wd / "mine.txt").write_text(name)
        seen[name] = wd
        gate.wait(timeout=30)                 # both builds hold their scratch at the same time
        if name == "b":
            first_done.wait(timeout=30)       # a's build has run its cleanup
            survived["b"] = (wd / "mine.txt").read_text() if (wd / "mine.txt").exists() else None

    _fake_stages(monkeypatch, on_preflight)

    def job(name):
        try:
            build.build(p)
        except EngineError as e:
            errors.append(str(e))
        finally:
            if name == "a":
                first_done.set()

    ts = [threading.Thread(target=job, args=(n,), name=n) for n in "ab"]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert len(errors) == 2 and all("stop after preflight" in e for e in errors)
    assert seen["a"] != seen["b"] and seen["a"].parent == seen["b"].parent == shm.resolve()
    assert survived == {"b": "b"}                              # a's cleanup left b's file in place
    assert list(shm.iterdir()) == []                           # each build removed its own dir


def test_bad_build_leaves_other_scratch_under_root_and_work_dir_shm(shm, project, monkeypatch):
    jobs = shm / "jobs"
    (jobs / "pytest_proj").mkdir(parents=True)                 # same-id dir of another invocation
    (jobs / "pytest_proj" / "theirs.txt").write_text("x")
    (shm / "pytest_proj").mkdir()
    (shm / "pytest_proj" / "theirs.txt").write_text("x")
    p, _ = project(lambda d: d["settings"].update(work_dir_shm=str(jobs)))
    _fake_stages(monkeypatch, lambda wd: (wd / "mine.txt").write_text("m"))
    with pytest.raises(EngineError, match="stop after preflight"):
        build.build(p)
    assert (jobs / "pytest_proj" / "theirs.txt").exists() and (shm / "pytest_proj" / "theirs.txt").exists()
    assert sorted(x.name for x in jobs.iterdir()) == ["pytest_proj"]


def test_good_preflight_without_workdir_removes_only_its_own_dir(shm, project):
    p, tl = project()
    (shm / "pytest_proj").mkdir()
    (shm / "pytest_proj" / "theirs.txt").write_text("x")
    runner.run_preflight(tl, p, None, dom=False, snapshots=False)
    assert sorted(x.name for x in shm.iterdir()) == ["pytest_proj"]
    assert (shm / "pytest_proj" / "theirs.txt").exists()


def test_bad_cli_preflight_keeps_a_concurrent_same_id_scratch(shm, project, monkeypatch):
    p, tl = project()
    other = config.workdir(tl.project_id)                      # a concurrent invocation's scratch
    (other / "theirs.txt").write_text("x")
    made = []
    monkeypatch.setattr(runner, "_run", lambda tl, pd, w, wd, *a: made.append(wd) or runner.Report())
    assert cli.main(["preflight", str(p), "--no-dom"]) == 0
    assert made and made[0] != other and not made[0].exists()   # cli's own scratch: created, then removed
    assert (other / "theirs.txt").exists()
    assert list(shm.iterdir()) == [other]


# --------------------------------------------------------------------------------------------- timeline writes
def test_good_concurrent_timeline_saves_leave_one_whole_file(project, tmp_path):
    _, tl = project()
    a, b = tl.model_copy(deep=True), tl.model_copy(deep=True)
    a.settings.topic, b.settings.topic = "A" * 50, "B" * 5000
    dst = tmp_path / "shared.json"
    errors = []

    def writer(t):
        try:
            for _ in range(40):
                timeline.save(t, dst)
        except Exception as e:          # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=writer, args=(t,)) for t in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert errors == []
    assert timeline.load(dst).settings.topic in ("A" * 50, "B" * 5000)
    assert [x.name for x in tmp_path.iterdir() if x.name != "proj"] == ["shared.json"]


def test_bad_failed_timeline_save_keeps_the_file_and_leaves_no_temp(project, tmp_path, monkeypatch):
    _, tl = project()
    dst = tmp_path / "t.json"
    timeline.save(tl, dst)
    before = dst.read_text()
    monkeypatch.setattr(timeline.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        timeline.save(tl, dst)
    assert dst.read_text() == before
    assert sorted(x.name for x in tmp_path.iterdir() if x.name != "proj") == ["t.json"]


# ------------------------------------------------------------------------------------ clean removes only its scratch
def test_good_clean_removes_only_the_given_projects_scratch(shm, project):
    p, tl = project()
    mine = config.workdir(tl.project_id)
    (mine / "mine.txt").write_text("m")
    other = config.workdir("another_project")
    (other / "theirs.txt").write_text("t")
    assert cli.main(["clean", str(p)]) == 0
    assert not mine.exists() and other.exists() and shm.exists()
    assert (other / "theirs.txt").read_text() == "t"


def test_bad_clean_without_a_project_keeps_all_scratch(shm):
    kept = config.workdir("keepme")
    (kept / "x.txt").write_text("x")
    assert cli.main(["clean"]) == 0
    assert kept.exists() and shm.exists()
