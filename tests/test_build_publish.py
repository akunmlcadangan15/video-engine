"""Build publishing: the output and build_report.json change only through os.replace after every gate passed; a
failed build keeps them byte-identical, writes build_report.failed.json and reports failure (raise or return)."""
import json
import os
from pathlib import Path

import pytest

from video_engine import build, cli
from video_engine.gates.core import Finding
from video_engine.util import EngineError

FAILED = "build_report.failed.json"


class _Rep:
    """Stand-in pre-flight report: the pre-flight gates are tested elsewhere."""
    ok, timings, subtimings, fixes, ai, caption_y_overrides, findings = True, {}, {}, [], None, [], []

    def summary(self):
        return "preflight: OK (stub)"


@pytest.fixture
def proj(project, monkeypatch):
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    return p


def _previous(p):
    """A published good build, marked so that any rewrite (even an identical render) changes its bytes."""
    build.build(p)
    out = p / "out"
    with open(out / "final.mp4", "ab") as f:
        f.write(b"previous")
    rep = json.loads((out / "build_report.json").read_text())
    (out / "build_report.json").write_text(json.dumps({**rep, "marker": "previous"}))
    return _published(p)


def _published(p):
    return {n: (p / "out" / n).read_bytes() for n in ("final.mp4", "build_report.json")}


def _leftovers(p):
    """Staging dirs and temp files the build left in the output dir."""
    return sorted(x.name for x in (p / "out").iterdir() if x.name.startswith("."))


def _pixel_fail(monkeypatch):
    real = build.qa.pixel_gates

    def gates(video, tl, *a, **k):
        fs, info = real(video, tl, *a, **k)
        return fs + [Finding("R38", "fail", "injected black frame", 0)], info
    monkeypatch.setattr(build.qa, "pixel_gates", gates)


def _frame_count_fail(monkeypatch):
    real = build.count_frames
    monkeypatch.setattr(build, "count_frames", lambda f: real(f) - (Path(f).name == "final.mp4"))


def _loudness_fail(monkeypatch):
    """A01 fails whether the audio stage runs or is a cache hit (both judge the measurement by loudness_findings)."""
    monkeypatch.setattr(build.audio, "loudness_findings", lambda m, *a: [Finding("A01", "fail", "injected -20.0 LUFS")])


def _mux_error(monkeypatch):
    real = build.audio.mux

    def mux(*a, **k):
        real(*a, **k)                                  # the staged file exists when the error happens
        raise EngineError("injected mux error")
    monkeypatch.setattr(build.audio, "mux", mux)


# ---------------------------------------------------------------------------------------------------------- good
def test_good_clean_build_reports_success_and_publishes(proj):
    r = build.build(proj)
    out = proj / "out"
    assert r.ok and r.failed == [] and r.final == out / "final.mp4" and r.report == out / "build_report.json"
    rep = json.loads(r.report.read_text())
    assert rep["ok"] is True and rep["final"] == str(out / "final.mp4") and rep["frames"] == r.expected_frames
    assert not (out / FAILED).exists() and _leftovers(proj) == []


def test_good_passing_build_replaces_the_output_atomically_and_leaves_no_temp_files(proj, monkeypatch):
    build.build(proj)
    out = proj / "out"
    (out / FAILED).write_text("{}")                    # stale report of an earlier failed build
    before = (out / "final.mp4").stat().st_ino
    real, moves = os.replace, []
    monkeypatch.setattr(build.os, "replace", lambda a, b: (moves.append((Path(a), Path(b))), real(a, b))[1])
    assert cli.main(["build", str(proj)]) == 0
    staged = [a for a, b in moves if b == out / "final.mp4"]
    assert len(staged) == 1 and staged[0].parent.parent == out     # renamed from a staging dir in out/
    assert (out / "final.mp4").stat().st_ino != before             # a different inode took the name in one rename
    assert any(b == out / "build_report.json" and a.parent == out for a, b in moves)
    assert not (out / FAILED).exists() and _leftovers(proj) == []


# ----------------------------------------------------------------------------------------------------------- bad
def test_bad_pixel_gate_fail_keeps_the_previous_output_and_raises(proj, monkeypatch):
    good = _previous(proj)
    _pixel_fail(monkeypatch)
    with pytest.raises(EngineError, match="gate R38 failed") as e:
        build.build(proj)
    assert isinstance(e.value, build.BuildFailed) and not e.value.result.ok and e.value.result.final is None
    assert _published(proj) == good and _leftovers(proj) == []
    rep = json.loads((proj / "out" / FAILED).read_text())
    assert rep["ok"] is False and rep["final"] is None and "injected black frame" in rep["error"]
    assert any(q.startswith("[FAIL] R38") for q in rep["qa"])


def test_bad_pixel_gate_fail_is_a_failed_result_on_the_return_path_and_cli_exit_1(proj, monkeypatch, capsys):
    _pixel_fail(monkeypatch)
    r = build.build(proj, raise_on_fail=False)
    assert not r.ok and r.final is None and any("R38" in f for f in r.failed)
    assert r.report == proj / "out" / FAILED and not (proj / "out" / "final.mp4").exists()
    assert cli.main(["build", str(proj)]) == 1
    assert FAILED in capsys.readouterr().err and _leftovers(proj) == []


@pytest.mark.parametrize("inject, error", [(_frame_count_fail, "frames, expected"), (_loudness_fail, "gate A01 failed"),
                                           (_mux_error, "injected mux error")])
def test_bad_stage_or_gate_failure_keeps_the_previous_output_byte_identical(proj, monkeypatch, inject, error):
    good = _previous(proj)
    inject(monkeypatch)
    with pytest.raises(EngineError, match=error):
        build.build(proj)
    assert _published(proj) == good and _leftovers(proj) == []
    rep = json.loads((proj / "out" / FAILED).read_text())
    assert rep["ok"] is False and rep["final"] is None and error in rep["error"]
