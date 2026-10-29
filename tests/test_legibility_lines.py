"""Profile [legibility] (Geoverum): water labels, line contrast of pins, routes, region outlines and
leaders, halo geometry, open counters, adaptive area labels and leader lines. Labelled GOOD and BAD."""
import json
import re

import numpy as np
import pytest
from PIL import Image

from video_engine import hyperframes, legibility as L, profiles, timeline
from video_engine.gates import layer2
from tests.test_layout import beats_doc
from tests.test_legibility import GEO, LG, PAPER, DARK_MAP, _geo, _geo_tl, _halo_first, _r64_dom

WATER = "#033d57"


def _hex(c):
    return layer2._rgb_hex(c)


# GOOD / BAD: water labels ---------------------------------------------------------------------------------------------
def test_good_water_ink_is_the_configured_blue():
    assert L.oklch_hex(LG["water_ink_l"], LG["water_ink_c"], LG["water_ink_hue"]).lower() == WATER
    assert L.dark_ink({"water": True}, LG).lower() == WATER


def test_bad_land_area_label_never_gets_the_water_ink():
    assert L.dark_ink({"colour": "#4B4D8B"}, LG).lower() != WATER
    assert L.dark_ink({}, LG).lower() != WATER


def test_good_area_label_without_water_key_loads_as_land():
    a = timeline.AreaLabel.model_validate({"text": "SEKUTU", "region": 0})
    assert a.water is False and timeline.AreaLabel.model_validate({"text": "SELAT", "region": 1, "water": True}).water


def test_bad_area_label_unknown_key_still_rejected():
    with pytest.raises(Exception, match="sea"):
        timeline.AreaLabel.model_validate({"text": "SELAT", "region": 1, "sea": True})


def test_good_water_flag_reaches_the_area_element():
    sc = {"fps": 30, "n_frames": 300, "theme": {"pin_style": "teardrop", "pin_label_px": 52, "title_px": 112,
                                                 "figure_px": 128},
          "pins": [], "regions": [{"entity": "Laut", "colour": "#3a5a8a"}],
          "layout": {"els": {"area:0": [[1.0, 0.3, None, 0.0]]},
                     "areas": [{"key": 0, "text": "SELAT", "px": 44, "region": 0, "water": True}]}}
    (el,) = L.elements(sc)
    assert el["kind"] == "area" and el["water"] and el["colour"] is None
    sc["layout"]["areas"][0].pop("water")
    (el,) = L.elements(sc)
    assert not el["water"] and el["colour"] == "#3a5a8a"


# GOOD / BAD: line contrast (R64 lines) --------------------------------------------------------------------------------
def _line_dom(tmp_path, bg_rgb, colours, ring_rgb=None, lid="route:0", opacity=1.0):
    """One probed frame: a 300 x 120 lines-hidden background (bg_rgb; ring_rgb paints the lower half) and a 6 px bar
    of line pixels (the line-mask screenshot), with its data-line-ink colours."""
    bg = np.zeros((120, 300, 3), np.uint8) + np.array(bg_rgb, np.uint8)
    if ring_rgb is not None:
        bg[60:] = ring_rgb
    mask = np.full((120, 300, 3), 255, np.uint8)
    mask[57:63, 40:260] = 40
    Image.fromarray(bg).save(tmp_path / "line_bg.png")
    Image.fromarray(mask).save(tmp_path / "line_mask.png")
    ln = {"id": lid, "x": 40, "y": 57, "w": 220, "h": 6, "opacity": opacity, "ink": json.dumps(colours)}
    item = {"t": 1.0, "rects": [], "lines": [ln], "line_bg": str(tmp_path / "line_bg.png"),
            "line_mask": str(tmp_path / "line_mask.png"), "line_clip": {"x": 0, "y": 0, "w": 300, "h": 120}}
    return {0: {"times": [], "legibility": {"lg": LG, "elements": [], "times": [item]}}}


def _levels(tl, dom):
    return [f.level for f in layer2.r64_legibility(tl, dom)]


def test_good_R64_cased_route_on_paper_passes(project, tmp_path):
    tl = _geo_tl(project)
    dom = _line_dom(tmp_path, PAPER, ["#ffffff", LG["route_casing_colour"]])
    assert "fail" not in _levels(tl, dom) and "warn" not in _levels(tl, dom)
    (row,) = layer2.r64_line_rows(dom)
    assert row["lc"] >= LG["lc_line"] and row["ring_px"] > 0


def test_bad_R64_line_in_the_map_colour_fails(project, tmp_path):
    tl = _geo_tl(project)
    fs = layer2.r64_legibility(tl, _line_dom(tmp_path, [181, 106, 149], ["#b56a95"], lid="region:0"))
    assert fs and fs[0].level == "fail" and "region:0 Lc" in fs[0].message


def test_bad_R64_line_warns_between_fail_and_target(project, tmp_path):
    tl = _geo_tl(project)
    core = "#000000"
    def lc_of(v):
        return abs(float(L.apca_lc(float(L.apca_y(L.hex_rgb(core))), float(L.apca_y([v] * 3)))))
    v = next(v for v in range(256) if LG["lc_line_fail"] + 1 <= lc_of(v) <= LG["lc_line"] - 1)
    assert _levels(tl, _line_dom(tmp_path, [v] * 3, [core])) == ["warn"]


def test_good_casing_separates_where_the_core_cannot(project, tmp_path):
    """Half the ring paper, half dark map: the red core alone fails on part of the ring, the paper outline covers the
    rest; R64 takes per ring pixel the best of core and casing."""
    tl = _geo_tl(project)
    red, paper = "#e8322b", LG["paper_colour"]
    one = layer2.r64_line_rows(_line_dom(tmp_path, [228, 60, 50], [red], ring_rgb=DARK_MAP, lid="pin:0"))[0]["lc"]
    both = _line_dom(tmp_path, [228, 60, 50], [red, paper], ring_rgb=DARK_MAP, lid="pin:0")
    assert one < LG["lc_line_fail"]
    assert layer2.r64_line_rows(both)[0]["lc"] >= LG["lc_line"] and "fail" not in _levels(tl, both)


def test_good_dimmed_line_is_not_gated(tmp_path):
    assert layer2.r64_line_rows(_line_dom(tmp_path, [181, 106, 149], ["#b56a95"], opacity=0.38)) == []


@pytest.mark.parametrize("core, casing", [("#4b4d8b", "paper_colour"), ("#b56a95", "paper_colour"),
                                          ("#f2d06b", "route_casing_colour")],
                         ids=["GOOD-dark-outline-paper-casing", "GOOD-mid-outline-paper-casing",
                              "GOOD-light-outline-dark-casing"])
def test_good_region_casing_takes_the_opposite_tone(core, casing):
    assert L.line_casing(core, LG) == LG[casing]


def test_bad_neutral_white_leader_on_paper_fails_the_line_gate(project, tmp_path):
    """The neutral leader (white) without adaptive ink: Lc ~0 on paper."""
    fs = layer2.r64_legibility(_geo_tl(project), _line_dom(tmp_path, PAPER, ["#ffffff"], lid="leader:pin:0:0"))
    assert fs and fs[0].level == "fail"


# GOOD / BAD: halo geometry --------------------------------------------------------------------------------------------
def _halo_dom(tmp_path, em=0.07, ratio=0.25, alpha=0.7, kind="title"):
    dom = _r64_dom(tmp_path, PAPER, "rgb(47, 33, 21)", kind=kind)
    r = dom[0]["legibility"]["times"][0]["rects"][0]
    px = 112 if kind == "title" else 52
    r.update(stroke_px=0, stroke="rgba(0, 0, 0, 0)", paint_order="normal",
             halo=json.dumps({"w": em * px, "blur": ratio * em * px, "em": em, "px": px, "holes": 0}),
             halo_color=f"rgba(236, 226, 214, {alpha})")
    return dom


def test_good_R64_halo_in_band_passes(project, tmp_path):
    tl = _geo_tl(project)
    assert _levels(tl, _halo_dom(tmp_path)) == ["pass"]
    (row,) = layer2.r64_rows(_halo_dom(tmp_path))
    assert row["halo_em"] == 0.07 and row["blur_ratio"] == 0.25 and row["halo_alpha"] == 0.7


@pytest.mark.parametrize("kw, msg", [({"em": 0.12}, "halo width"), ({"em": 0.04}, "halo width"),
                                     ({"ratio": 0.6}, "halo blur"), ({"alpha": 0.95}, "halo alpha"),
                                     ({"alpha": 0.4}, "halo alpha")],
                         ids=["BAD-too-wide", "BAD-too-thin", "BAD-blur-over-half-width", "BAD-too-opaque",
                              "BAD-too-faint"])
def test_bad_R64_halo_out_of_band_fails(project, tmp_path, kw, msg):
    fs = layer2.r64_legibility(_geo_tl(project), _halo_dom(tmp_path, **kw))
    assert any(f.level == "fail" and msg in f.message for f in fs), [f.message for f in fs]


@pytest.mark.parametrize("key, val", [("halo_blur_ratio", 0.6), ("halo_width_em", 0.12), ("label_halo_width_em", 0.05),
                                      ("lc_line_fail", 35), ("halo_blur_ratio", 0.3)],
                         ids=["BAD-blur-ratio-over-half", "BAD-width-over-max", "BAD-width-under-min",
                              "BAD-line-fail-above-target", "BAD-blur-ratio-over-max"])
def test_bad_legibility_schema_halo_and_line_bands(key, val):
    over = {key: val}
    if key == "halo_blur_ratio" and val == 0.3:
        over["halo_blur_max_ratio"] = 0.2
    with pytest.raises(Exception):
        profiles.Legibility.model_validate({**LG, **over})


def test_good_geoverum_halo_and_line_keys():
    g = GEO
    assert (g["halo_width_min_em"], g["halo_width_max_em"]) == (0.06, 0.10)
    assert g["halo_blur_ratio"] <= g["halo_blur_max_ratio"] == 0.5
    assert g["halo_alpha"] == g["halo_alpha_max"] == 0
    assert (g["lc_line"], g["lc_line_fail"]) == (30, 25) and 2 <= g["pin_outline_px"] <= 3
    assert 1.5 <= g["route_casing_px"] <= 2 and g["leader_casing_px"] == 0 and g["region_casing_px"] == 0


# GOOD / BAD: open counters --------------------------------------------------------------------------------------------
def _ring_glyph(h=120, w=300):
    g = np.zeros((h, w), bool)
    g[30:90, 100:160] = True
    g[42:78, 112:148] = False                       # an 'O': 36 x 36 counter
    return g


def test_good_halo_coverage_leaves_the_counter_empty():
    g = _ring_glyph()
    cov = L.halo_coverage(g, 4.0, 1.0)
    assert L.holes(g).sum() == 36 * 36 and cov[L.holes(g)].max() == 0.0
    assert cov[30:90, 97:100].min() > 0.3           # the halo is painted outside


def test_bad_stroke_halo_fills_the_counter():
    """A text-stroke halo (the dilation alone) paints the inside of the loop."""
    g = _ring_glyph()
    assert (L.disk_dilate(g, 4.0) & L.holes(g)).sum() > 0


def _counter_dom(tmp_path, fill):
    g = _ring_glyph()
    bg = np.zeros((120, 300, 3), np.uint8) + np.array(PAPER, np.uint8)
    mask = np.where(g[..., None], 0, 255).astype(np.uint8).repeat(3, axis=2)
    png = bg.copy()
    png[g] = [47, 33, 21]
    if fill:
        png[L.disk_dilate(g, 4.0) & ~g] = [250, 246, 238]   # stroke halo: also inside the counter
    for n, a in (("bg", bg), ("mask", mask), ("png", png)):
        Image.fromarray(a).save(tmp_path / f"{n}.png")
    st = {"flag": False, "pol": "dark", "ink": "#2f2115", "halo": "#ece2d6", "va": 0, "step": 0}
    r = {"id": "title", "text": "O", "x": 100, "y": 30, "w": 60, "h": 60, "opacity": 1, "color": "rgb(47, 33, 21)",
         "stroke_px": 0, "stroke": "", "paint_order": "normal", "ink_state": json.dumps(st),
         "halo": json.dumps({"w": 4.2, "blur": 1.05, "em": 0.07, "px": 60, "holes": 1296}),
         "halo_color": "rgba(236, 226, 214, 0.7)"}
    el = {"id": "title", "kind": "title", "start": 1.0, "end": 1.0, "entity": None, "colour": None, "px": 60}
    item = {"t": 1.0, "rects": [r], "bg": str(tmp_path / "bg.png"), "mask": str(tmp_path / "mask.png"),
            "png": str(tmp_path / "png.png"), "clip": {"x": 0, "y": 0, "w": 300, "h": 120}}
    return {0: {"times": [], "legibility": {"lg": LG, "elements": [el], "times": [item]}}}


def test_good_R64_open_counter_passes(project, tmp_path):
    dom = _counter_dom(tmp_path, fill=False)
    (row,) = layer2.r64_rows(dom)
    assert row["counters"][0] > 0 and row["counters"][1] == 1.0
    assert not any("counters" in f.message and f.level == "fail" for f in layer2.r64_legibility(_geo_tl(project), dom))


def test_bad_R64_filled_counter_fails(project, tmp_path):
    fs = layer2.r64_legibility(_geo_tl(project), _counter_dom(tmp_path, fill=True))
    assert any(f.level == "fail" and "halo fills the counters" in f.message for f in fs)


# GOOD / BAD: area labels (R64 target lc_area) -------------------------------------------------------------------------
def _area_dom(tmp_path, bg, ink):
    dom = _r64_dom(tmp_path, bg, ink, kind="title")
    meta = dom[0]["legibility"]
    meta["elements"][0].update(id="area:0", kind="area", px=44)
    meta["times"][0]["rects"][0]["id"] = "area:0"
    return dom


def test_good_R64_area_label_dark_ink_on_paper_passes(project, tmp_path):
    dom = _area_dom(tmp_path, PAPER, "rgb(47, 33, 21)")
    (row,) = layer2.r64_rows(dom)
    assert row["kind"] == "area" and row["target"] == LG["lc_area"] and row["lc"] >= LG["lc_area"]
    assert _levels(_geo_tl(project), dom) == ["pass"]


def test_bad_R64_area_label_dark_ink_on_dark_map_fails(project, tmp_path):
    fs = layer2.r64_legibility(_geo_tl(project), _area_dom(tmp_path, DARK_MAP, "rgb(47, 33, 21)"))
    assert fs and fs[0].level == "fail" and "area:0" in fs[0].message


# GOOD: end to end (real render) ---------------------------------------------------------------------------------------
def _water_beats(doc):
    beats_doc(doc)
    doc["segments"][2]["gsap_scene_config"]["beats"][1]["area_labels"] = [
        {"text": "Sekutu", "region": 0}, {"text": "Selat", "region": 1, "water": True}]


def test_good_area_labels_adaptive_and_water_label_blue_italic(project, tmp_path, monkeypatch):
    _halo_first(monkeypatch)
    p, tl = project(_water_beats)
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    areas = sc["layout"]["areas"]
    assert [bool(a.get("water")) for a in areas] == [False, True]
    els = {e["id"]: e for e in L.elements(sc) if e["kind"] == "area"}
    t = round(max(e["start"] for e in els.values()) + 0.1, 2)
    res = hyperframes.probe_dom(d, [t], 1080, 1920, tmp_path / "snaps", bg="bg")
    rects = {r["id"]: r for r in res["times"][0]["rects"]}
    land, water = (rects[e] for e in sorted(els, key=lambda k: bool(els[k]["water"])))
    assert water["font_style"] == "italic" and land["font_style"] == "normal"
    # the water ink (#033d57) or, when the chooser escalates (step 3), the same blue lowered by ink_l_step
    blues = {L.dark_ink({"water": True}, LG, k * LG["ink_l_step"]).lower() for k in range(8)}
    assert WATER in blues and _hex(water["color"]).lower() in blues and _hex(land["color"]).lower() not in blues
    assert _hex(water["color"]).lower() == sc["inks"][next(k for k in els if els[k]["water"])]["segs"][0]["ink"].lower()
    for r in (land, water):
        hx = layer2._halo(r)
        assert r["stroke_px"] == 0 and hx and hx["alpha"] > 0 and LG["halo_width_min_em"] <= hx["em"] <= LG["halo_width_max_em"]
    res["legibility"] = {"lg": sc["legibility"], "elements": L.elements(sc), "times": res["times"]}
    rows = [w for w in layer2.r64_rows({2: res}) if w["kind"] == "area"]
    assert {w["id"] for w in rows} == set(els) and all(w["target"] == LG["lc_area"] for w in rows)


def test_good_figure_counters_open_with_lining_digits(project, tmp_path, monkeypatch):
    """Playfair's default digits are old-style; the figure shows lining tabular digits. The halo mask draws the same
    glyphs (feature twin faces), so its counters match the DOM glyph mask and stay open."""
    _halo_first(monkeypatch)
    p, tl = project(_geo(camera_preset="static", figures=[{"kind": "year", "value": 1945, "at_frame": 0,
                                                            "position": [0.5, 0.4]}]))
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    res = hyperframes.probe_dom(d, [1.5], 1080, 1920, tmp_path / "snaps", bg="bg")
    res["legibility"] = {"lg": sc["legibility"], "elements": L.elements(sc), "times": res["times"]}
    (row,) = [w for w in layer2.r64_rows({2: res}) if w["id"] == "figure:0"]
    assert row["counters"] and row["counters"][1] >= LG["counter_open_min"]
    it = res["times"][0]
    r = next(r for r in it["rects"] if r["id"] == "figure:0")
    mask = np.asarray(Image.open(it["mask"]).convert("L")) < 128
    ox, oy = it["clip"]["x"], it["clip"]["y"]
    crop = mask[int(r["y"] - oy):int(r["y"] - oy + r["h"]), int(r["x"] - ox):int(r["x"] - ox + r["w"])]
    dom_holes, canvas_holes = int(L.holes(crop).sum()), json.loads(r["halo"])["holes"]
    assert dom_holes > 0 and abs(canvas_holes - dom_holes) <= 0.15 * dom_holes


def test_good_leader_line_takes_the_label_ink_and_casing(project, tmp_path):
    """A pin label on a leader: the leader is painted in the label's ink with a casing in its halo (dark ink) and
    gated as a line."""
    p, tl = project(beats_doc)
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    js = (d / "scene.js").read_text()
    S = json.loads(re.fullmatch(r"window\.SCENE=(.*);\s*", js, re.S).group(1))
    track = S["layout"]["labels"]["0"]
    pos = next(e[1] for e in track if e[1])
    pos["leader"] = [0, 0, 60, 0]
    pos["x"] += 60
    for e in track:
        if e[1]:
            e[1] = pos
    (d / "scene.js").write_text("window.SCENE=" + json.dumps(S) + ";")
    t0 = next(e for e in L.elements(sc) if e["id"] == "pin:0")
    t = round((t0["start"] + t0["end"]) / 2, 2)
    res = hyperframes.probe_dom(d, [t], 1080, 1920, tmp_path / "snaps", bg="bg")
    it = res["times"][0]
    lab = next(r for r in it["rects"] if r["id"] == "pin:0")
    st = L.state(lab)
    (ln,) = [x for x in it["lines"] if x["id"].startswith("leader:pin:0")]
    ink = json.loads(ln["ink"])
    assert ink[0].lower() == _hex(lab["color"]).lower() == st["ink"].lower()
    assert ink[1].lower() == (st["halo"] if st["lt"] < 0.5 else LG["route_casing_colour"]).lower()
    res["legibility"] = {"lg": sc["legibility"], "elements": L.elements(sc), "times": res["times"]}
    rows = [w for w in layer2.r64_line_rows({2: res}) if w["kind"] == "leader"]
    assert rows and rows[0]["target"] == LG["lc_line"]
