"""SFX cue model: render events mapped to profile SFX kinds, one cue per event with time, kind, gain and source
asset id."""
import pytest
from pydantic import ValidationError

from video_engine import hyperframes, profiles, sfx
from video_engine.timeline import SfxCue

FPS = 30


def _geo(extra=None):
    def mut(d):
        d["profile"] = "geoverum"
        sc = d["segments"][2]["gsap_scene_config"]
        sc["pins"] = [{"label": "A", "x": 0.4, "y": 0.3, "at_frame": 3}]
        sc["regions"] = [{"entity": "Sekutu", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.3, 0.5]], "at_frame": 6}]
        sc["routes"] = [{"points": [[0.1, 0.1], [0.6, 0.6]], "at_frame": 10, "draw_s": 1.0}]
        sc["camera_path"] = [{"at_frame": 0, "focus": [0.3, 0.4], "zoom": 1.5},
                             {"at_frame": 45, "focus": [0.7, 0.4], "zoom": 1.5, "move": "whip"}]
        sc["title"] = "SEKUTU"
        sc["title_at_frame"] = 8
        sc["figures"] = [{"kind": "number", "value": 6, "motion": "counter", "at_frame": 12, "count_s": 0.5,
                          "position": [0.5, 0.6]}]
        if extra:
            extra(d)
    return mut


# good ---------------------------------------------------------------------------------------------------------------
def test_good_render_events_map_to_profile_kinds_at_their_frames(project):
    p, tl = project(_geo())
    th = profiles.of(tl).render_values("hf")
    ev = sfx.events(tl)
    got = {(e["event"], e["frame"]) for e in ev if e["event"] != "counter_tick"}
    w = round(th["whip_s"] * FPS)
    assert ("pin", 120 + 3 + round(sfx.PIN_LAND_S * FPS)) in got           # teardrop landing
    assert ("region", 126) in got and ("title", 128) in got
    assert ("route", 120 + round((10 + 10 + 30) / 2)) in got               # mid-draw
    assert ("whip", 120 + round((45 - w + 45) / 2)) in got                 # mid-move
    ticks = [e["frame"] for e in ev if e["event"] == "counter_tick"]
    assert ticks == [e["frame"] for e in hyperframes.sfx_events(tl)] and len(ticks) == 6   # one per shown step
    assert ev == sorted(ev, key=lambda e: (e["frame"], e["event"]))
    sp = profiles.of(tl).sound
    assert sfx.kinds_used(tl) == sorted({sp.events[e["event"]] for e in ev})


def test_good_cues_carry_time_kind_gain_and_source_asset(project):
    p, tl = project(_geo())
    sp = profiles.of(tl).sound
    cs = sfx.cues(tl, lambda k: (f"id:{k}", 0.25))
    assert len(cs) == len(sfx.events(tl))
    for c in cs:
        k = sp.kinds[c.kind]
        assert c.kind == sp.events[c.event] and c.gain_db == k.gain_db and c.asset_id == f"id:{c.kind}"
        want = c.frame / FPS - (0.25 if k.align == "peak" else 0)
        assert c.time_s == pytest.approx(max(0, want), abs=1e-4) and c.trim_s == pytest.approx(max(0, -want), abs=1e-4)
    whoosh = [c for c in cs if c.kind == "whoosh"]
    assert whoosh and all(c.time_s == pytest.approx(c.frame / FPS - 0.25, abs=1e-4) for c in whoosh)  # peak on event


def test_good_default_profile_has_no_cues(project):
    p, tl = project()
    assert sfx.kinds_used(tl) == [] and sfx.cues(tl, lambda k: ("x", 0)) == []


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_unknown_cue_kind_is_rejected():
    with pytest.raises(ValidationError):
        SfxCue(time_s=0, kind="boom", gain_db=-6, asset_id="x", event="pin", frame=0)
    with pytest.raises(ValidationError):
        SfxCue(time_s=0, kind="pop", gain_db=3, asset_id="x", event="pin", frame=0)


@pytest.mark.parametrize("sound, msg", [
    ({"events": {"pin": "boom"}, "kinds": {"boom": {"gain_db": -6}}}, "unknown"),
    ({"events": {"pin": "pop"}, "kinds": {}}, "has no"),
    ({"events": {"explosion": "pop"}, "kinds": {"pop": {"gain_db": -6}}}, "unknown event"),
])
def test_bad_profile_sound_tables_are_rejected(sound, msg):
    with pytest.raises(ValidationError, match=msg):
        profiles.SoundProfile.model_validate(sound)
