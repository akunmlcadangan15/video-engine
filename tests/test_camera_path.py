"""HyperFrames camera_path: multi-key camera legs in one segment (schema, plan, template, R56 per leg, DOM probe,
draft map_path, renderer profile values in cache keys)."""
import pytest
from pydantic import ValidationError

import math

from video_engine import draft, hyperframes, layout, native, profiles
from video_engine.gates import layer1, layer2, style

PATH = [{"at_frame": 0, "focus": [0.3, 0.3], "zoom": 1.2},
        {"at_frame": 30, "focus": [0.5, 0.5], "zoom": 2.0},
        {"at_frame": 45, "focus": [0.7, 0.6], "zoom": 1.5}]


def _path(keys=PATH, preset=None):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc["camera_path"] = keys
        if preset:
            sc["camera_preset"] = preset
        sc["pins"] = [{"label": "Pusat", "x": 0.5, "y": 0.5, "at_frame": 0}]
    return mut


def _scene(tl, p):
    return hyperframes.scene_data(tl.segments[2], tl, p)


def _covers(st, W, H, mw, mh):
    return st["x"] <= 1e-6 and st["y"] <= 1e-6 and st["x"] + st["s"] * mw >= W - 1e-3 and st["y"] + st["s"] * mh >= H - 1e-3


# good ---------------------------------------------------------------------------------------------------------------
def test_good_preset_scene_is_one_glide_leg_with_the_preset_math(project):
    p, tl = project()
    sc = _scene(tl, p)
    mw, mh = sc["map_size"]
    a, b, m = hyperframes.camera_states(tl.segments[2].gsap_scene_config, 1080, 1920, mw, mh)
    assert sc["camera"] == {"start": a, "end": b, "legs": [{"f0": 0, "f1": 60, "to": b, "ease": "glide"}]}
    assert sc["motion_px"] == round(m, 2)


def test_good_path_legs_contiguous_and_drift_to_segment_end(project):
    p, tl = project(_path())
    sc = _scene(tl, p)
    legs = sc["camera"]["legs"]
    assert [(L["f0"], L["f1"], L["ease"]) for L in legs] == [(0, 30, "glide"), (30, 45, "glide"), (45, 60, "drift")]
    drift = sc["theme"]["camera_drift_per_s"]
    assert legs[2]["to"]["s"] == pytest.approx(1.5 * (1 + drift * 15 / 30), abs=1e-4)
    assert sc["camera"]["end"] == legs[-1]["to"]


def test_good_every_interpolated_state_covers_the_frame(project):
    """Legs tween x, y, s with one ease, so every in-between transform is a convex mix of two covering states."""
    p, tl = project(_path())
    sc = _scene(tl, p)
    mw, mh = sc["map_size"]
    prev = sc["camera"]["start"]
    for L in sc["camera"]["legs"]:
        for e in (0.0, 0.25, 0.5, 0.75, 1.0):
            st = {k: prev[k] + (L["to"][k] - prev[k]) * e for k in "xys"}
            assert _covers(st, 1080, 1920, mw, mh)
        prev = L["to"]


def test_good_R56_checks_each_leg(project):
    p, tl = project(_path())
    out, fixes = layer2.r56_camera(tl, p)
    r = [f for f in out if f.seg == 2]
    assert [f.level for f in r] == ["pass"] and "2 legs" in r[0].message and not fixes


def test_good_probe_times_include_key_frames(project):
    _, tl = project(_path())
    assert layer2.probe_times(tl.segments[2], 30) == [f / 30 for f in (0, 18, 30, 36, 45, 59)]
    _, tl0 = project()
    assert layer2.probe_times(tl0.segments[2], 30) == [18 / 30, 36 / 30, 59 / 30]


def test_good_path_opens_on_moving_map_and_keys_count_as_novelty(project):
    def mut(d):
        _path(preset="static")(d)
        d["segments"] = [d["segments"][2] | {"seg_index": 0, "start_frame": 0, "end_frame": 60}]
    p, tl = project(mut)
    h = style.h01_hook(tl, p, None, profiles.load("geoverum").gates.hook)
    assert not [f for f in h if "moving map" in f.message]
    assert style.camera_events(tl.segments[0]) == [0, 30, 45]


def test_good_dom_probe_follows_the_path(project, tmp_path):
    """At each key frame the pin under the key focus is where the engine camera puts it (template legs = engine
    math); the key-2 focus is at the frame centre. The zero-size pin anchor is probed but has no area for R63."""
    p, tl = project(_path())
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    res = hyperframes.probe_dom(d, [0.0, 1.0, 1.5], 1080, 1920)
    got = []
    for item, key in zip(res["times"], PATH):
        r = next(r for r in item["rects"] if r["id"] == "anchor:pin:0")
        cam = sc["camera"]["start"] if key["at_frame"] == 0 else next(L["to"] for L in sc["camera"]["legs"]
                                                                     if L["f1"] == key["at_frame"])
        mw, mh = sc["map_size"]
        assert (r["x"], r["y"]) == pytest.approx((cam["x"] + cam["s"] * 0.5 * mw, cam["y"] + cam["s"] * 0.5 * mh), abs=0.5)
        assert r["w"] == r["h"] == 0 and not layer2.visible([r])
        got.append((r["x"], r["y"]))
    assert got[1] == pytest.approx((540, 960), abs=0.5) and len({round(x) for x, _ in got}) == 3


def test_good_draft_map_path_converted_to_camera_path():
    keys = draft._camera_path([{"at_s": 2.0, "x": 0.5, "y": 1.4, "zoom": 9}, {"at_s": 0.3, "x": -1, "y": 0.2}], 180, 30)
    assert keys == [{"at_frame": 0, "focus": [0.0, 0.2], "zoom": 1.0}, {"at_frame": 60, "focus": [0.5, 1.0], "zoom": 4.0}]


def test_good_map_path_only_offered_by_profiles_that_enable_it():
    assert profiles.load("geoverum").draft.map_path and not profiles.load().draft.map_path


def test_good_renderer_profile_values_in_hf_key_not_in_native_key(project, monkeypatch):
    p, tl = project()
    k_hf = hyperframes.keys(tl.segments[2], tl, p)[0]
    k_nat = [native.plan(s, tl, p).key for s in tl.segments[:2]]
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update={"camera_drift_per_s": 0.05})})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))
    assert hyperframes.keys(tl.segments[2], tl, p)[0] != k_hf
    assert [native.plan(s, tl, p).key for s in tl.segments[:2]] == k_nat


STRAIGHT = [{"at_frame": 0, "focus": [0.3, 0.3], "zoom": 1.2},       # one direction through the inner key
            {"at_frame": 20, "focus": [0.42, 0.4], "zoom": 1.6},
            {"at_frame": 50, "focus": [0.58, 0.52], "zoom": 2.4}]


def _speeds(sc, n=60):
    """Screen speed (glide_speeds metric, px / frame) over every frame step."""
    W, H, cam = sc["width"], sc["height"], sc["camera"]
    R, st = math.hypot(W, H) / 2, [layout.camera_at(cam, f + 1e-7) for f in range(n)]
    c = lambda a: ((W / 2 - a["x"]) / a["s"], (H / 2 - a["y"]) / a["s"])
    return [math.hypot(R * math.log(b["s"] / a["s"]), math.dist(c(a), c(b)) * (a["s"] + b["s"]) / 2)
            for a, b in zip(st, st[1:])]


def test_good_path_speed_continuous_without_dip_at_inner_key(project):
    p, tl = project(_path(STRAIGHT))
    v = _speeds(_scene(tl, p))
    assert v[19] == pytest.approx(v[20], rel=0.1)                        # no speed step at the key
    assert min(v[10:30]) >= 0.9 * min(max(v[:20]), max(v[20:50]))         # and no dip around it


def test_good_path_frame_1_already_moving_and_glide_lands_into_the_drift(project):
    p, tl = project(_path(STRAIGHT))
    sc = _scene(tl, p)
    v = _speeds(sc)
    assert v[0] >= 0.25 * max(v)
    drift = math.hypot(sc["width"], sc["height"]) / 2 * sc["theme"]["camera_drift_per_s"] / 30
    assert v[49] < 0.1 * max(v) and v[50] == pytest.approx(drift, rel=0.05)   # the glide lands into the drift


def test_good_key_hold_stops_the_camera(project):
    keys = [STRAIGHT[0], {**STRAIGHT[1], "hold": 10}, {**STRAIGHT[2], "at_frame": 59}]
    p, tl = project(_path(keys))
    sc = _scene(tl, p)
    legs, v = sc["camera"]["legs"], _speeds(sc)
    assert (legs[1]["f0"], legs[1]["f1"], legs[1]["to"]) == (20, 30, legs[0]["to"])
    assert max(v[20:30]) < 1e-6 and v[19] < 0.1 * max(v) and v[30] < 0.1 * max(v)
    assert legs[0]["v"][1] == legs[2]["v"][0] == 0.0


def test_good_turn_back_stops_and_shot_end_keeps_moving(project):
    keys = [STRAIGHT[0], STRAIGHT[1], {"at_frame": 59, "focus": [0.3, 0.3], "zoom": 1.2}]
    p, tl = project(_path(keys))
    sc = _scene(tl, p)
    v = _speeds(sc)
    assert sc["camera"]["legs"][1]["v"] == [0.0, pytest.approx(sc["theme"]["camera_end_speed"], rel=0.2)]
    assert v[19] < 0.1 * max(v) and v[57] > 0.5 * sum(v[20:58]) / 38


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("keys, msg", [
    (PATH[:1], "at least 2 keys"),
    ([PATH[1], PATH[2]], "first key"),
    ([PATH[0], PATH[2], PATH[1]], "must be >"),
    ([PATH[0], {"at_frame": 20, "focus": [1.2, 0.5]}], "within 0..1"),
    ([PATH[0], {"at_frame": 20, "focus": [0.5, 0.5], "zoom": 0.5}], "greater than or equal to 1"),
    ([PATH[0], {**PATH[1], "hold": 15}, PATH[2]], "hold 15"),
], ids=["BAD-one-key", "BAD-first-not-0", "BAD-not-increasing", "BAD-focus-range", "BAD-zoom-below-cover",
        "BAD-hold-into-next-key"])
def test_bad_path_schema(project, keys, msg):
    with pytest.raises(ValidationError, match=msg):
        project(_path(keys))


def test_bad_last_key_beyond_segment_fails_T02(project):
    p, tl = project(_path([PATH[0], {"at_frame": 60, "focus": [0.5, 0.5]}]))
    assert any(f.level == "fail" and "camera_path last key" in f.message for f in layer1.t02_integrity(tl, p))


def test_bad_leg_without_motion_fails_R56(project):
    p, tl = project(_path([PATH[0], {"at_frame": 30, "focus": [0.301, 0.3], "zoom": 1.2}]))
    out, fixes = layer2.r56_camera(tl, p)
    assert any(f.level == "fail" and "leg 1" in f.message for f in out if f.seg == 2) and not fixes


def test_bad_draft_map_path_without_two_distinct_keys():
    with pytest.raises(ValueError, match="at least 2 keys"):
        draft._camera_path([{"at_s": 0, "x": 0.5, "y": 0.5}, {"at_s": 0.0, "x": 0.6, "y": 0.5}], 180, 30)
