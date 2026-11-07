"""Native components (stage E): camera roll, thermometer, parametric flow. Model rules, scene data, and real renders:
roll keeps the map over every corner, the thermometer's fill height, colour and number come from one value, and a
flow moves with render time (seek), identically on every run."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import components, hyperframes
from video_engine.timeline import CameraKey, Flow, GsapScene, Thermometer

BG = np.array([0x0B, 0x11, 0x18])


def _shot(project, tmp_path, mut, times, name="a"):
    p, tl = project(mut)
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / f"hf_{name}")
    res = hyperframes.probe_dom(d, list(times), 1080, 1920, tmp_path / f"snap_{name}")
    return [np.asarray(Image.open(r["png"]).convert("RGB")).astype(int) for r in res["times"]], sc, res


def _scene(**kw):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(pins=[], cards=[], **kw)
    return mut


def _path(n, roll):
    return [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.3}, {"at_frame": n - 1, "focus": [0.52, 0.5], "zoom": 1.5,
                                                                 "roll": roll}]


# roll ---------------------------------------------------------------------------------------------------------------
def test_bad_roll_range():
    with pytest.raises(ValidationError):
        CameraKey(at_frame=0, focus=[0.5, 0.5], roll=46)   # ROLL_MAX is 45: banking needs the full range


def test_good_no_roll_scene_unchanged(project):
    p, tl = project(_scene(camera_path=_path(tl_n(project), 0.0)))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert "r" not in sc["camera"]["start"] and all("r" not in L["to"] for L in sc["camera"]["legs"])


def tl_n(project):
    return project()[1].segments[2].n_frames


def test_good_roll_legs_and_corners_covered(project, tmp_path):
    n = tl_n(project)
    shots, sc, _ = _shot(project, tmp_path, _scene(camera_path=_path(n, 18.0)), [0.0, (n - 1) / 30])
    assert sc["camera"]["start"]["r"] == 0.0 and sc["camera"]["legs"][0]["to"]["r"] == 18.0
    end = shots[1]
    for y, x in [(2, 2), (2, 1077), (1917, 2), (1917, 1077)]:          # no background showing in a corner
        assert np.abs(end[y, x] - BG).max() > 12, (y, x, end[y, x])
    assert np.abs(shots[0] - end).mean() > 5                         # the frame really turned


# thermometer --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw, msg", [({"value": 60}, "within"), ({"min": 10, "max": 5, "value": 7}, "< max"),
                                     ({"value": 20, "position": [1.2, 0.5]}, "within 0..1")])
def test_bad_thermometer(kw, msg):
    with pytest.raises(ValidationError, match=msg):
        Thermometer(**kw)


@pytest.mark.parametrize("value", [12.0, 38.0])
def test_good_thermometer_one_value_drives_fill_colour_and_number(project, tmp_path, value):
    T = {"value": value, "min": 0, "max": 50, "label": "Suhu", "note": "ilustratif", "fill_s": 0.6,
         "position": [0.5, 0.5], "height_pct": 40}
    shots, sc, res = _shot(project, tmp_path, _scene(thermometers=[T]), [2.0], name=str(value))
    th = sc["thermometers"][0]
    model = Thermometer(**T)
    frac = components.thermo_fraction(model)
    want = np.array([int(components.thermo_colour(model, frac)[k:k + 2], 16) for k in (1, 3, 5)])
    img, cx = shots[0], round(th["x"] + th["w"] / 2)
    tube_h = th["h"] - th["bulb_d"] * 0.85 + th["bulb_d"] * 0.3
    top, bottom = th["y"], th["y"] + tube_h
    col = img[round(top):round(bottom), cx]
    hits = np.where(np.abs(col - want).max(axis=1) < 30)[0]
    assert len(hits), "fill colour present in the tube"
    fill_top = top + hits.min()
    expect = bottom - frac * tube_h
    assert abs(fill_top - expect) < 0.06 * tube_h, (fill_top, expect)
    num_y = round(th["y"] - 0.08 * th["h"] - 0.08 * th["h"])         # the number sits above the tube: white glyphs
    strip = img[num_y - 30:num_y + 30, round(th["x"]) - 60:round(th["x"] + th["w"]) + 60]
    assert (strip.min(axis=2) > 225).sum() > 150, "the thermometer number is drawn"
    text = next(r for r in res["times"][0]["rects"] if r["id"] == "thermo:0")["text"]
    assert f"{int(value)}°C" in text.replace(" ", "") and "ilustratif" in text


# flow ---------------------------------------------------------------------------------------------------------------
def test_bad_flow():
    with pytest.raises(ValidationError, match="at least 2 points"):
        Flow(paths=[[[0.1, 0.1]]])
    with pytest.raises(ValidationError, match="within 0..1"):
        Flow(paths=[[[0.1, 0.1], [1.4, 0.2]]])
    with pytest.raises(ValidationError, match="globe"):
        GsapScene(map_asset="w.jpg", flows=[{"paths": [[[0.1, 0.1], [0.2, 0.2]]]}],
                  globe={"keys": [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 2}]})


def test_good_flow_seed_is_deterministic():
    f = Flow(paths=[[[0.1, 0.1], [0.5, 0.5]], [[0.2, 0.8], [0.6, 0.3]]], seed=3)
    a, b = components.flow_data(f, 1000, 1000, 42), components.flow_data(f, 1000, 1000, 42)
    assert a == b and a["phase"] != components.flow_data(f, 1000, 1000, 43)["phase"]
    assert components.flow_offset(a, 0, 1.0, 30) != components.flow_offset(a, 0, 1.5, 30)


def test_good_flow_moves_with_render_time_and_repeats_exactly(project, tmp_path):
    n = tl_n(project)
    F = {"paths": [[[0.2, 0.5], [0.8, 0.5]]], "at_frame": 0, "reveal_s": 0.2, "width_px": 14, "speed_px": 200,
         "label": "Arus (ilustrasi)", "colour": "#ff3df0"}
    hold = [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.2}, {"at_frame": n - 1, "focus": [0.5, 0.5], "zoom": 1.2}]
    a, sc, res = _shot(project, tmp_path, _scene(camera_path=hold, flows=[F]), [1.0, 1.1], name="a")
    b, _, _ = _shot(project, tmp_path, _scene(camera_path=hold, flows=[F]), [1.0], name="b")
    assert sc["flows"][0]["label"] == "Arus (ilustrasi)" and any(r["id"] == "legend" for r in res["times"][0]["rects"])
    plain, _, _ = _shot(project, tmp_path, _scene(camera_path=hold), [1.0], name="n")
    cam, (mw, mh) = sc["camera"]["start"], sc["map_size"]
    y = round(0.5 * mh * cam["s"] + cam["y"])
    x0, x1 = round(0.25 * mw * cam["s"] + cam["x"]), round(0.75 * mw * cam["s"] + cam["x"])
    band = lambda im: np.abs(im[y - 4:y + 5, x0:x1] - plain[0][y - 4:y + 5, x0:x1]).max(axis=2) > 40
    m0, m1 = band(a[0]), band(a[1])
    assert 0.25 < m0.mean() < 0.75                          # dashes and gaps along the stream
    assert (m0 ^ m1).mean() > 0.15                          # 0.1 s later the dashes moved
    assert np.array_equal(a[0], b[0])                       # same seek time, separate page: identical frame


# split screen -------------------------------------------------------------------------------------------------------
def _split(n, second_focus=(0.8, 0.2)):
    second = {"map_asset": "assets/map.jpg", "map_asset_id": "map", "pins": [], "cards": [],
              "camera_path": [{"at_frame": 0, "focus": list(second_focus), "zoom": 2.0},
                              {"at_frame": n - 1, "focus": [second_focus[0] - 0.05, second_focus[1]], "zoom": 2.4}]}
    return _scene(camera_path=[{"at_frame": 0, "focus": [0.2, 0.8], "zoom": 2.0},
                               {"at_frame": n - 1, "focus": [0.25, 0.8], "zoom": 2.4}],
                  split={"second": second, "divider_colour": "#ffffff"})


def test_bad_split_rules():
    cam = [{"at_frame": 0, "focus": [0.5, 0.5]}, {"at_frame": 9, "focus": [0.4, 0.5]}]
    with pytest.raises(ValidationError, match="bottom panel needs its own camera_path"):
        GsapScene(map_asset="m.jpg", camera_path=cam, split={"second": {"map_asset": "m.jpg"}})
    with pytest.raises(ValidationError, match="multiple of 2"):
        GsapScene(map_asset="m.jpg", camera_path=cam, split={"second": {"map_asset": "m.jpg", "camera_path": cam},
                                                             "divider_px": 7})


def test_good_split_panels_keys_and_credits(project):
    from video_engine.gates.layer1 import visual_refs
    n = tl_n(project)
    p, tl = project(_split(n))
    seg = tl.segments[2]
    (a, ta), (b, tb) = hyperframes.split_panels(seg, tl)
    assert ta.settings.height == tb.settings.height == (1920 - 8) // 2 and a.gsap_scene_config.split is None
    assert b.gsap_scene_config.camera_path[0].focus == [0.8, 0.2]
    k1 = hyperframes.keys(seg, tl, p)[0]
    p2, tl2 = project(_split(n, (0.7, 0.3)))
    assert hyperframes.keys(tl2.segments[2], tl2, p2)[0] != k1           # the bottom panel is in the key
    assert sum(1 for r in visual_refs(tl) if r[0] == 2 and r[3] == "map") == 2


def test_good_split_render_two_cameras_and_divider(project, tmp_path):
    from video_engine import streams
    n = tl_n(project)
    p, tl = project(_split(n))
    out, hit = hyperframes.render(tl.segments[2], tl, p, tmp_path / "w", workers=2, use_cache=False)
    assert not hit
    streams.verify(out, n, 30, "split")
    import subprocess
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
    assert img[956:964].mean() > 235                                       # white divider between the panels
    top, bottom = img[:956], img[964:]
    assert np.abs(top - bottom).mean() > 20                                # two different views of the map
    a_only, _ = hyperframes.render(hyperframes.split_panels(tl.segments[2], tl)[0][0], hyperframes.split_panels(
        tl.segments[2], tl)[0][1], p, tmp_path / "w2", workers=2, use_cache=False)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(a_only), "-frames:v", "1", "-f",
                          "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert np.abs(np.frombuffer(raw, np.uint8).reshape(956, 1080, 3).astype(int) - top).mean() < 4   # top = panel A


def test_good_split_dom_gate_probes_each_panel(project, tmp_path):
    from video_engine.gates import layer2
    n = tl_n(project)

    def mut(d):
        _split(n)(d)
        d["segments"][2]["gsap_scene_config"]["split"]["second"]["pins"] = [{"label": "B", "x": 0.8, "y": 0.2}]
    p, tl = project(mut)
    fs, dom = layer2.r63_dom(tl, p, tmp_path / "w", {})
    assert 2 in dom and any(f.seg == 2 for f in fs)
    assert (tmp_path / "w" / "hf" / "seg002_t").is_dir() and (tmp_path / "w" / "hf" / "seg002_b").is_dir()


@pytest.mark.parametrize("low, high, frac, want", [("#000000", "#010101", 0.5, "#010101"),
                                                   ("#000000", "#050505", 0.5, "#030303"),
                                                   ("#0a0a0a", "#0b0b0b", 0.5, "#0b0b0b")])
def test_good_thermo_colour_rounds_half_up_like_the_template(low, high, frac, want):
    """The template mixes with Math.round (half up); Python's round() is half-to-even and disagreed on .5 steps."""
    from types import SimpleNamespace
    assert components.thermo_colour(SimpleNamespace(colour_low=low, colour_high=high), frac) == want
