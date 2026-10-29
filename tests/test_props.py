"""Props layer (gsap_scene_config.props, beats[].props): model, scene math (props.state), the hf/props.js render on
the shared camera legs (probed in headless Chrome), the F01-F04 gates, T02 timing, R07 credits, the caption
avoid-list and the segment key."""
import json

import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, props, timeline
from video_engine.gates import layer1, layer2, props_gates

URL = "https://example.org/asset/prop"


def _png(path, size=(400, 300), alpha=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if alpha:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        w, h = size
        im.paste(Image.new("RGBA", (w // 2, h // 2), (200, 60, 40, 255)), (w // 4, h // 4))
    else:
        im = Image.new("RGB", size, (200, 60, 40))
    im.save(path)
    return path


def _doc(*ps, credit=True, preset="zoom_in", path="assets/prop.png"):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["props"] = list(ps)
        d["segments"][2]["gsap_scene_config"]["camera_preset"] = preset
        if credit:
            d["credits_manifest"].append({"asset_id": "prop", "path": path, "source_url": URL,
                                          "license": "CC0-1.0", "watermark_free": True})
    return mut


def _proj(project, *ps, size=(400, 300), alpha=True, **kw):
    d, _ = project(_doc(*ps, **kw))
    _png(d / "assets" / "prop.png", size, alpha)
    return d, timeline.load(d)


MAP_PROP = {"asset": "assets/prop.png", "asset_id": "prop", "x": 0.5, "y": 0.45, "width_px": 200}
SCREEN_PROP = {"asset": "assets/prop.png", "asset_id": "prop", "anchor": "screen", "x": 540, "y": 700, "width_px": 200}


def _fs(d, tl, gate, events=(), dom=None):
    return [f for f in props_gates.run(tl, d, list(events), dom or {}) if f.gate == gate]


# model -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("p", [MAP_PROP, SCREEN_PROP, {**MAP_PROP, "x": None, "y": None, "lat": 40.0, "lng": 50.0},
                               {**MAP_PROP, "width_px": None, "width_map": 0.1, "depth": 0.5,
                                "enter": {"kind": "slide", "dir": "left"}, "exit": {"kind": "pop"},
                                "idle": {"kind": "bob", "amplitude": 6, "period_s": 2}, "shadow": {}}])
def test_good_prop_values(p):
    timeline.Prop(**{k: v for k, v in p.items() if v is not None})


@pytest.mark.parametrize("p", [{**MAP_PROP, "asset": "assets/prop.jpg"}, {**MAP_PROP, "width_map": 0.1},
                               {**MAP_PROP, "width_px": None}, {**SCREEN_PROP, "y": None},
                               {**SCREEN_PROP, "depth": 0.5}, {**MAP_PROP, "x": 1.5},
                               {**MAP_PROP, "t_in": 30, "t_out": 30}, {**MAP_PROP, "enter": {"kind": "fade", "dir": "up"}},
                               {**MAP_PROP, "lat": 40.0, "lng": 50.0}])
def test_bad_prop_values_rejected(p):
    with pytest.raises(ValidationError):
        timeline.Prop(**{k: v for k, v in p.items() if v is not None})


# scene math --------------------------------------------------------------------------------------------------------
def _cam(s0=1.0, s1=2.0, n=60):
    at = lambda s: {"x": 540 - s * 750, "y": 960 - s * 800, "s": s}
    return {"start": at(s0), "legs": [{"f0": 0, "f1": n, "to": at(s1), "ease": "glide"}]}


def _entry(**kw):
    e = {"id": "prop:0", "size": [400, 300], "ink": [0.25, 0.25, 0.75, 0.75], "anchor": "map", "f_in": 0, "f_out": 60,
         "rot": 0.0, "op": 1.0, "z": 0, "m": [750.0, 800.0], "d": 0.0, "w": 200.0}
    e.update(kw)
    return e


def test_good_map_prop_follows_the_map_and_scales_with_the_zoom():
    cam = _cam()
    for f in (0, 30, 59):
        st = props.state(_entry(m=[900.0, 800.0]), cam, f, 1080, 1920)
        from video_engine import layout
        c = layout.camera_at(cam, f)
        assert (st["x"], st["y"]) == pytest.approx((c["x"] + c["s"] * 900, c["y"] + c["s"] * 800), abs=1e-6)
        assert st["w"] == pytest.approx(200 * c["s"], rel=1e-9)


def test_good_depth_prop_grows_faster_than_the_map():
    cam = _cam()
    a = props.state(_entry(d=0.0), cam, 59, 1080, 1920)["w"]
    b = props.state(_entry(d=0.5), cam, 59, 1080, 1920)["w"]
    assert b > a * 1.2


def test_good_screen_prop_ignores_the_camera():
    e = _entry(anchor="screen", p=[540, 700], d=0.0)
    assert props.state(e, _cam(), 0, 1080, 1920) == props.state(e, _cam(), 59, 1080, 1920)


def test_good_pop_enter_and_fade_exit():
    e = _entry(enter={"kind": "pop", "f": 10, "ease": "glide"}, exit={"kind": "fade", "f": 10, "ease": "drift"})
    cam = {"start": _cam()["start"], "legs": []}
    assert props.state(e, cam, 0, 1080, 1920)["w"] == 0
    assert props.state(e, cam, 10, 1080, 1920)["w"] == pytest.approx(200)
    assert props.state(e, cam, 55, 1080, 1920)["opacity"] == pytest.approx(0.5)
    assert props.state(e, cam, 60, 1080, 1920) is None


# render: hf/props.js on the shared camera legs ---------------------------------------------------------------------
def test_good_rendered_props_match_props_state_through_a_zoom(project, tmp_path):
    d, tl = _proj(project, MAP_PROP, {**MAP_PROP, "x": 0.4, "depth": 0.5, "z": 2}, SCREEN_PROP)
    seg = tl.segments[2]
    out, sc = hyperframes.build_dir(seg, tl, d, tmp_path / "hf")
    assert (out / "props.js").is_file() and all((out / e["img"]).is_file() for e in sc["props"])
    fps = sc["fps"]
    frames = [0, 15, 30, 45, 59]
    res = hyperframes.probe_props(out, [f / fps for f in frames], 1080, 1920)
    for f, item in zip(frames, res["times"]):
        got = {p["id"]: p for p in item["props"]}
        for e in sc["props"]:
            st = props.state(e, sc["camera"], f, 1080, 1920)
            g = got[e["id"]]
            assert g["visible"]
            assert (g["x"], g["y"]) == pytest.approx((st["x"], st["y"]), abs=1.0), (f, e["id"])
            assert g["bw"] == pytest.approx(st["w"], abs=1.0)


def test_bad_rendered_prop_outside_its_frames_is_hidden(project, tmp_path):
    d, tl = _proj(project, {**MAP_PROP, "t_in": 20, "t_out": 40})
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_props(out, [10 / sc["fps"], 30 / sc["fps"], 50 / sc["fps"]], 1080, 1920)
    assert [it["props"][0]["visible"] for it in res["times"]] == [False, True, False]


# segment key ---------------------------------------------------------------------------------------------------------
def test_good_segment_key_follows_prop_bytes_and_params(project):
    d, tl = _proj(project, MAP_PROP)
    k0 = hyperframes.keys(tl.segments[2], tl, d)[0]
    assert hyperframes.keys(tl.segments[2], tl, d)[0] == k0
    im = Image.open(d / "assets" / "prop.png"); im.putpixel((0, 0), (1, 2, 3, 4)); im.save(d / "assets" / "prop.png")
    k1 = hyperframes.keys(tl.segments[2], tl, d)[0]
    tl.segments[2].gsap_scene_config.props[0].rotation = 10.0
    k2 = hyperframes.keys(tl.segments[2], tl, d)[0]
    assert len({k0, k1, k2}) == 3


def test_bad_segment_without_props_has_no_props_script(project, tmp_path):
    d, tl = project()
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    assert "props" not in sc and not (out / "props.js").exists()
    assert "props.js" not in (out / "index.html").read_text()


# F01 alpha -----------------------------------------------------------------------------------------------------------
def test_good_f01_png_with_alpha_passes(project):
    d, tl = _proj(project, MAP_PROP)
    assert [f.level for f in _fs(d, tl, "F01")] == ["pass"]


def test_bad_f01_png_without_alpha_fails(project):
    d, tl = _proj(project, MAP_PROP, alpha=False)
    fs = _fs(d, tl, "F01")
    assert [f.level for f in fs] == ["fail"] and "transparent" in fs[0].message


# F02 resolution ------------------------------------------------------------------------------------------------------
def test_good_f02_source_twice_the_shown_size_passes(project):
    d, tl = _proj(project, {**SCREEN_PROP, "width_px": 200})
    assert [f.level for f in _fs(d, tl, "F02")] == ["pass"]


def test_bad_f02_warns_below_1_5x_and_fails_below_1x_at_max_zoom(project):
    d, tl = _proj(project, {**SCREEN_PROP, "width_px": 300})          # 400 / 300 = 1.33x
    assert [f.level for f in _fs(d, tl, "F02")] == ["warn"]
    d, tl = _proj(project, {**MAP_PROP, "width_px": 395})             # the zoom_in preset grows it past 400 px
    fs = _fs(d, tl, "F02")
    assert [f.level for f in fs] == ["fail"] and "needs >= 1x" in fs[0].message


# F03 clear -----------------------------------------------------------------------------------------------------------
def test_good_f03_prop_away_from_texts_and_captions(project):
    d, tl = _proj(project, SCREEN_PROP)
    ev = [(120, 180, "Halo", 64, 300, 540, 1300)]
    assert [f.level for f in _fs(d, tl, "F03", ev)] == ["pass"]


def test_bad_f03_prop_under_a_caption_or_a_pin_label_fails(project):
    d, tl = _proj(project, SCREEN_PROP)
    ev = [(120, 180, "Halo", 64, 300, 540, 700)]
    fs = _fs(d, tl, "F03", ev)
    assert fs and all(f.level == "fail" for f in fs) and "caption 'Halo'" in fs[0].message
    dom = {2: {"times": [{"t": 1.0, "rects": [{"id": "pin:0", "x": 500, "y": 680, "w": 120, "h": 40, "opacity": 1}]}]}}
    fs = _fs(d, tl, "F03", (), dom)
    assert [f.level for f in fs] == ["fail"] and "pin:0" in fs[0].message


# F04 safe area -------------------------------------------------------------------------------------------------------
def test_bad_f04_prop_at_the_frame_edge_fails(project):
    d, tl = _proj(project, {**SCREEN_PROP, "x": 40})
    assert [f.level for f in _fs(d, tl, "F04")] == ["fail"]


def test_good_f04_prop_inside_the_safe_area(project):
    d, tl = _proj(project, SCREEN_PROP)
    assert [f.level for f in _fs(d, tl, "F04")] == ["pass"]


# T02 timing, R07 credits, caption avoid-list -------------------------------------------------------------------------
def test_bad_t02_prop_frames_leave_the_segment(project):
    d, tl = _proj(project, {**MAP_PROP, "t_in": 50, "t_out": 90})
    fs = [f for f in layer1.t02_integrity(tl, d) if f.level == "fail"]
    assert fs and "leave the segment" in fs[0].message


def test_bad_t02_enter_and_exit_longer_than_the_prop(project):
    d, tl = _proj(project, {**MAP_PROP, "t_in": 0, "t_out": 20, "enter": {"kind": "fade", "s": 0.5},
                            "exit": {"kind": "fade", "s": 0.5}})
    assert any("enter + exit" in f.message for f in layer1.t02_integrity(tl, d) if f.level == "fail")


def test_good_r07_credited_prop_passes(project):
    d, tl = _proj(project, MAP_PROP)
    assert [f.level for f in layer1.r07_credits(tl, d)] == ["pass"]


def test_bad_r07_uncredited_prop_fails(project):
    d, tl = _proj(project, {**MAP_PROP, "asset_id": None}, credit=False)
    fs = layer1.r07_credits(tl, d)
    assert any(f.level == "fail" and "prop" in f.message for f in fs)


def test_good_props_join_the_caption_avoid_list_for_their_frames_only(project):
    d, tl = _proj(project, {**SCREEN_PROP, "t_in": 0, "t_out": 30})
    boxes = layer2.element_boxes(tl, d, {})[2]
    pr = [b for b in boxes if b["id"] == "prop:0"]
    assert pr and pr[0]["frames"][0] == 120 and pr[-1]["frames"][1] == 150
    inside = (125, 130, "Halo", 64, 300, 540, 700)
    later = (160, 170, "Halo", 64, 300, 540, 700)
    assert layer2._rects_for(inside, tl, {2: pr}) and not layer2._rects_for(later, tl, {2: pr})
