"""Layer 2 gates (math + DOM + caption placement): labelled GOOD and BAD cases."""
import pytest

from video_engine import captions, profiles, timeline
from video_engine.gates import layer2, run_preflight

from conftest import DEMO

CLEAR = profiles.load().caption_style["clearance"]   # default profile caption clearance (px at 1920)


def fails(fs, gate):
    return [f for f in fs if f.gate == gate and f.level == "fail"]


# M01 ---------------------------------------------------------------------------------------------------------------
def test_M01_good(project):
    p, tl = project()
    assert not fails(layer2.m01_source_length(tl, p), "M01")


def test_M01_bad_source_too_short(project):
    p, tl = project(lambda d: d["segments"][1].update(source_in_frame=90))   # 4 s clip = 120 frames < 90 + 60
    assert "needs" in fails(layer2.m01_source_length(tl, p), "M01")[0].message


# R56 ---------------------------------------------------------------------------------------------------------------
def test_R56_good(project):
    p, tl = project()
    fs, fixes = layer2.r56_camera(tl, p)
    assert not fails(fs, "R56") and not fixes


def test_R56_fix_auto_raises_intensity(project):
    p, tl = project(lambda d: d["segments"][0]["camera_move"].update(intensity=0.2))
    fs, fixes = layer2.r56_camera(tl, p)
    assert fixes and fixes[0]["seg"] == 0 and fixes[0]["to"] > 0.2
    assert tl.segments[0].camera_move.intensity == fixes[0]["to"]
    assert any(f.level == "fix" for f in fs)


def test_R56_fix_hyperframes_intensity(project):
    p, tl = project(lambda d: d["segments"][2]["gsap_scene_config"].update(camera_intensity=0.1))
    fs, fixes = layer2.r56_camera(tl, p)
    assert fixes and fixes[0]["path"] == "gsap_scene_config.camera_intensity"


def test_R56_bad_auto_disabled(project):
    p, tl = project(lambda d: d["segments"][0]["camera_move"].update(intensity=0.2, auto_intensity=False))
    assert "auto_intensity is false" in fails(layer2.r56_camera(tl, p)[0], "R56")[0].message


def test_R56_bad_static_still(project):
    p, tl = project(lambda d: d["segments"][0]["camera_move"].update(preset="static"))
    assert fails(layer2.r56_camera(tl, p)[0], "R56")


# R62 ---------------------------------------------------------------------------------------------------------------
def test_R62_good(project, W):
    _, tl = project()
    fs, ev = layer2.r62_caption_margins(tl, W(("Surabaya", 0, 10), ("menolak", 10, 20)))
    assert not fails(fs, "R62") and ev


def test_R62_bad_font_shrunk_below_limit(project, W):
    _, tl = project(lambda d: d.update(caption_style={"max_chars": 80}))
    fs, _ = layer2.r62_caption_margins(tl, W(("Pemberontakankemerdekaanrakyatsurabayaindonesia", 0, 10)))
    assert "shrunk" in fails(fs, "R62")[0].message


def test_L22_bad_unknown_style_key(project, W):
    _, tl = project(lambda d: d.update(caption_style={"colour_of_box": "red"}))
    fs, _ = layer2.r62_caption_margins(tl, W(("a", 0, 10)))
    assert fails(fs, "L22")


# R63 element rects -------------------------------------------------------------------------------------------------
def rect(i, x, y, w, h, text="", opacity=1.0):
    return {"id": i, "text": text, "x": x, "y": y, "w": w, "h": h, "opacity": opacity}


def test_R63_good_rects():
    assert not layer2.check_rects([rect("pin:0", 300, 500, 300, 66), rect("card:0", 260, 230, 560, 180)], 1080, 1920, 1.0, 1)


def test_R63_bad_outside_safe_area():
    fs = layer2.check_rects([rect("pin:0", 900, 500, 300, 66)], 1080, 1920, 1.0, 1)
    assert "outside safe area" in fs[0].message


def test_R63_bad_pairwise_overlap():
    fs = layer2.check_rects([rect("pin:0", 300, 300, 347, 66), rect("card:0", 270, 230, 540, 244)], 1080, 1920, 1.0, 1)
    assert "overlaps" in fs[0].message


def test_R63_good_invisible_elements_ignored():
    assert not layer2.check_rects([rect("pin:0", 900, 500, 300, 66, opacity=0.0)], 1080, 1920, 1.0, 1)


# R63 caption vs elements: the 'habis tepat' / 'Pusat Kota' case from the first demo render ----------------------------
PUSAT_KOTA = [rect("pin:1", 565, 1088, 271, 66, "Pusat Kota"), rect("pin:0", 380, 646, 403, 66, "Jembatan Merah"),
              rect("card:0", 259, 230, 563, 176, "Surabaya, 1945")]


def demo_case(W):
    tl = timeline.load(DEMO)
    tl.caption_style = {}
    ws = W(("habis", 150, 160), ("tepat", 160, 175))
    events = captions.compile_ass(ws, tl)[1]
    boxes = {1: [dict(r, clear=CLEAR) for r in PUSAT_KOTA]}
    return tl, ws, events, boxes


def test_R63_bad_caption_too_close_to_pusat_kota_pin(W):
    tl, _, events, boxes = demo_case(W)
    assert events[0][6] == 1344          # default lower slot
    fs = layer2.r63_caption_clearance(tl, events, boxes)
    assert fs and "Pusat Kota" in fs[0].message and fs[0].seg == 1


def test_R63_good_caption_auto_placed_away_from_pin(W):
    tl, ws, events, boxes = demo_case(W)
    ovs, fs = layer2.place_captions(tl, events, boxes)
    assert ovs == [(120, 255, ovs[0][2])] and ovs[0][2] > 1344 and fs[0].level == "fix"
    moved = captions.compile_ass(ws, tl, ovs)[1]
    assert moved[0][6] == ovs[0][2]
    assert not layer2.r63_caption_clearance(tl, moved, boxes)
    box = layer2.caption_box(moved[0])
    assert box[1] - (1088 + 66) >= CLEAR and box[3] <= 1920 - layer2.SAFE_BOTTOM


def test_R63_bad_no_free_position(W):
    tl, _, events, _ = demo_case(W)
    wall = [dict(rect(f"pin:{i}", 100, 120 + i * 200, 880, 60, "x"), clear=CLEAR) for i in range(9)]
    ovs, fs = layer2.place_captions(tl, events, {1: wall})
    assert not ovs and fs[0].level == "fail"


def test_R63_native_overlay_and_focus_box(project, W):
    def mut(d):
        d["segments"][0]["overlays"] = [{"id": "logo", "box": [300, 1250, 480, 120], "text": "LOGO"}]
    p, tl = project(mut)
    ws = W(("Halo", 5, 20))
    ev = captions.compile_ass(ws, tl)[1]
    boxes = layer2.element_boxes(tl, p, {})
    assert any(r["id"] == "overlay:logo" for r in boxes[0])
    assert layer2.r63_caption_clearance(tl, ev, boxes)                       # BAD: overlay box at the caption slot
    ovs, _ = layer2.place_captions(tl, ev, boxes)
    assert not layer2.r63_caption_clearance(tl, captions.compile_ass(ws, tl, ovs)[1], boxes)   # GOOD after placement


def test_R63_focus_box_projected(project):
    p, tl = project(lambda d: d["segments"][0]["camera_move"].update(focus_box=[600, 300, 900, 700]))
    fr = layer2.focus_rects(tl.segments[0], tl, p)
    assert len(fr) == 2 and all(r["clear"] == 0 for r in fr) and all(r["y"] > 900 for r in fr)


# end-to-end on the demo project: DOM probe in headless Chrome ------------------------------------------------------
@pytest.mark.parametrize("auto_place", [False, True], ids=["BAD-no-autoplace", "GOOD-autoplace"])
def test_R63_dom_demo_end_to_end(W, auto_place):
    tl = timeline.load(DEMO)
    tl.caption_style = {"auto_place": auto_place}
    rep = run_preflight(tl, DEMO, W(("habis", 150, 160), ("tepat", 160, 175)), snapshots=False)
    r63 = [f for f in rep.findings if f.gate == "R63"]
    if auto_place:
        assert rep.ok and any(f.level == "fix" and f.seg == 1 for f in r63)
        assert rep.caption_y_overrides and rep.caption_y_overrides[0][0] == 120
    else:
        assert not rep.ok and any("Pusat Kota" in f.message for f in r63 if f.level == "fail")
    assert rep.timings["total"] < 5.0
