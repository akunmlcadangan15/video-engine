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


@pytest.mark.parametrize("hold", [0, 30, 40])
def test_good_whip_cue_sits_mid_camera_whip_leg_after_a_hold(project, hold):
    """The whoosh lands mid-way through the whip leg hyperframes.camera_plan renders, which starts after the previous
    key's hold. Before the fix the cue ignored the hold (a 40-frame hold before the whip key at frame 45 put it 4
    frames early)."""
    def extra(d):
        d["segments"][2]["gsap_scene_config"]["camera_path"][0]["hold"] = hold
    p, tl = project(_geo(extra))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    whip = [L for L in sc["camera"]["legs"] if L["ease"] == "whip"]
    assert len(whip) == 1
    want = tl.segments[2].start_frame + round((whip[0]["f0"] + whip[0]["f1"]) / 2)
    assert [e["frame"] for e in sfx.events(tl) if e["event"] == "whip"] == [want]


def test_good_cues_carry_time_kind_gain_and_source_asset(project):
    p, tl = project(_geo())
    sp = profiles.of(tl).sound
    ev = sfx.events(tl)
    cs = sfx.cues(tl, lambda k: (f"id:{k}", 0.25))
    assert [c.event for c in cs if c.event != "counter_tick"] == [e["event"] for e in ev if e["event"] != "counter_tick"]
    tick_frames = [c.frame for c in cs if c.event == "counter_tick"]
    raw_tick_frames = [e["frame"] for e in ev if e["event"] == "counter_tick"]
    assert tick_frames and set(tick_frames) <= set(raw_tick_frames) and raw_tick_frames[-1] in tick_frames
    for c in cs:
        k = sp.kinds[c.kind]
        assert c.kind == sp.events[c.event] and c.gain_db == k.gain_db and c.asset_id == f"id:{c.kind}"
        want = c.frame / FPS - (0.25 if k.align == "peak" else 0)
        assert c.time_s == pytest.approx(max(0, want), abs=1e-4) and c.trim_s == pytest.approx(max(0, -want), abs=1e-4)
    whoosh = [c for c in cs if c.kind == "whoosh"]
    assert whoosh and all(c.time_s == pytest.approx(c.frame / FPS - 0.25, abs=1e-4) for c in whoosh)  # peak on event


def test_good_counter_tick_throttled_by_min_gap_s(project, tmp_path, monkeypatch):
    """[sound.kinds.counter_tick] min_gap_s caps cue rate, coalesces simultaneous counters, and keeps closing ticks;
    min_gap_s = 0.0 emits one cue per event."""
    def two_dense(d):
        _geo()(d)
        d["segments"][2]["gsap_scene_config"]["figures"] = [
            {"kind": "number", "value": 60, "motion": "counter", "at_frame": 12, "count_s": 0.5, "position": [0.4, 0.5]},
            {"kind": "number", "value": 60, "motion": "counter", "at_frame": 12, "count_s": 0.6, "position": [0.6, 0.7]},
        ]

    p, tl = project(two_dense)
    sp = profiles.of(tl).sound
    min_gap = sp.kinds["counter_tick"].min_gap_s
    assert min_gap == pytest.approx(0.065)
    raw = hyperframes.sfx_events(tl)
    cs = [c for c in sfx.cues(tl, lambda k: (f"id:{k}", 0.0)) if c.event == "counter_tick"]
    frames = [c.frame for c in cs]
    assert len(cs) < len(raw) and len(frames) == len(set(frames))
    assert all((b - a) / FPS >= min_gap - 1e-9 for a, b in zip(frames, frames[1:]))
    assert frames[0] == raw[0]["frame"]
    closing_fig1 = max(e["frame"] for e in raw if e["figure"] == 1)
    assert closing_fig1 in frames

    # Explicit min_gap_s = 0.0 preserves 1:1 unthrottled cue emission.
    from video_engine import config
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    (pdir / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (pdir / "unthrottled.toml").write_text(
        '[sound.events]\ncounter_tick = "counter_tick"\n'
        '[sound.kinds.counter_tick]\ngain_db = -20.0\nmax_s = 0.08\nmin_gap_s = 0.0\n'
    )
    monkeypatch.setattr(config, "PROFILES_DIR", pdir)
    profiles.clear_cache()
    try:
        tl0 = tl.model_copy(update={"profile": "unthrottled"})
        cs0 = sfx.cues(tl0, lambda k: (f"id:{k}", 0.0))
        assert [c.frame for c in cs0] == [e["frame"] for e in sfx.events(tl0) if e["event"] == "counter_tick"]
    finally:
        profiles.clear_cache()


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
    ({"kinds": {"counter_tick": {"gain_db": -20, "min_gap_s": -0.1}}}, "greater than or equal to 0"),
    ({"kinds": {"counter_tick": {"gain_db": -20, "min_gap_s": 6.0}}}, "less than or equal to 5"),
])
def test_bad_profile_sound_tables_are_rejected(sound, msg):
    with pytest.raises(ValidationError, match=msg):
        profiles.SoundProfile.model_validate(sound)
