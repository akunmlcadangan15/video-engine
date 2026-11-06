"""Two-aspect directing (video_engine/framing.py): one shot intent, framed per orientation by the format adapter."""
from __future__ import annotations

import pytest

from video_engine import framing, profiles
from video_engine.util import EngineError

REASON = "story beat"


def _shot(shot, W=1080, H=1920, still_camera=False, hf_preset=None):
    def mut(d):
        d["settings"]["width"], d["settings"]["height"] = W, H
        s0, s2 = d["segments"][0], d["segments"][2]
        s0["intent"] = {"shot": shot, "reason": REASON}
        if not still_camera:
            s0.pop("camera_move")
        s2["intent"] = {"shot": shot, "reason": REASON}
        if hf_preset:
            s2["gsap_scene_config"]["camera_preset"] = hf_preset
        else:
            s2["gsap_scene_config"].pop("camera_preset", None)
    return mut


@pytest.mark.parametrize("shot", framing.SHOTS)
def test_good_one_intent_is_framed_per_orientation(project, shot):
    _, tp = project(_shot(shot))
    _, tl = project(_shot(shot, 1920, 1080))
    fp, fl = framing.DEFAULTS["portrait"][shot], framing.DEFAULTS["landscape"][shot]
    assert tp.segments[0].camera_move.preset == fp.native and tp.segments[0].camera_move.intensity == fp.intensity
    assert tl.segments[0].camera_move.preset == fl.native
    assert tp.segments[2].gsap_scene_config.camera_preset == fp.hf
    assert tl.segments[2].gsap_scene_config.camera_preset == fl.hf
    assert tp.segments[0].intent.shot == tl.segments[0].intent.shot == shot   # the story layer is shared


def test_good_adapter_follows_the_proposal_table():
    P, L = framing.DEFAULTS["portrait"], framing.DEFAULTS["landscape"]
    assert all(f.subject == (0.5, 0.45) and f.read == "top_down" and f.title_slot == "top" for f in P.values()
               if not f.subjects)
    assert all(f.read == "left_right" and f.caption_slot == "lower_third" for f in L.values())
    assert L["track"].subject == pytest.approx((1 / 3, 0.5))                       # rule of thirds
    assert all(L[s].intensity <= P[s].intensity for s in framing.SHOTS)          # wider field of view in 16:9
    assert sum(P[s].native.startswith("ken_burns") for s in framing.SHOTS) > \
        sum(L[s].native.startswith("ken_burns") for s in framing.SHOTS)          # push in 9:16, lateral pan in 16:9
    assert L["compare"].subjects[0][1] == L["compare"].subjects[1][1]            # side by side
    assert P["compare"].subjects[0][0] == P["compare"].subjects[1][0]            # stacked


def test_good_explicit_camera_wins_over_the_framing(project):
    _, tl = project(_shot("reveal", 1920, 1080, still_camera=True, hf_preset="pan_south"))
    assert tl.segments[0].camera_move.preset == "ken_burns_zoom_in"
    assert tl.segments[2].gsap_scene_config.camera_preset == "pan_south"


def test_good_segments_without_a_shot_are_untouched(project):
    _, a = project()
    assert a.segments[2].gsap_scene_config.camera_preset == "zoom_in"
    seg = a.segments[0].model_copy(update={"intent": None})
    assert "shot" not in str(seg.model_dump())


def test_good_intent_without_shot_keeps_its_dump(project):
    def mut(d):
        d["segments"][0]["intent"] = {"motion": "static", "reason": REASON}
    _, tl = project(mut)
    assert tl.segments[0].intent.model_dump() == {"motion": "static", "negative_space": False, "reason": REASON}


def test_bad_intent_needs_motion_space_or_shot(project):
    with pytest.raises(Exception, match="intent needs motion, intentional negative_space or a shot"):
        project(lambda d: d["segments"][0].update(intent={"reason": REASON}))


def test_good_profile_overrides_one_shot(tmp_path, monkeypatch, project):
    from video_engine import config
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text('[framing.landscape.establish]\nnative = "pan_left"\nintensity = 0.5\n')
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    _, tl = project(lambda doc: (_shot("establish", 1920, 1080)(doc), doc.update(profile="chan")))
    assert tl.segments[0].camera_move.preset == "pan_left" and tl.segments[0].camera_move.intensity == 0.5
    assert profiles.load("chan").blake3 != profiles.load().blake3


@pytest.mark.parametrize("text,msg", [('[framing.square.establish]\nnative = "pan_left"\n', "orientation"),
                                      ('[framing.landscape.zoom]\nnative = "pan_left"\n', "unknown shot"),
                                      ('[framing.landscape.hold]\nspeed = 2\n', "unknown keys"),
                                      ('[framing.landscape.hold]\nintensity = 9\n', "0..6")])
def test_bad_framing_overrides(tmp_path, monkeypatch, text, msg):
    from video_engine import config
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text(text)
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError, match=msg):
        profiles.load("chan")


def test_good_describe_lists_every_shot_for_the_orientation():
    p, ls = framing.describe("portrait"), framing.describe("landscape")
    assert all(f"- {s}:" in p and f"- {s}:" in ls for s in framing.SHOTS)
    assert "title slot top_left" in ls and "title slot top_left" not in p
