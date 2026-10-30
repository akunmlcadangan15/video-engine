"""Profile [camera] pulses, [transitions] (zoom whip, page turn), [layout.fly] and their gates (P01, C01)."""
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from video_engine import audio, config, hyperframes, layout, profiles, timeline, transitions
from video_engine.gates import style
from video_engine.util import EngineError

from test_style_gates import layout as tl_layout, levels

W, H, FPS = 1080, 1920, 30
VIEW = ((1000.0, 1500.0), 1.5)
PULSE = profiles.CameraProfile(pulse_zoom=0.3, pulse_s=0.8, pulse_gap_s=0.3, pulse_pan=0.04,
                               pulse_direction="in").scene()


def legs(pulse, f0=0, f1=120, drift=0.02, **kw):
    return hyperframes.drift_legs(VIEW, f0, f1, drift, pulse, W, H, 2000.0, 3000.0, FPS, **kw)


def scale_series(ls, start):
    cam = {"start": start, "legs": ls}
    return [layout.camera_at(cam, f)["s"] for f in range(ls[-1]["f1"] + 1)]


def _profile(monkeypatch, name="default", **tables):
    prof = profiles.load(name)
    data = prof.data.model_copy(update=tables)
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile(name, data, "x"))
    return data


# [camera] pulses: drift_legs --------------------------------------------------------------------------------------
def test_good_default_camera_profile_has_no_pulses_and_one_drift_leg():
    assert profiles.CameraProfile().scene() is None
    assert "camera_pulse" not in profiles.load().render_values("hf")
    assert legs(None) == [{"f0": 0, "f1": 120, "to": legs(None)[0]["to"], "ease": "drift"}]


def test_good_pulses_fill_the_wait_with_even_gaps_and_alternate_away_and_back():
    ls = legs(PULSE)
    pulses = [L for L in ls if L["ease"] == "pulse"]
    P, G = 24, 9
    k = (120 - G) // (P + G)
    assert len(pulses) == k == 3
    assert all(L["f1"] - L["f0"] == P and L["bez"] == [0.3, 0.0, 0.15, 1.0] for L in pulses)
    gaps = [L["f1"] - L["f0"] for L in ls if L["ease"] == "drift"]
    assert sum(gaps) + k * P == 120 and max(gaps) - min(gaps) <= 1 and min(gaps) >= G
    assert [L["f0"] for L in ls] == sorted(L["f0"] for L in ls) and ls[-1]["f1"] == 120
    base = VIEW[1]
    s = [L["to"]["s"] for L in pulses]
    assert s[0] / base == pytest.approx(math.exp(0.3 + 0.02 * pulses[0]["f1"] / FPS), rel=1e-5)   # away: push in
    assert s[1] / base == pytest.approx(math.exp(0.02 * pulses[1]["f1"] / FPS), rel=1e-5)          # back on base
    assert [L.get("away") for L in pulses] == [0, 0, 1]   # away pulse 0, its way back, away pulse 1
    assert "away" not in ls[4]                              # drift on the base between them


def test_good_pulse_peak_speed_follows_the_bezier():
    ss = scale_series(legs(PULSE), hyperframes._state(*VIEW, W, H, 2000.0, 3000.0))
    peak = max(abs(math.log(b / a)) for a, b in zip(ss, ss[1:])) * FPS
    assert peak == pytest.approx(2.82 * 0.3 / 0.8, rel=0.12)


def test_good_drift_never_stops_between_pulses():
    ls = legs(PULSE)
    ss = scale_series(ls, hyperframes._state(*VIEW, W, H, 2000.0, 3000.0))
    for L in ls:
        if L["ease"] == "drift" and L["f1"] - L["f0"] > 1:
            assert ss[L["f1"]] > ss[L["f0"]]


def test_good_drift_zooms_at_a_constant_ln_rate_however_long_the_wait():
    for n in (60, 600):
        ss = scale_series(legs(None, 0, n, drift=0.06), hyperframes._state(*VIEW, W, H, 2000.0, 3000.0))
        rates = [math.log(b / a) * FPS for a, b in zip(ss, ss[1:])]
        assert min(rates) == pytest.approx(0.06, rel=1e-4) and max(rates) == pytest.approx(0.06, rel=1e-4)


def test_bad_linear_drift_would_slow_down_in_a_long_wait():
    # the old drift added a constant zoom per second; its ln rate falls as the zoom grows (the P01 hold in long waits)
    lin = [1.5 * (1 + 0.06 * f / FPS) for f in range(601)]
    rates = [math.log(b / a) * FPS for a, b in zip(lin, lin[1:])]
    assert rates[-1] < 0.6 * rates[0]


def test_good_drift_zooms_about_the_anchor_point():
    ls = legs(None, 0, 300, drift=0.06, anchor=(0.5, 0.35))
    start = hyperframes._state(*VIEW, W, H, 2000.0, 3000.0)
    cam = {"start": start, "legs": ls}
    for f in (0, 150, 300):
        c = layout.camera_at(cam, f)
        # the map point under the anchor stays put on screen
        mx, my = (0.5 * W - c["x"]) / c["s"], (0.35 * H - c["y"]) / c["s"]
        mx0, my0 = (0.5 * W - start["x"]) / start["s"], (0.35 * H - start["y"]) / start["s"]
        assert (mx, my) == pytest.approx((mx0, my0), abs=1e-3)


def test_good_drift_bound_ping_pongs_inside_the_bound():
    start = hyperframes._state(*VIEW, W, H, 2000.0, 3000.0)
    ls = legs(None, 0, 600, drift=0.06, bound=0.1)
    ss = scale_series(ls, start)
    g = [math.log(v / start["s"]) for v in ss]
    assert max(g) <= 0.1 + 1e-4 and min(g) >= -1e-4 and len(ls) > 3
    steps = [abs(b - a) * FPS for a, b in zip(g, g[1:])]
    assert min(steps) > 0.05   # never still, even at a turn


def test_bad_unbounded_drift_leaves_the_bound():
    start = hyperframes._state(*VIEW, W, H, 2000.0, 3000.0)
    ss = scale_series(legs(None, 0, 600, drift=0.06), start)
    assert math.log(ss[-1] / start["s"]) == pytest.approx(1.2, rel=1e-4)


def test_good_pulse_ends_keep_the_drift_rate():
    rho = 0.5
    for L in ({"f0": 0, "f1": 24, "to": {"s": rho}, "ease": "pulse", "bez": [0.3, 0.0, 0.15, 1.0], "lr": 0.048},):
        e = hyperframes.leg_ease(L, 1.0)
        s = [1 + (rho - 1) * e(t / 1000) for t in range(1001)]
        r = [math.log(b / a) * 1000 for a, b in zip(s, s[1:])]
        assert all(v < 0 for v in r)                              # zooms out the whole leg: never reverses
        assert abs(r[0]) == pytest.approx(0.048, rel=0.1) and abs(r[-1]) == pytest.approx(0.048, rel=0.1)


def test_good_whip_after_a_drift_has_no_lr_so_its_pan_stays_in_out(project):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["camera_path"] = [
            {"at_frame": 0, "focus": [0.3, 0.4], "zoom": 1.5}, {"at_frame": 45, "focus": [0.7, 0.4], "zoom": 1.5, "move": "whip"}]
    p, tl = project(mut)
    whip = [L for L in hyperframes.scene_data(tl.segments[2], tl, p)["camera"]["legs"] if L["ease"] == "whip"]
    assert whip and all("lr" not in L for L in whip)


def test_bad_whip_without_lr_stops_at_both_ends():
    e = hyperframes.leg_ease({"f0": 0, "f1": 24, "to": {"s": 0.5}, "ease": "whip"}, 1.0)
    assert e(0.001) < 1e-9 and 1 - e(0.999) < 1e-9


def test_good_bound_drift_shrinks_to_a_floor_then_reports_no_change():
    keys = [{"at_frame": 0}, {"at_frame": 90}]
    assert layout.bound_drift(keys, {1: 0.2}) and keys[1]["drift_max"] == pytest.approx(0.14)
    assert "drift_max" not in keys[0]
    for _ in range(20):
        if not layout.bound_drift(keys, {1: 0.2}):
            break
    assert keys[1]["drift_max"] == layout.DRIFT_MIN and not layout.bound_drift(keys, {1: 0.2})


def test_bad_camera_key_drift_max_out_of_range():
    for v in (0.0, -0.1, 3.0):
        with pytest.raises(ValidationError):
            timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], drift_max=v)


def test_good_pulse_size_list_sets_each_away_pulse_and_zero_is_drift():
    ls = legs({**PULSE, "zoom": [0.3, 0.0]}, base_zoom=0.3)
    assert [L["ease"] for L in ls].count("pulse") == 2   # away 0 and its way back; away 1 (size 0) is drift
    assert all("away" not in L for L in ls if L["f0"] >= ls[3]["f1"])


def test_bad_wait_shorter_than_gap_pulse_gap_has_no_pulse():
    assert [L["ease"] for L in legs(PULSE, 0, 2 * 9 + 23)] == ["drift"]


def test_good_cubic_bezier_matches_css_endpoints_and_is_monotonic():
    v = [layout.cubic_bezier(0.3, 0.0, 0.15, 1.0, t / 50) for t in range(51)]
    assert v[0] == 0 and v[-1] == 1 and all(b >= a for a, b in zip(v, v[1:]))
    assert layout.cubic_bezier(0.0, 0.0, 1.0, 1.0, 0.37) == pytest.approx(0.37, abs=1e-6)


def test_bad_camera_profile_values_rejected():
    with pytest.raises(ValidationError):
        profiles.CameraProfile(pulse_ease=(1.5, 0, 0.15, 1))
    with pytest.raises(ValidationError):
        profiles.CameraProfile(pulse_anchor=(0.5, 1.2))
    with pytest.raises(ValidationError):
        profiles.CameraProfile(pulse_direction="sideways")


# CameraKey.pulse ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("v", [None, 0.0, 0.25, [0.3, 0.0, 0.1]])
def test_good_camera_key_pulse_values(v):
    assert timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], pulse=v).pulse == v


@pytest.mark.parametrize("v", [-0.1, 1.5, [], [0.2, 2.0]])
def test_bad_camera_key_pulse_values(v):
    with pytest.raises(ValidationError):
        timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], pulse=v)


# planner: pulses shrink where a frame breaks a layout rule ---------------------------------------------------------
def test_good_shrink_pulses_shrinks_only_the_conflicting_away_pulse():
    keys = [{"at_frame": 0}, {"at_frame": 90}]
    layout.shrink_pulses(keys, {0: (3, {1})}, 0.3)
    assert keys[0]["pulse"] == [0.3, 0.225, 0.3] and "pulse" not in keys[1]
    layout.shrink_pulses(keys, {0: (3, {1})}, 0.3)
    layout.shrink_pulses(keys, {0: (3, {1})}, 0.3)
    layout.shrink_pulses(keys, {0: (3, {1})}, 0.3)
    assert keys[0]["pulse"] == [0.3, 0.0, 0.3]


def test_bad_shrink_pulses_at_zero_stays_zero():
    keys = [{"at_frame": 0, "pulse": [0.0]}]
    layout.shrink_pulses(keys, {0: (2, {0})}, 0.3)
    assert keys[0]["pulse"] == [0.0, 0.0]


def test_good_shrink_pulses_converges_to_zero_for_tiny_sizes():
    # 4-decimal rounding of a tiny profile size: every step is strictly smaller, so a pulse that keeps conflicting
    # reaches 0 and the shrink reports no change after that
    for zoom in (0.0001, 0.0013, 0.3):
        keys = [{"at_frame": 0}]
        sizes = []
        for _ in range(10):
            if not layout.shrink_pulses(keys, {0: (1, {0})}, zoom):
                break
            sizes.append(keys[0]["pulse"][0])
        assert sizes[-1] == 0.0 and len(sizes) <= len(layout.PULSE_STEPS)
        assert all(b < a for a, b in zip([zoom] + sizes, sizes))


def test_bad_shrink_pulses_below_floor_is_zero_and_zero_reports_no_change():
    keys = [{"at_frame": 0, "pulse": [layout.PULSE_FLOOR / 2]}]
    assert layout.shrink_pulses(keys, {0: (1, {0})}, 0.3) and keys[0]["pulse"] == [0.0]
    assert not layout.shrink_pulses(keys, {0: (1, {0})}, 0.3) and keys[0]["pulse"] == [0.0]


def test_good_label_move_frames_exact_frame_numbers():
    log = ["pin label 'A' moved at frame 10 (hard rule): above -> right",
           "pin label 'B' hidden at frame 37: no valid position",
           "pin label 'C' moved at frame 52: left -> below"]
    assert layout.label_move_frames(log) == {10, 37, 52}


def test_bad_label_move_frames_no_prefix_match():
    log = ["pin label 'A' moved at frame 100: above -> right", "beat at frame 10: content does not fit",
           "pin label 'B' uses a leader (below) from frame 10"]
    assert layout.label_move_frames(log) == {100}
    assert 10 not in layout.label_move_frames(log)


def test_good_planner_plan_has_no_layout_conflict_on_pulse_frames(project, monkeypatch):
    from test_layout import beats_doc
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    _profile(monkeypatch, "geoverum", camera=profiles.CameraProfile(pulse_zoom=0.3, pulse_gap_s=0.3,
                                                                     pulse_direction="in"))
    pl = layout._Planner(seg, tl, d)
    p = pl.run()
    assert pl.pulse_conflicts(p) == {}
    assert any(L["ease"] == "pulse" for L in p.cam["legs"])


def test_good_push_in_headroom_never_pushes_content_off_z2(project, monkeypatch):
    from test_layout import beats_doc
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    p0 = layout.apply_segment(seg.model_copy(deep=True), tl, d)
    _profile(monkeypatch, "geoverum", camera=profiles.CameraProfile(pulse_zoom=0.3, pulse_direction="in"))
    p1 = layout.apply_segment(seg.model_copy(deep=True), tl, d)
    assert all(b["zoom"] <= a["zoom"] + 1e-9 for a, b in zip(p0.keys, p1.keys))
    assert not [ln for ln in p1.log if "does not fit" in ln]


# P01 ---------------------------------------------------------------------------------------------------------------
def _p01(project, monkeypatch, camera, gate=profiles.CameraGate()):
    d, tl = project(tl_layout(("map", 6, None), profile="default"))
    prof = profiles.load()
    data = prof.data.model_copy(update={"camera": camera})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))
    sc = tl.segments[0].gsap_scene_config
    sc.camera_path = [timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], zoom=1.5)]
    return style.p01_camera(tl, d, gate)


def test_good_default_profile_runs_no_p01(project, W):
    d, tl = project(tl_layout(("map", 10, None), profile="default"))
    assert profiles.load().gates.camera is None
    assert "P01" not in {f.gate for f in style.run_style(tl, d, None)}


def test_bad_p01_drift_only_camera_holds_fail(project, monkeypatch):
    fs = _p01(project, monkeypatch, profiles.CameraProfile())
    assert "fail" in levels(fs, "P01") and any("holds 6.0 s" in f.message for f in fs)


def test_good_p01_pulsing_camera_passes(project, monkeypatch):
    fs = _p01(project, monkeypatch, profiles.CameraProfile(pulse_zoom=0.3, pulse_s=0.8, pulse_gap_s=0.3),
              profiles.CameraGate(moving_warn=0.5))
    assert levels(fs, "P01") == ["pass"] and "longest hold 0.4 s" in fs[0].message


def test_bad_p01_long_gaps_warn(project, monkeypatch):
    fs = _p01(project, monkeypatch, profiles.CameraProfile(pulse_zoom=0.3, pulse_s=0.8, pulse_gap_s=2.0))
    assert set(levels(fs, "P01")) == {"warn"}


def test_bad_camera_gate_order_rejected():
    with pytest.raises(ValidationError):
        profiles.CameraGate(hold_warn_s=3.0, hold_fail_s=2.0)


# [transitions] -----------------------------------------------------------------------------------------------------
GEO_T = dict(kind="zoom_whip", by_type={"map": "page_turn", "archive": "page_turn"})


def test_good_default_profile_has_no_transitions(project):
    _, tl = project(tl_layout(("map", 10, None), ("photo", 10, None), profile="default"))
    assert not profiles.load().transitions.active() and transitions.windows(tl) == []


def test_good_transition_windows_and_kind_by_visual_type(project, monkeypatch):
    _, tl = project(tl_layout(("map", 10, None), ("photo", 10, None), ("video", 10, None)))
    _profile(monkeypatch, transitions=profiles.TransitionsProfile(**GEO_T))
    ws = transitions.windows(tl)
    assert [(w.kind, w.f0, w.f1) for w in ws] == [("page_turn", 300, 312), ("zoom_whip", 597, 612)]


def test_good_same_type_map_to_map_whips_instead_of_the_page_turn(project, monkeypatch):
    _, tl = project(tl_layout(("map", 10, None), ("map", 10, None), ("photo", 10, None)))
    _profile(monkeypatch, transitions=profiles.TransitionsProfile(**GEO_T, same_type={"map": "zoom_whip"}))
    assert [transitions.kind_at(tl, i) for i in (1, 2)] == ["zoom_whip", "page_turn"]
    assert profiles.TransitionsProfile(same_type={"map": "zoom_whip"}).active()


def test_bad_transition_window_longer_than_a_segment_fails_c01(project, monkeypatch):
    _, tl = project(tl_layout(("photo", 10, None), ("photo", 0.3, None), ("photo", 10, None)))
    _profile(monkeypatch, transitions=profiles.TransitionsProfile(kind="zoom_whip"))
    assert transitions.fit_problems(tl)
    assert "fail" in levels(style.transition_findings(tl), "C01")
    with pytest.raises(EngineError, match="transitions"):
        transitions.render(tl, [], Path("."))


def test_bad_transitions_profile_values_rejected():
    with pytest.raises(ValidationError):
        profiles.TransitionsProfile(by_type={"cartoon": "page_turn"})
    with pytest.raises(ValidationError):
        profiles.TransitionsProfile(same_type={"cartoon": "zoom_whip"})
    with pytest.raises(ValidationError):
        profiles.TransitionsProfile(whip_blur_in_s=0.5, whip_in_s=0.2)


def test_good_whip_frames_push_in_blur_then_settle():
    T = profiles.TransitionsProfile(**GEO_T)
    rng = np.random.default_rng(1)
    a = rng.integers(0, 255, (64, 36, 3), np.uint8)
    b = rng.integers(0, 255, (64, 36, 3), np.uint8)
    fr = transitions.whip_frames([a] * 3, [b] * 12, T, FPS)
    assert len(fr) == 15
    assert np.array_equal(fr[-1], b)                       # settled: the incoming shot untouched
    assert not np.array_equal(fr[3], b) and not np.array_equal(fr[2], a)


def test_good_page_frames_reveal_the_incoming_shot():
    page = np.full((64, 36, 3), 200, np.uint8)
    under = np.full((64, 36, 3), 20, np.uint8)
    fr = transitions.page_frames(page, [under] * 12)
    covered = [float(np.mean(f == 200)) for f in fr]
    assert covered[0] > 0.9 and np.array_equal(fr[-1], under)
    assert all(b <= a + 1e-9 for a, b in zip(covered, covered[1:]))


def _clip(path, color, n):
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=32x32:r=30",
                    "-frames:v", str(n), "-c:v", "ffv1", "-pix_fmt", "bgr0", str(path)], check=True)
    return path


@pytest.mark.parametrize("concat", [False, True])
@pytest.mark.parametrize("f0", [0, 27, 50])
def test_good_overlay_graph_covers_exactly_the_clip_frames(tmp_path, concat, f0):
    """Base as a lossless file or as the mux's concat of h264 segments; the clip covers frames f0 .. f0 + 9."""
    tr = _clip(tmp_path / "tr.mkv", "blue", 10)
    if concat:
        seg = tmp_path / "seg.mp4"
        subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32:r=30",
                        "-frames:v", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)], check=True)
        (tmp_path / "c.txt").write_text(f"file '{seg}'\nfile '{seg}'\n")
        base = ["-f", "concat", "-safe", "0", "-i", str(tmp_path / "c.txt")]
    else:
        base = ["-i", str(_clip(tmp_path / "base.mkv", "red", 60))]
    chain, lab = transitions.overlay_graph([(tr, f0)], 1, FPS)
    out = subprocess.run([config.FFMPEG, "-v", "error", *base, "-i", str(tr), "-filter_complex", chain, "-map", lab,
                          "-r", "30", "-frames:v", "60", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    px = np.frombuffer(out, np.uint8).reshape(-1, 32, 32, 3)[:, 16, 16]
    blue = [i for i, p in enumerate(px) if p[2] > 200 and p[0] < 60]
    assert len(px) == 60 and blue == list(range(f0, f0 + 10))


def test_good_overlay_graph_empty_without_clips():
    assert transitions.overlay_graph([], 1, FPS) == ("", "[0:v]")


def test_good_final_mux_without_transitions_builds_the_same_command(project, monkeypatch, tmp_path):
    d, tl = project(tl_layout(("photo", 3, None), profile="default"))
    monkeypatch.setattr(audio.streams, "joinable", lambda paths: (True, []))
    cmd = lambda **kw: [str(c) for c in audio.composite_cmd(tl, [tmp_path / "s.mp4"], None, tmp_path / "o.mp4", 1, **kw)[0]]
    plain = [cmd(**kw) for kw in ({}, {"transitions": None}, {"transitions": []})]
    assert plain[0] == plain[1] == plain[2]
    clip = tmp_path / "t.mkv"
    with_t = cmd(transitions=[(clip, 12)])
    fc = with_t[with_t.index("-filter_complex") + 1]
    assert str(clip.resolve()) in with_t and "setpts=N+12" in fc and fc != plain[0][plain[0].index("-filter_complex") + 1]


# C01 with transitions ------------------------------------------------------------------------------------------------
def test_good_c01_transitions_are_not_hard_cuts(project, monkeypatch):
    _, tl = project(tl_layout(("map", 10, None), ("photo", 20, None), ("video", 15, None), ("photo", 15, None)))
    _profile(monkeypatch, "geoverum", transitions=profiles.TransitionsProfile(**GEO_T))
    fs = style.c01_cuts(tl, profiles.load("geoverum").gates.cuts)
    assert levels(fs, "C01") == ["pass", "pass"] and "0 hard cuts" in fs[0].message


def test_bad_c01_shot_change_rate_fails_with_transitions(project, monkeypatch):
    segs = [("map", 5, None)] + [("photo" if i % 2 else "video", 5, None) for i in range(11)]
    _, tl = project(tl_layout(*segs))
    _profile(monkeypatch, "geoverum", transitions=profiles.TransitionsProfile(**GEO_T))
    fs = style.c01_cuts(tl, profiles.load("geoverum").gates.cuts)
    assert levels(fs, "C01") == ["fail", "fail"] and "shot changes" in fs[0].message


def test_good_v01_names_the_transition(project, monkeypatch):
    _, tl = project(tl_layout(("photo", 10, None), ("photo", 10, None)))
    _profile(monkeypatch, "geoverum", transitions=profiles.TransitionsProfile(**GEO_T))
    fs = [f for f in style.run_style(tl, Path("."), None) if f.gate == "V01" and f.level != "pass"]
    assert fs and "zoom whip" in fs[0].message


# [layout.fly] -------------------------------------------------------------------------------------------------------
def test_good_geoverum_fly_values_reach_the_scene(project):
    from test_layout import beats_doc
    d, tl = project(beats_doc)
    p = layout.apply_segment(tl.segments[2], tl, d)
    fly = layout.scene_layout(p)["fly"]
    assert (fly["title_in"], fly["figure_in"], fly["label_in"], fly["out"]) == (2.6, 2.2, 3.0, 2.6)
    assert (fly["title_opacity_in_s"], fly["figure_opacity_in_s"]) == (0.15, 0.40)
    assert (fly["ease_in"], fly["ease_out"]) == ("expo.out", "power2.in")


def test_good_layout_without_fly_keeps_its_hash(tmp_path, monkeypatch):
    from video_engine.util import blake3_json
    geo = (config.PROFILES_DIR / "geoverum.toml").read_text()
    a, b = geo.index("[layout.fly]"), geo.index("ease_out")
    d = tmp_path / "p"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "nofly.toml").write_text(geo[:a] + geo[geo.index("\n", b) + 1:])
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    prof = profiles.load("nofly")
    assert prof.layout.fly is None
    dump = json.loads(prof.data.model_dump_json())
    assert dump["layout"].pop("fly") is None
    assert dump.pop("grade") is None          # an absent [grade] adds nothing either
    dump.pop("resources")                     # the worker budget is a machine setting, never hashed
    assert prof.blake3 == blake3_json(dump)   # an absent [layout.fly] adds nothing to the hash


def test_bad_fly_scale_below_one_rejected():
    with pytest.raises(ValidationError):
        layout.LayoutFly(title_in=0.5)


def test_good_template_fly_block_replaces_fades_only_with_ly_fly():
    html = (Path(hyperframes.__file__).parent / "template" / "index.html").read_text()
    assert "const show=(el,spans,kind)=>LY.fly?fly(el,spans,kind):fades(el,spans);" in html
    for call in ("show(lw,LY.els['label:'+i],'label')", "show(lw,LY.els['area:'+a.key],'label')",
                 "show(T,LY.els['title:0'],'title')", "show(el,LY.els['fig:'+i],'figure')"):
        assert call in html


# ---------------------------------------------------------------- counter-scale (template window.__inv, window.__legs)

def _js_helpers() -> str:
    src = (Path(config.ENGINE_ROOT) / "video_engine" / "template" / "index.html").read_text()
    a = src.index("window.__bez=function")
    b = src.index("</script>", a)
    g = json.dumps(str(config.GSAP_JS))
    return f"const window={{}}, G=require({g}), gsap=G.gsap||G.default;\n" + src[a:b]


def _node(body: str) -> list:
    r = subprocess.run(["node", "-e", _js_helpers() + "\n" + body], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_good_pulse_counter_scale_cancels_zoom_every_frame():
    # pulse leg zoom 1.0 -> exp(0.3) under the pulse bezier: camera scale x label scale = 1 at every step
    out = _node("""const b=[0.3,0,0.15,1], a=1.0, z=Math.exp(0.3), E=window.__bez(b), I=window.__inv(a,z,E);
      const r=[];for(let k=0;k<=20;k++){const p=k/20, cam=a+(z-a)*E(p), lab=1/a+(1/z-1/a)*I(p);r.push(cam*lab);}
      console.log(JSON.stringify(r));""")
    assert max(abs(v - 1) for v in out) < 1e-9


def test_bad_eased_reciprocal_drifts_from_zoom_mid_pulse():
    # the camera ease applied to 1/zoom leaves the label off its planned size mid-leg by > 1 %
    out = _node("""const b=[0.3,0,0.15,1], a=1.0, z=Math.exp(0.3), E=window.__bez(b);
      const p=0.3, cam=a+(z-a)*E(p), lab=1/a+(1/z-1/a)*E(p);console.log(JSON.stringify([cam*lab]));""")
    assert abs(out[0] - 1) > 0.01


def test_good_every_leg_counter_scale_cancels_zoom_every_frame():
    # window.__legs on 1 -> 4 -> 1.5 -> 3 -> 1: drift (linear), glide, whip (power3.inOut), pulse cubic-bezier; the
    # camera tween (from.s -> to.s, ease) x the counter-scale tween (1/from.s -> 1/to.s, inv) = 1 at every step
    out = _node("""const C={start:{s:1},legs:[{f0:0,f1:30,to:{s:4},ease:'drift'},{f0:30,f1:60,to:{s:1.5},ease:'glide'},
      {f0:60,f1:90,to:{s:3},ease:'whip'},{f0:90,f1:120,to:{s:1},ease:'drift',bez:[0.3,0,0.15,1]}]};
      console.log(JSON.stringify(window.__legs(C,30).map(g=>{const a=g.from.s, z=g.L.to.s, r=[];
        for(let k=0;k<=40;k++){const p=k/40;r.push((a+(z-a)*g.ease(p))*(1/a+(1/z-1/a)*g.inv(p)));}return r;})));""")
    assert len(out) == 4 and all(max(abs(v - 1) for v in r) < 1e-9 for r in out)


def test_good_legs_carry_the_camera_ease_and_timing():
    out = _node("""const C={start:{s:2},legs:[{f0:0,f1:30,to:{s:2},ease:'drift'},{f0:30,f1:45,to:{s:2.5},ease:'whip'}]};
      const g=window.__legs(C,30);console.log(JSON.stringify([g[0].ease(0.3),g[0].inv(0.3),g[1].ease(0.25),
        g[1].t0,g[1].d,g[1].from.s]));""")
    assert out == pytest.approx([0.3, 0.3, 0.03125, 1.0, 0.5, 2.0])          # flat leg: inv = the camera ease


def test_good_legs_endpoint_speed_ease_matches_engine():
    from video_engine import hyperframes
    legs = [{"f0": 0, "f1": 30, "to": {"s": 2.0}, "ease": "glide", "v": [0.75, 1.2]},
            {"f0": 30, "f1": 50, "to": {"s": 2.5}, "ease": "glide", "v": [0.0, 2.6]}]
    out = _node("const C=" + json.dumps({"start": {"s": 1.0}, "legs": legs}) + """;
      console.log(JSON.stringify(window.__legs(C,30).map(g=>[0,0.1,0.3,0.5,0.7,0.9,1].map(p=>[g.ease(p),g.inv(p)]))));""")
    for L, s0, got in zip(legs, (1.0, 2.0), out):
        e = hyperframes.leg_ease(L, s0)
        for p, (ge, gi) in zip((0, 0.1, 0.3, 0.5, 0.7, 0.9, 1), got):
            z = s0 + (L["to"]["s"] - s0) * ge
            assert ge == pytest.approx(e(p), abs=1e-12) and z * (1 / s0 + (1 / L["to"]["s"] - 1 / s0) * gi) == pytest.approx(1)
    assert [e(0.0), e(1.0)] == [0.0, 1.0] and (e(1e-6) - e(0)) / 1e-6 == pytest.approx(0.0, abs=1e-5)


@pytest.mark.parametrize("ease", ["glide", "whip"])   # drift is exponential: its reciprocal is exact
def test_bad_camera_ease_on_reciprocal_endpoints_oversizes_a_1_to_4_zoom(ease):
    # 1/a -> 1/z with the camera ease: at the middle of a 1 -> 4 leg camera 2.5 x label 0.625 = 1.5625
    out = _node(f"""const g=window.__legs({{start:{{s:1}},legs:[{{f0:0,f1:30,to:{{s:4}},ease:'{ease}'}}]}},30)[0];
      const p=0.5, cam=1+3*g.ease(p), lab=1+(0.25-1)*g.ease(p);console.log(JSON.stringify([cam*lab]));""")
    assert out[0] == pytest.approx(1.5625, abs=1e-9)


# ---------------------------------------------------------------- R63 reads fly-through texts at scale 1

def _fly_scene(fly=True):
    els = {"label:0": [[1.0, 0.35, 3.0, 0.3]], "title:0": [[1.2, 0.35, None, 0.0]], "area:Sekutu": [[2.0, 0.35, None, 0.0]],
           "fig:1": [[2.5, 0.35, None, 0.0]], "head:0": [[0.8, 0.6, None, 0.0]]}
    return {"layout": {"els": els, **({"fly": {"out": 2.6}} if fly else {})}}


def test_good_flying_ids_inside_entry_and_exit():
    from video_engine.gates import layer2
    sc = _fly_scene()
    assert layer2.flying(sc, 1.1) == {"pin:0"}
    assert layer2.flying(sc, 1.3) == {"pin:0", "title"}
    assert layer2.flying(sc, 1.6) == set()                       # settled: scale 1
    assert layer2.flying(sc, 2.1) == {"area:Sekutu"}
    assert layer2.flying(sc, 2.6) == {"figure:1"}
    assert layer2.flying(sc, 3.1) == {"pin:0"}                   # exit
    assert "pinhead:0" not in layer2.flying(sc, 0.9)              # heads do not fly


def test_bad_no_fly_marks_nothing():
    from video_engine.gates import layer2
    assert layer2.flying(_fly_scene(fly=False), 1.1) == set()
    assert layer2.flying({}, 1.1) == set()


def _probe(t):
    big = {"id": "pin:0", "text": "Jembatan Merah", "x": 20, "y": 600, "w": 700, "h": 100, "opacity": 0.6}
    return {"times": [{"t": t, "rects": [dict(big)]}]}


def test_good_r63_skips_texts_in_flight():
    from video_engine.gates import layer2
    res = _probe(1.1)
    layer2.mark_flying(res, _fly_scene())
    assert layer2.check_rects(res["times"][0]["rects"], 1080, 1920, 1.1, 0) == []


def test_bad_r63_fails_settled_or_unflown_text_outside_safe_area():
    from video_engine.gates import layer2
    for t, sc in ((1.6, _fly_scene()), (1.1, _fly_scene(fly=False))):
        res = _probe(t)
        layer2.mark_flying(res, sc)
        fs = layer2.check_rects(res["times"][0]["rects"], 1080, 1920, t, 0)
        assert [f.level for f in fs] == ["fail"] and "outside safe area" in fs[0].message


def test_bad_planner_conflicting_pulse_is_detected_and_only_it_shrinks(project, monkeypatch):
    # a label move on one frame of one away pulse: the planner detects that pulse and reduces it alone to drift
    from test_layout import beats_doc
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    # print=None: [print] place names are larger and already make every pulse of this fixture wait conflict
    _profile(monkeypatch, "geoverum", camera=profiles.CameraProfile(pulse_zoom=0.3, pulse_gap_s=0.3,
                                                                     pulse_direction="in"), print=None)
    base = layout._Planner(seg, tl, d).run()
    ats = [k["at_frame"] for k in base.keys]
    away = [(max(j for j, a in enumerate(ats) if a <= L["f0"]), L["away"], L["f0"])
            for L in base.cam["legs"] if L.get("ease") == "pulse" and "away" in L]
    assert away, "fixture wait has pulses"
    size = lambda keys, j, n: (lambda c: c[min(n, len(c) - 1)] if isinstance(c, list) else c)(
        keys[j].get("pulse", 0.3))
    n_away = {}
    for j, n, _ in away:
        n_away[j] = max(n_away.get(j, 0), n + 1)
    for j, k in enumerate(base.keys):            # pulses the planner already shrank to 0 have no away leg
        if isinstance(k.get("pulse"), list):
            n_away[j] = max(n_away.get(j, 0), len(k["pulse"]))
    # a full-size pulse of a wait with several pulses
    i, m, f0 = next((j, n, f) for j, n, f in away if n_away[j] > 1 and size(base.keys, j, n) > 0.2)
    frame = f0 + 2
    real = layout.label_move_frames
    monkeypatch.setattr(layout, "label_move_frames", lambda log: real(log) | {frame})
    seen = []
    orig = layout._Planner.pulse_conflicts
    monkeypatch.setattr(layout._Planner, "pulse_conflicts", lambda self, pp: seen.append(orig(self, pp)) or seen[-1])
    p = layout._Planner(seg, tl, d).run()
    assert any(i in b and m in b[i][1] for b in seen) and seen[-1] == {}       # detected, then resolved
    assert size(p.keys, i, m) == 0.0
    assert [(j, n) for j in n_away for n in range(n_away[j])
            if size(p.keys, j, n) != size(base.keys, j, n)] == [(i, m)]       # only that pulse changed


def test_good_planner_plan_has_no_layout_conflict_on_drift_frames(project, monkeypatch):
    from test_layout import beats_doc
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    _profile(monkeypatch, "geoverum")
    pl = layout._Planner(seg, tl, d)
    p = pl.run()
    assert pl.drift_conflicts(p) == {}
    assert any(L["ease"] == "drift" for L in p.cam["legs"])


def test_bad_planner_drift_conflict_bounds_only_that_wait(project, monkeypatch):
    # a zone break on one frame inside one wait's drift (not on its first frame): only that key gets a drift bound
    from test_layout import beats_doc
    from video_engine.gates import layout_gates
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    _profile(monkeypatch, "geoverum")
    base = layout._Planner(seg, tl, d).run()
    assert all("drift_max" not in k for k in base.keys)
    ats = [k["at_frame"] for k in base.keys]
    leg = next(L for L in base.cam["legs"] if L["ease"] == "drift" and "away" not in L and L["f1"] - L["f0"] > 12)
    i = max(j for j, a in enumerate(ats) if a <= leg["f0"])
    bad = leg["f0"] + 6
    real_zones, real_items, cur = layout_gates.zones, layout.frame_items, {}
    monkeypatch.setattr(layout, "frame_items", lambda pp, f: cur.update(f=f, moving=bool(pp.cam["legs"]))
                        or real_items(pp, f))
    monkeypatch.setattr(layout_gates, "zones", lambda items, L, W, H: ["fake"] if cur["f"] == bad and cur["moving"]
                        else real_zones(items, L, W, H))
    p = layout._Planner(seg, tl, d).run()
    assert p.keys[i].get("drift_max") == layout.DRIFT_MIN
    assert [j for j, k in enumerate(p.keys) if "drift_max" in k] == [i]


# P01 measures the 2D frame-centre motion ---------------------------------------------------------------------------
def _motion(project, keys):
    d, tl = project(tl_layout(("map", 4, None), profile="default"))
    tl.segments[0].gsap_scene_config.camera_path = [timeline.CameraKey(**k) for k in keys]
    return style.motion_frames(tl, d)


def test_good_p01_vertical_pan_counts_as_moving(project):
    mf = _motion(project, [{"at_frame": 0, "focus": [0.5, 0.3], "zoom": 1.5},
                           {"at_frame": 60, "focus": [0.5, 0.7], "zoom": 1.5}])
    g = profiles.CameraGate()
    mid = mf[20:40]
    assert all(z < g.still_zoom_ln_s and pan >= g.still_pan_w_s for z, pan in mid)


def test_bad_p01_true_hold_counts_as_still(project):
    mf = _motion(project, [{"at_frame": 0, "focus": [0.5, 0.3], "zoom": 1.5},
                           {"at_frame": 60, "focus": [0.5, 0.3], "zoom": 1.5}])
    g = profiles.CameraGate()
    assert all(z < g.still_zoom_ln_s and pan < g.still_pan_w_s for z, pan in mf[5:110])


def test_good_p01_native_vertical_window_pan_counts_as_moving(project, monkeypatch):
    import hypoverum_core_rs as core
    d, tl = project(tl_layout(("photo", 2, None), profile="default"))
    n = tl.segments[0].n_frames
    monkeypatch.setattr(core, "camera_windows", lambda a, b, k: [(100.0, 100.0 + 8 * f, 900.0, 1600.0) for f in range(k)])
    assert all(pan == pytest.approx(8 / 900 * FPS) for _, pan in style.motion_frames(tl, d)[:n - 1])
    monkeypatch.setattr(core, "camera_windows", lambda a, b, k: [(100.0, 100.0, 900.0, 1600.0)] * k)
    assert all(z == 0 and pan == 0 for z, pan in style.motion_frames(tl, d)[:n - 1])


# R63 fly skip is bounded to each span's entry and exit --------------------------------------------------------------
def test_good_flying_text_that_exits_and_reenters():
    from video_engine.gates import layer2
    sc = {"layout": {"fly": {"out": 2.6}, "els": {"label:0": [[1.0, 0.35, 2.0, 0.3], [4.0, 0.35, None, 0.0]]}}}
    assert layer2.flying(sc, 2.1) == {"pin:0"}          # first exit
    assert layer2.flying(sc, 4.1) == {"pin:0"}          # re-entry


def test_bad_text_after_exit_end_is_not_skipped_when_it_reenters():
    from video_engine.gates import layer2
    sc = {"layout": {"fly": {"out": 2.6}, "els": {"label:0": [[1.0, 0.35, 2.0, 0.3], [4.0, 0.35, None, 0.0]]}}}
    assert layer2.flying(sc, 3.0) == set()               # between the exit end and the re-entry
    assert layer2.flying(sc, 4.6) == set()               # settled again after re-entry: R63 checks it
    res = {"times": [{"t": 4.6, "rects": [{"id": "pin:0", "text": "A", "x": 20, "y": 600, "w": 700, "h": 100,
                                           "opacity": 1.0}]}]}
    layer2.mark_flying(res, sc)
    assert [f.level for f in layer2.check_rects(res["times"][0]["rects"], 1080, 1920, 4.6, 0)] == ["fail"]

