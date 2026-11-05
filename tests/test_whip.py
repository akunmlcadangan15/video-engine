"""camera_path move "whip": drift-then-fast in-out leg with directional motion blur; never a cut."""
import numpy as np
import pytest
from PIL import Image

from video_engine import draft, hyperframes, profiles
from video_engine.gates import layer2, style

K0 = {"at_frame": 0, "focus": [0.3, 0.4], "zoom": 1.5}
WHIP = {"at_frame": 45, "focus": [0.7, 0.4], "zoom": 1.5, "move": "whip"}


def _path(keys):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc["camera_path"] = keys
        sc["pins"] = [{"label": "Pusat", "x": 0.7, "y": 0.4, "at_frame": 0}]
    return mut


def _theme(monkeypatch, **kw):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=kw)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))


def _edge(png):
    """Strongest horizontal edges (99.9th percentile of neighbour steps): a blur smears steps over many pixels."""
    a = np.asarray(Image.open(png).convert("L"), dtype=np.float32)
    return float(np.percentile(np.abs(np.diff(a, axis=1)), 99.9))


# good ---------------------------------------------------------------------------------------------------------------
def test_good_whip_waits_with_drift_then_moves_fast_into_the_key(project):
    p, tl = project(_path([K0, WHIP]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    th = sc["theme"]
    w = round(th["whip_s"] * 30)
    legs = sc["camera"]["legs"]
    assert [(L["f0"], L["f1"], L["ease"]) for L in legs] == [(0, 45 - w, "drift"), (45 - w, 45, "whip"), (45, 60, "drift")]
    assert legs[0]["to"]["s"] > sc["camera"]["start"]["s"]           # the camera keeps pushing in while it waits
    sx, sy = legs[1]["blur"]
    assert sx == pytest.approx(th["whip_blur_px"]) and sy == pytest.approx(th["whip_blur_px"] / 4)  # horizontal travel


def test_good_whip_in_out_easing_and_lands_on_the_key(project, tmp_path):
    """The pin under the whip key focus barely moves in the first fifth of the whip, is nearly there at four fifths
    and sits at the frame centre on the key frame."""
    p, tl = project(_path([K0, WHIP]))
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    L = sc["camera"]["legs"][1]
    t0, t1 = L["f0"] / 30, L["f1"] / 30
    ts = [t0, t0 + 0.2 * (t1 - t0), t0 + 0.8 * (t1 - t0), t1]
    xs = [next(r["x"] for r in item["rects"] if r["id"] == "anchor:pin:0")
          for item in hyperframes.probe_dom(d, ts, 1080, 1920)["times"]]
    frac = [(x - xs[0]) / (xs[3] - xs[0]) for x in xs]
    assert frac[1] < 0.1 and frac[2] > 0.9 and xs[3] == pytest.approx(540, abs=0.5)


def test_good_whip_blurs_mid_move_and_is_sharp_on_the_key(project, tmp_path, monkeypatch):
    """Mid-whip the edges are smeared against the same scene without blur; on the key frame the two are identical.
    Each time is probed in a fresh page (Chrome re-rasters a seeked-back will-change layer at a different quality)."""
    p, tl = project(_path([K0, WHIP]))
    L = hyperframes.scene_data(tl.segments[2], tl, p)["camera"]["legs"][1]
    shots = {}
    for name, blur in (("blur", 14.0), ("plain", 0.0)):
        _theme(monkeypatch, whip_blur_px=blur)
        d, _ = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / name)
        shots[name] = [hyperframes.probe_dom(d, [t], 1080, 1920, tmp_path / f"snap_{name}_{i}")["times"][0]["png"]
                       for i, t in enumerate(((L["f0"] + L["f1"]) / 60, L["f1"] / 30))]
    assert _edge(shots["blur"][0]) < 0.4 * _edge(shots["plain"][0])
    assert np.array_equal(np.asarray(Image.open(shots["blur"][1])), np.asarray(Image.open(shots["plain"][1])))


def test_good_whip_is_not_a_cut_and_R56_checks_it(project):
    p, tl = project(_path([K0, WHIP]))
    _, tl0 = project()
    assert style.hard_cuts(tl) == style.hard_cuts(tl0)
    r = [f for f in layer2.r56_camera(tl, p)[0] if f.seg == 2]
    assert [f.level for f in r] == ["pass"] and "1 legs" in r[0].message


def test_good_short_leg_whips_over_the_whole_leg(project):
    p, tl = project(_path([K0, {**WHIP, "at_frame": 10}]))
    legs = hyperframes.scene_data(tl.segments[2], tl, p)["camera"]["legs"]
    assert [(L["f0"], L["f1"], L["ease"]) for L in legs] == [(0, 10, "whip"), (10, 60, "drift")]


def test_good_glide_and_preset_legs_carry_no_blur(project):
    """The template builds the blur lens only when a leg carries `blur`, so scenes without a whip render as before."""
    for mut in (None, _path([K0, {**WHIP, "move": "glide"}])):
        p, tl = project(mut) if mut else project()
        assert not any("blur" in L for L in hyperframes.scene_data(tl.segments[2], tl, p)["camera"]["legs"])


def test_good_draft_keeps_whip_moves():
    keys = draft._camera_path([{"at_s": 0, "x": 0.2, "y": 0.2, "move": "whip"},
                               {"at_s": 1.0, "x": 0.8, "y": 0.2, "move": "whip"},
                               {"at_s": 2.0, "x": 0.5, "y": 0.5, "move": "spin"}], 90, 30)
    assert [k.get("move") for k in keys] == [None, "whip", None]


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_unknown_move(project):
    from video_engine.util import EngineError
    with pytest.raises(EngineError, match=r"invalid values \(segments\.2\.gsap_scene_config\.camera_path\.1\.move:"):
        project(_path([K0, {**WHIP, "move": "spin"}]))


def test_bad_whip_without_travel_fails_R56(project):
    from video_engine.timeline import SegmentIntent
    p, tl = project(_path([K0, {**WHIP, "focus": [0.301, 0.4]}]))
    assert any(f.level == "warn" for f in layer2.r56_camera(tl, p)[0] if f.seg == 2)
    tl.segments[2].intent = SegmentIntent(motion="moving", reason="Expected whip travel")
    assert any(f.level == "fail" and "leg 1" in f.message for f in layer2.r56_camera(tl, p)[0] if f.seg == 2)
