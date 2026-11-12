"""Build publishing: the output and build_report.json change only through os.replace after every gate passed; a
failed build keeps them byte-identical, writes build_report.failed.json and reports failure (raise or return)."""
import json
import os
from pathlib import Path

import pytest

from video_engine import build, cli, gate_tools
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
    """Staging dirs and temp files the build left in the output dir. `.batch.lock` is the project lock file that
    `engine build` takes (batch.ProjectLock); it stays on purpose, because unlinking a flock file races."""
    return sorted(x.name for x in (p / "out").iterdir() if x.name.startswith(".") and x.name != ".batch.lock")


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


def test_bad_report_write_failure_keeps_the_previous_output_byte_identical(proj, monkeypatch):
    """The report is staged before the video is replaced: a failing report write (e.g. disk full) publishes nothing."""
    good = _previous(proj)
    real = build.stage_json

    def stage(path, data):
        if Path(path).name == "build_report.json":
            raise OSError(28, "injected: No space left on device")
        return real(path, data)
    monkeypatch.setattr(build, "stage_json", stage)
    with pytest.raises(OSError, match="No space left"):
        build.build(proj)
    assert _published(proj) == good and _leftovers(proj) == []
    rep = json.loads((proj / "out" / FAILED).read_text())
    assert rep["ok"] is False and rep["final"] is None and "No space left" in rep["error"]


# --------------------------------------------------------------------------------------------- not_checked
SAMPLED = {"gate": None, "seg": None, "kind": "sampled",
           "why": f"pixel QA (R38, R38space, R56px) looked at every {build.qa.SAMPLE_STEP}th frame of the final file, "
                  f"at 1/{build.qa.SNAP_DIV} size"}


@pytest.fixture
def native_proj(project, monkeypatch):
    """The stubbed-pre-flight project without its HyperFrames segment (renders without the HyperFrames toolchain)."""
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    p, _ = project(lambda d: (d["segments"].pop(),
                              d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False})))
    return p


def test_good_fully_checked_build_reports_only_the_sampling(native_proj, monkeypatch):
    """Every gate switched on, AI QA requested, the audio measured afresh and no skip: the one thing the report
    still names as not checked is that pixel QA looks at sampled frames."""
    monkeypatch.setattr(gate_tools, "is_active", lambda g, p: True)
    r = build.build(native_proj, ai_qa=object(), use_cache=False)
    assert json.loads(r.report.read_text())["not_checked"] == [SAMPLED]


def test_good_audio_cache_hit_says_the_loudness_was_not_measured_again(native_proj):
    build.build(native_proj)
    nc = json.loads(build.build(native_proj).report.read_text())["not_checked"]
    assert [e["gate"] for e in nc if e["kind"] == "cached"] == ["A01"] and nc[-1] == SAMPLED
    assert {"gate": "AIQA", "seg": None, "kind": "off", "why": "AI visual QA not requested (--ai-qa)"} in nc


def test_bad_stage_error_report_lists_the_stages_that_did_not_run(native_proj, monkeypatch):
    _mux_error(monkeypatch)
    with pytest.raises(EngineError, match="injected mux error"):
        build.build(native_proj)
    nc = json.loads((native_proj / "out" / FAILED).read_text())["not_checked"]
    assert [e["why"] for e in nc if e["kind"] == "not_run" and e["gate"] is None] == [
        f"stage qa did not run: the build stopped with EngineError ({build.POST_GATES} not judged)"]
    assert any(e["gate"] == "AIQA" and e["kind"] == "off" for e in nc) and SAMPLED not in nc
