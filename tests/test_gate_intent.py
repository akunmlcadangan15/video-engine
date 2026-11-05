"""Local creative intent: GOOD/BAD policy, integrity, schema and mutation contracts."""
import io

import numpy as np
import pytest
from pydantic import ValidationError

from video_engine import qa
from video_engine.gates import layer2, layer3, severity
from video_engine.timeline import CameraMove, SegmentIntent, Timeline
from video_engine.util import EngineError


def _intent(**kwargs):
    return SegmentIntent(reason="Deliberate composition", **kwargs)


@pytest.mark.parametrize("bad", [
    {}, {"motion": "automatic", "reason": "x"}, {"motion": "static", "reason": "  "},
    {"motion": "moving"}, {"negative_space": "true", "reason": "x"},
    {"reason": "x"}, {"motion": "static", "reason": "x", "global": True},
])
def test_bad_intent_rejects_unknown_or_ambiguous_contract(bad):
    with pytest.raises(ValidationError):
        SegmentIntent.model_validate(bad)


def test_good_intent_roundtrip_local_and_legacy_read(project):
    _, tl = project(lambda d: d["segments"][0].update(
        hold="read", intent={"motion": "moving", "negative_space": True, "reason": "  Local reveal  "}))
    clone = Timeline.model_validate(tl.model_dump())
    assert clone.segments[0].intent.reason == "Local reveal"
    assert clone.segments[0].motion_expectation == "moving"
    assert clone.segments[1].intent is None and clone.segments[1].motion_expectation is None
    clone.segments[0].intent = None
    assert clone.segments[0].motion_expectation == "static"
    assert not CameraMove().auto_intensity and not clone.segments[0].creative_autofix


@pytest.mark.parametrize("intent,level", [(None, "warn"), (_intent(motion="static"), "info"),
                                          (_intent(motion="moving"), "fail")])
def test_good_frozen_samples_respect_only_local_motion_intent(project, monkeypatch, intent, level):
    _, tl = project()
    tl.segments = tl.segments[:2]
    tl.segments[0].intent = intent
    frame = np.random.default_rng(7).integers(40, 220, (480, 270, 3), dtype=np.uint8).tobytes()
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter([frame] * 24))
    fs, _ = qa.pixel_gates(None, tl)
    fs = severity.apply(fs, tl, draft=True)
    motion = {f.seg: f for f in fs if f.gate == "R56px"}
    assert motion[0].level == level and motion[1].level == "warn"
    if intent:
        assert intent.reason in motion[0].message


def test_good_legacy_read_motion_is_info(project, monkeypatch):
    _, tl = project(lambda d: d["segments"][0].update(hold="read"))
    tl.segments = tl.segments[:1]
    frame = bytes([120]) * (480 * 270 * 3)
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter([frame] * 12))
    fs, _ = qa.pixel_gates(None, tl)
    assert any(f.gate == "R56px" and f.level == "info" and "legacy hold read" in f.message for f in fs)


def test_good_negative_space_is_advisory_and_local_but_black_is_hard(project, monkeypatch):
    _, tl = project()
    intent = _intent(motion="static", negative_space=True)
    tl.segments[0].intent = intent
    st = (120.0, 0.0, [0.0, 30.0, 30.0], [0.0, 20.0, 20.0])
    assert layer3.r38_check(st, 0, "snapshot", intent)[0].level == "info"
    assert intent.reason in layer3.r38_check(st, 0, "snapshot", intent)[0].message
    assert layer3.r38_check(st, 1, "snapshot")[0].level == "warn"
    fs = layer3.r38_check((0.0, 1.0, [0.0]*3, [0.0]*3), 0, "snapshot", intent)
    assert [(f.gate, f.level) for f in severity.apply(fs, tl, draft=True)] == [("R38", "fail")]
    monkeypatch.setattr(layer3, "snapshots", lambda *args: {0: bytes([120]) * (480 * 270 * 3),
                                                            1: bytes([120]) * (480 * 270 * 3)})
    fs, _ = layer3.r38_snapshots(tl, None, {})
    assert {f.level for f in fs if f.gate == "R38space" and f.seg == 0} == {"info"}
    assert {f.level for f in fs if f.gate == "R38space" and f.seg == 1} == {"warn"}


def test_bad_intent_never_waives_no_frames_or_decode_failure(project, monkeypatch):
    _, tl = project()
    tl.segments[0].intent = _intent(motion="static", negative_space=True)
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter(()))
    fs, _ = qa.pixel_gates(None, tl)
    assert all(f.gate == "R38" and f.level == "fail" for f in severity.apply(fs, tl, draft=True))

    def broken(*args):
        raise EngineError("QA frame decode failed")
    monkeypatch.setattr(qa, "iter_sample_frames", broken)
    with pytest.raises(EngineError, match="decode failed"):
        qa.pixel_gates(None, tl)


def test_bad_partial_rgb_frame_is_decode_error(monkeypatch):
    class Proc:
        stdout = io.BytesIO(b"incomplete")
        stderr = io.BytesIO()
        def wait(self):
            return 0
        def poll(self):
            return 0
    monkeypatch.setattr(qa.subprocess, "Popen", lambda *args, **kwargs: Proc())
    with pytest.raises(EngineError, match="partial RGB frame"):
        list(qa.iter_sample_frames(None, 1080, 1920))


@pytest.mark.parametrize("native", [True, False])
def test_good_creative_autofix_default_does_not_mutate_but_explicit_optin_does(project, monkeypatch, native):
    p, tl = project()
    s = tl.segments[0 if native else 2]
    tl.segments = [s]
    if native:
        s.camera_move.intensity = 0.1
        monkeypatch.setattr(layer2, "_native_motion", lambda s, tl, sw, sh, i: i * 100)
    else:
        s.gsap_scene_config.camera_intensity = 0.1
        monkeypatch.setattr(layer2, "_hf_motion", lambda s, tl, p, i: i * 100)
    before = tl.model_dump()
    fs, fixes = layer2.r56_camera(tl, p)
    assert tl.model_dump() == before and not fixes
    assert any(f.level == "warn" for f in fs)
    s.creative_autofix = True
    fs, fixes = layer2.r56_camera(tl, p)
    assert fixes and fixes[0]["from"] == 0.1 and fixes[0]["to"] > 0.1
    assert any(f.level == "fix" for f in fs)


def test_good_static_intent_prevents_creative_autofix_even_when_enabled(project):
    p, tl = project()
    s = tl.segments[0]
    tl.segments = [s]
    s.intent = _intent(motion="static")
    s.creative_autofix = True
    s.camera_move.auto_intensity = True
    s.camera_move.intensity = 0.1
    fs, fixes = layer2.r56_camera(tl, p)
    assert not fixes and s.camera_move.intensity == 0.1 and fs[0].level == "info"

@pytest.mark.parametrize("target", ["camera", "segment"])
def test_bad_creative_optin_requires_real_boolean(project, target):
    if target == "camera":
        with pytest.raises(ValidationError):
            CameraMove(auto_intensity="true")
    else:
        with pytest.raises(EngineError, match="creative_autofix"):
            project(lambda d: d["segments"][0].update(creative_autofix="true"))


def test_good_negative_space_runs_collapse_with_sample_ranges(project, monkeypatch):
    _, tl = project()
    tl.segments = tl.segments[:1]
    blank = bytes([120]) * (480 * 270 * 3)
    detailed = np.random.default_rng(9).integers(40, 220, (480, 270, 3), dtype=np.uint8).tobytes()
    # Two runs separated by a detailed sample; do not hide the gap in a merged warning.
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter([blank, blank, detailed, blank, blank]))
    fs, _ = qa.pixel_gates(None, tl)
    space = [f for f in fs if f.gate == "R38space"]
    assert len(space) == 6  # three thirds, two distinct runs (rather than twelve findings)
    assert {f.frames for f in space} == {(0, 10), (15, 25)}
    assert all("2 samples; first sample" in f.message for f in space)
    report = severity.summary(fs, tl)
    assert all(row["frames"] in ([0, 10], [15, 25]) for row in report["warns"])


def test_good_report_retains_intent_reason_even_without_failed_pixels(project):
    _, tl = project(lambda d: d["segments"][0].update(
        hold="read", intent={"negative_space": True, "reason": "  Space for reflection  "}))
    assert severity.summary([], tl)["segment_intents"] == [
        {"seg": 0, "intent": {"motion": None, "negative_space": True, "reason": "Space for reflection"}, "hold": "read"}]

@pytest.mark.parametrize("motion,level", [("moving", "fail"), ("static", "info"), (None, "info"), ("read", "info")])
def test_singleton_samples_verify_only_explicit_moving_contract(project, monkeypatch, motion, level):
    _, tl = project()
    tl.segments = tl.segments[:1]
    s = tl.segments[0]
    s.end_frame = 5
    if motion == "read":
        s.hold = "read"
    elif motion:
        s.intent = _intent(motion=motion)
    frame = np.random.default_rng(7).integers(40, 220, (480, 270, 3), dtype=np.uint8).tobytes()
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter([frame]))
    fs, info = qa.pixel_gates(None, tl)
    fs = severity.apply(fs, tl, draft=True)
    finding = next(f for f in fs if f.gate == "R56px")
    assert finding.level == level and finding.frames == (0, 5)
    assert "too few" in finding.message and "no visible motion" not in finding.message
    assert info["motion"] == {}
    if motion == "moving":
        assert "cannot verify expected motion" in finding.message
        assert s.intent.reason in finding.message
    else:
        assert "cannot verify expected motion" not in finding.message


def test_good_QA_pass_message_does_not_claim_universal_motion(project, monkeypatch):
    _, tl = project()
    tl.segments = tl.segments[:1]
    tl.segments[0].end_frame = 10
    frames = [np.random.default_rng(seed).integers(40, 220, (480, 270, 3), dtype=np.uint8).tobytes()
              for seed in range(2)]
    monkeypatch.setattr(qa, "iter_sample_frames", lambda *args: iter(frames))
    fs, _ = qa.pixel_gates(None, tl)
    assert len(fs) == 1 and fs[0].level == "pass"
    assert "no integrity failures or motion warnings" in fs[0].message
    assert "motion in every segment" not in fs[0].message

@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("intentional", [False, True])
def test_negative_space_explicit_requirement_overrides_local_intent(project, required, draft, intentional):
    def mut(d):
        if required:
            d["gates"] = {"require": ["R38space"]}
        if intentional:
            d["segments"][0]["intent"] = {"negative_space": True, "reason": "Space for reflection"}
    _, tl = project(mut)
    s = tl.segments[0]
    st = (120.0, 0.0, [0.0, 30.0, 30.0], [0.0, 20.0, 20.0])
    fs = severity.apply(layer3.r38_check(st, 0, "snapshot", s.intent), tl, draft=draft)
    assert len(fs) == 1 and fs[0].gate == "R38space"
    assert fs[0].level == ("fail" if required else "info" if intentional else "warn")
    if required:
        assert "explicitly required" in fs[0].message and fs[0].waived is None
    if intentional:
        assert "Space for reflection" in fs[0].message


@pytest.mark.parametrize("draft", [False, True])
def test_bad_required_negative_space_cannot_be_waived_project_wide(project, draft):
    _, tl = project(lambda d: d.update(gates={"require": ["R38space"], "waive": ["R38space"]}))
    st = (120.0, 0.0, [0.0, 30.0, 30.0], [0.0, 20.0, 20.0])
    fs = severity.apply(layer3.r38_check(st, 0, "snapshot", _intent(negative_space=True)), tl, draft=draft)
    assert next(f for f in fs if f.gate == "R38space").level == "fail"
    assert any(f.gate == "GW01" and "waive" in f.message and "ignored" in f.message for f in fs)
