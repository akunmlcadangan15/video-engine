"""Gate relief: `gates.waive_with_reason`, build `--mode draft`, `--repin` and the W01 refresh hint."""
import json
import os
import shlex
from pathlib import Path

import pytest

from video_engine import audio, build, cli, config, deliver, gate_registry, timeline
from video_engine.gates import layer1, severity
from video_engine.gates.core import Finding
from video_engine.util import EngineError

from test_build_publish import _previous, _published, _Rep
from test_gate_severity import two_maps
from test_words_pin import RAW, vo_project

def need_toolchain():
    """`cli.main` turns a missing vendored Node into exit code 1 (no skip), so these tests check for it first."""
    if not (config.NODE.is_file() and config.CHROME.is_file()) and os.environ.get("VIDEO_ENGINE_REQUIRE_TOOLCHAIN") != "1":
        pytest.skip("vendored toolchain not installed; run `engine doctor --install`")


REASON = "the title card sits in zone 1 on purpose for this reveal"


@pytest.fixture
def proj(project, monkeypatch):
    """A project whose pre-flight is a stub (the pre-flight gates are tested elsewhere), like test_build_publish."""
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    return p


@pytest.fixture
def fake_stt(monkeypatch):
    """Speech-to-text returns the canned words of test_words_pin."""
    monkeypatch.setattr(audio, "transcribe_words",
                        lambda path, lang="id", model=audio.STT_MODEL, keyterms=(), use_cache=True:
                        ([dict(w) for w in RAW], use_cache))


def waiver(*gates, reason=REASON):
    return {"gates": {"waive_with_reason": [{"gate": g, "reason": reason} for g in gates]}}


# --------------------------------------------------------------------------------------- waive_with_reason
def test_good_waive_with_reason_lowers_a_waivable_technical_gate_and_keeps_the_reason(project):
    _, tl = project(two_maps(**waiver("L01")))
    fs = severity.apply([Finding("L01", "fail", "text boxes overlap", 0)], tl)
    assert [f.level for f in fs] == ["info"]
    assert fs[0].waived.startswith("fail: waived by the project with a reason") and REASON in fs[0].waived
    rows = severity.summary(fs, tl)
    assert rows["waivers"] == [{"gate": "L01", "reason": REASON}] and rows["fails"] == []
    assert rows["waived"][0]["gate"] == "L01" and REASON in rows["waived"][0]["waived"]


def test_good_waive_with_reason_also_lowers_a_warning_and_ignores_other_gates(project):
    _, tl = project(two_maps(**waiver("O01", "L06")))
    fs = severity.apply([Finding("O01", "warn", "w"), Finding("L06", "fail", "f"), Finding("L01", "fail", "kept")], tl)
    assert [(f.gate, f.level) for f in fs] == [("O01", "info"), ("L06", "info"), ("L01", "fail")]


def test_good_no_waivers_keep_the_report_shape(project):
    _, tl = project(two_maps())
    assert "waivers" not in severity.summary([], tl)


@pytest.mark.parametrize("gate", ["R63", "T02", "R07", "A01", "M02", "S01", "H01", "NOPE"])
def test_bad_waive_with_reason_rejects_gates_that_are_not_waivable(project, gate):
    with pytest.raises(EngineError, match="cannot be waived"):
        project(two_maps(**waiver(gate)))


@pytest.mark.parametrize("reason", ["", "   ", "too short"])
def test_bad_waive_with_reason_needs_a_real_reason(project, reason):
    with pytest.raises(EngineError, match="must say why"):
        project(two_maps(**waiver("L01", reason=reason)))


def test_bad_waive_with_reason_rejects_duplicates_and_unknown_keys(project):
    with pytest.raises(EngineError, match="more than once"):
        project(two_maps(**waiver("L01", "L01")))
    with pytest.raises(EngineError):
        project(two_maps(gates={"waive_with_reason": [{"gate": "L01", "reason": REASON, "extra": 1}]}))


def test_good_plain_waive_of_a_waivable_gate_is_ignored_with_a_hint(project):
    _, tl = project(two_maps(gates={"waive": ["L01"]}))
    fs = severity.apply([Finding("L01", "fail", "overlap")], tl)
    assert [f.level for f in fs if f.gate == "L01"] == ["fail"]
    gw = [f for f in fs if f.gate == "GW01"]
    assert len(gw) == 1 and "gates.waive_with_reason" in gw[0].message


def test_good_the_registry_marks_exactly_the_three_waivable_gates():
    assert gate_registry.WAIVABLE_WITH_REASON == ("O01", "L01", "L06")
    assert not set(gate_registry.WAIVABLE_WITH_REASON) & set(gate_registry.NEVER_ADVISORY)


# ------------------------------------------------------------------------------------------- draft severity
def test_good_draft_lowers_technical_fails_that_a_profile_could_make_advisory(project):
    _, tl = project(two_maps())
    fs = [Finding(g, "fail", "x") for g in ("R63", "M02", "Z01", "L02")]
    out = {f.gate: f for f in severity.apply(fs, tl, draft=True)}
    assert {f.level for f in out.values()} == {"warn"}
    assert all("draft" in out[g].waived for g in ("R63", "M02", "Z01"))
    assert "draft" not in out["L02"].waived                   # advisory in geoverum.toml: lowered in every build
    assert {f.level for f in severity.apply([Finding(g, "fail", "x") for g in ("R63", "M02")], tl)} == {"fail"}


def test_bad_draft_never_lowers_the_gates_no_profile_may_lower(project):
    _, tl = project(two_maps())
    never = [Finding(g, "fail", "x") for g in gate_registry.NEVER_ADVISORY]
    assert {f.level for f in severity.apply(never, tl, draft=True)} == {"fail"}


def test_bad_draft_keeps_required_style_gates_blocking(project):
    _, tl = project(two_maps())
    out = severity.apply([Finding("S01", "fail", "gap"), Finding("N01", "fail", "nothing new")], tl, draft=True)
    assert [(f.gate, f.level) for f in out] == [("S01", "fail"), ("N01", "warn")]


# ------------------------------------------------------------------------------------------------ W01 hint
def test_good_w01_hint_names_the_exact_command_and_says_when_nothing_is_transcribed_again(project, fake_stt):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    d = json.loads((p / "master_timeline.json").read_text())
    d["settings"]["fps"] = 25
    d["audio_chain"]["stt"] = {"model": "other-model"}
    (p / "master_timeline.json").write_text(json.dumps(d))
    msgs = {f.message for f in layer1.w01_pinned_words(timeline.load(p), p) if f.level == "fail"}
    cmd = f"`engine words {shlex.quote(str(p))}`"
    fps = next(m for m in msgs if "settings.fps" in m)
    stt = next(m for m in msgs if "stt.model" in m)
    assert cmd in fps and "--repin" in fps and "nothing is transcribed again" in fps
    assert cmd in stt and "--repin" in stt and "nothing is transcribed again" not in stt
    assert layer1.pins_stale(timeline.load(p), p)


def test_good_pins_stale_is_false_for_current_hand_written_and_missing_pins(project, fake_stt):
    p, _ = vo_project(project)
    assert not layer1.pins_stale(timeline.load(p), p)             # nothing pinned yet
    audio.pin_captions(p)
    assert not layer1.pins_stale(timeline.load(p), p)             # pinned and current
    p2, tl2 = project(lambda d: d.update(captions=[{"word": "Halo", "start_frame": 0, "end_frame": 10}]))
    assert not layer1.pins_stale(tl2, p2)


# --------------------------------------------------------------------------------------------- build modes
def _inject(monkeypatch, gate, level="fail"):
    real = build.qa.pixel_gates

    def gates(video, tl, *a, **k):
        fs, info = real(video, tl, *a, **k)
        return fs + [Finding(gate, level, f"injected {gate}", 0)], info
    monkeypatch.setattr(build.qa, "pixel_gates", gates)


def test_good_release_build_still_fails_on_a_technical_gate(proj, monkeypatch):
    _inject(monkeypatch, "R62")
    r = build.build(proj, raise_on_fail=False)
    assert not r.ok and r.mode == "release" and not (proj / "out" / "final.mp4").exists()


def test_good_draft_build_lowers_the_gate_writes_to_out_draft_and_leaves_the_release_output(proj, monkeypatch):
    good = _previous(proj)
    _inject(monkeypatch, "R62")
    r = build.build(proj, mode="draft")
    d = proj / "out" / "draft"
    assert r.ok and r.mode == "draft" and r.final == d / "final.draft.mp4" and r.final.is_file()
    assert r.report == d / "build_report.json" and (d / "build_manifest.json").is_file()
    rep = json.loads(r.report.read_text())
    assert rep["mode"] == "draft" and rep["ok"] is True
    assert [w["gate"] for w in rep["gates"]["waived"]] == ["R62"] and "draft" in rep["gates"]["waived"][0]["waived"]
    assert _published(proj) == good                                   # out/final.mp4 and its report untouched
    assert sorted(x.name for x in d.iterdir() if x.name.startswith(".")) == []


def test_bad_draft_build_still_fails_on_a_gate_no_profile_may_lower(proj, monkeypatch):
    _inject(monkeypatch, "R38")
    r = build.build(proj, mode="draft", raise_on_fail=False)
    assert not r.ok and r.final is None
    assert (proj / "out" / "draft" / "build_report.failed.json").is_file() and not (proj / "out" / "final.mp4").exists()


def test_bad_unknown_build_mode_is_an_engine_error(proj):
    with pytest.raises(EngineError, match="unknown build mode"):
        build.build(proj, mode="fast")


def test_bad_a_draft_build_cannot_be_delivered(proj):
    r = build.build(proj, mode="draft")
    with pytest.raises(EngineError, match="draft build"):
        deliver.check_build(r.final)
    release = build.build(proj)
    assert deliver.check_build(release.final)["build_id"]               # the release build still passes


def test_good_cli_build_and_preflight_accept_mode(proj, monkeypatch, capsys):
    need_toolchain()
    _inject(monkeypatch, "R62")
    assert cli.main(["build", str(proj), "--mode", "draft"]) == 0
    out = capsys.readouterr().out
    assert "draft build" in out and (proj / "out" / "draft" / "final.draft.mp4").is_file()
    assert cli.main(["build", str(proj)]) == 1                           # release: the same gate blocks
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(["build", str(proj), "--mode", "nope"])


def test_good_cli_preflight_mode_draft_reports_technical_fails_as_warnings(project, monkeypatch, capsys):
    p, _ = project(two_maps())
    from video_engine.gates import runner
    real = runner.layer2.r62_caption_margins
    monkeypatch.setattr(runner.layer2, "r62_caption_margins",
                        lambda tl, words: (real(tl, words)[0] + [Finding("R62", "fail", "injected")], real(tl, words)[1]))
    assert cli.main(["preflight", str(p), "--no-dom"]) == 1
    capsys.readouterr()
    assert cli.main(["preflight", str(p), "--no-dom", "--mode", "draft"]) == 0
    assert "[WARN] R62" in capsys.readouterr().out


# ----------------------------------------------------------------------------------------------- --repin
def _stale_project(project, fake_stt):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    d = json.loads((p / "master_timeline.json").read_text())
    d["audio_chain"]["caption_fixes"].append({"from": "akhir", "to": "Akhir"})
    (p / "master_timeline.json").write_text(json.dumps(d))
    assert layer1.pins_stale(timeline.load(p), p)
    return p


def test_good_repin_pins_stale_words_again_before_the_pre_flight(project, fake_stt, monkeypatch):
    p = _stale_project(project, fake_stt)
    seen = {}
    monkeypatch.setattr(build, "run_preflight", lambda tl, pd, words, **k: (seen.update(w=list(words)), _Rep())[1])
    r = build.build(p, repin=True)
    assert r.ok and any(w.word == "Akhir" for w in seen["w"])
    disk = timeline.load(p)
    assert not layer1.pins_stale(disk, p) and disk.captions[-1].word == "Akhir"
    assert any("--repin" in str(row) for row in r.timer.rows)


def test_good_repin_does_nothing_when_the_pins_are_current(project, fake_stt, monkeypatch):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    before = (p / "master_timeline.json").read_text()
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    monkeypatch.setattr(audio, "pin_captions", lambda *a, **k: pytest.fail("pinned again although current"))
    assert build.build(p, repin=True).ok
    assert (p / "master_timeline.json").read_text() == before


def test_bad_without_repin_a_stale_pin_still_fails_the_build(project, fake_stt):
    p = _stale_project(project, fake_stt)
    with pytest.raises(EngineError, match="W01"):                   # the pre-flight stops the build before any render
        build.build(p)
    assert not (p / "out" / "final.mp4").exists()


def test_good_cli_build_accepts_repin(project, fake_stt, monkeypatch, capsys):
    need_toolchain()
    p = _stale_project(project, fake_stt)
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: _Rep())
    assert cli.main(["build", str(p), "--repin"]) == 0
    assert Path(p / "out" / "final.mp4").is_file()
