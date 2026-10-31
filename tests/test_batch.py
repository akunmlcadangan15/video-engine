"""Batch runner: queue loading, per-project lock, order, fail-fast and CLI wiring."""
import json
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
