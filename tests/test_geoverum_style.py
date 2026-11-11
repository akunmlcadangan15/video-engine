"""Geoverum map style (theme style options): vivid entity colours on dark satellite maps only, titles placed above
their region with a tracking-in entry, box labels that grow with their typed text, numbers in signal yellow that pop
without an elastic overshoot. GOOD cases render the real template in Chrome where the behaviour is visual."""
import itertools
import math

import numpy as np
from PIL import Image

from video_engine import entities, hyperframes, legibility as L, profiles

GEO = profiles.load("geoverum")
SQ = [[0.35, 0.45], [0.6, 0.45], [0.6, 0.6], [0.35, 0.6]]
TOP = [[0.35, 0.03], [0.6, 0.03], [0.6, 0.12], [0.35, 0.12]]   # a region at the top edge of the map


def _scene(**kw):
    def mut(d):
        d["profile"] = "geoverum"
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(pins=[], regions=[{"entity": "Spanyol", "polygon": SQ}], title="SPANYOL", title_entity="Spanyol",
                  camera_path=[{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.0},
                               {"at_frame": 59, "focus": [0.5, 0.5], "zoom": 1.02}])
        sc.update(**kw)
    return mut


def _oklab(h):
    lch = L.rgb_oklch(L.hex_rgb(h))
    return lch[0], lch[1] * math.cos(math.radians(lch[2])), lch[1] * math.sin(math.radians(lch[2]))


# good: profile values ------------------------------------------------------------------------------------------------
def test_good_vivid_twins_keep_hue_and_stay_distinct():
    """Every palette colour has a vivid twin with the same hue, about 1.8x chroma (gamut permitting), and the twins
    stay as distinct as the muted palette (OKLab distance >= 0.07)."""
    th = GEO.theme
    assert th.vivid_chroma_scale == 1.8 and tuple(th.vivid_l) == (0.44, 0.78)
    pal = [c.lower() for c in th.palette]
    viv = [entities.vivid_colour(c, th) for c in pal]
    for c, v in zip(pal, viv):
        (_, c0, h0), (_, c1, h1) = L.rgb_oklch(L.hex_rgb(c)), L.rgb_oklch(L.hex_rgb(v))
        assert min(abs(h1 - h0), 360 - abs(h1 - h0)) < 3, (c, v)
        assert c1 > c0, (c, v)                                       # gamut-limited hues (teal) gain less
    assert min(math.dist(_oklab(a), _oklab(b)) for a, b in itertools.combinations(viv, 2)) >= 0.07
    assert np.median([L.rgb_oklch(L.hex_rgb(v))[1] for v in viv]) >= 0.13   # reference fills: C 0.17-0.20


def test_good_type_scale_and_style_values_agree():
    th, d = GEO.theme, GEO.data
    assert d.type.title_px == th.title_px == d.layout.sizes.title == 104 and th.title_font == "title"
    assert th.figure_px < th.title_px and th.text_outline_px == 0 and th.pin_colour.lower() == "#e61129"
    assert d.reference_style.style["unified"]["signal_colors"]["pin"].lower() == th.pin_colour.lower()
    assert (th.title_entrance, th.title_anchor, th.label_box_entrance, th.figure_pop) == ("track", "region", "type", "back")
    assert d.layout.fly.title_in == 1.0 and d.layout.fly.label_in <= 1.15 and d.layout.fly.ease_in == "power3.out"


def test_good_default_profile_keeps_the_engine_look():
    th = profiles.load().theme
    assert th.vivid_chroma_scale is None and th.title_entrance == "fade" and th.label_box_entrance == "pop"
    assert th.figure_pop == "elastic" and th.figure_accent is None and th.title_anchor == "top"
    hf = profiles.load().render_values("hf")
    assert not {"vivid_chroma_scale", "title_entrance", "label_box_entrance", "figure_pop", "figure_accent"} & set(hf)


# good / bad: vivid on dark maps only ---------------------------------------------------------------------------------
def test_good_scene_paints_vivid_entity_colours_on_a_dark_map(project):
    p, tl = project(_scene())
    seg = tl.segments[2]
    assert entities.dark_map(seg, tl, p)
    muted, scene = entities.colours(tl)["Spanyol"], entities.scene_colours(seg, tl, p)["Spanyol"]
    assert scene == entities.vivid_colour(muted, GEO.theme) != muted
    sc = hyperframes.scene_data(seg, tl, p)
    assert sc["regions"][0]["colour"] == scene and sc["title"]["colour"] == scene   # fill and title glow


def test_bad_paper_map_keeps_the_muted_palette(project):
    """A bright archival map is printed: vivid colours would fight the paper multiply, so the palette stays muted."""
    def mut(d):
        _scene()(d)
        d["segments"][2]["gsap_scene_config"]["map_asset"] = "assets/paper.jpg"
    p, _ = project()
    Image.new("RGB", (2700, 2880), (238, 226, 200)).save(p / "assets" / "paper.jpg")
    p, tl = project(mut)
    seg = tl.segments[2]
    assert not entities.dark_map(seg, tl, p)
    assert entities.scene_colours(seg, tl, p) == entities.colours(tl)


# good / bad: title placement above its region -----------------------------------------------------------------------
def test_good_title_enters_above_its_region(project):
    p, tl = project(_scene())
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    cam, (mw, mh) = sc["camera"]["start"], sc["map_size"]
    region_top = min(y * mh * cam["s"] + cam["y"] for _, y in SQ)
    t = sc["title"]
    assert t["top"] + 104 * 1.04 <= region_top - 104 * hyperframes.TITLE_GAP_EM + 1   # above, with the gap
    assert abs(t["left"] - (0.475 * mw * cam["s"] + cam["x"])) < 1                 # centred on the region


def test_bad_region_at_the_top_puts_the_title_below_it_inside_the_safe_area(project):
    p, tl = project(_scene(regions=[{"entity": "Spanyol", "polygon": TOP}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    cam, (_, mh) = sc["camera"]["start"], sc["map_size"]
    region_bottom = max(y * mh * cam["s"] + cam["y"] for _, y in TOP)
    assert sc["title"]["top"] >= region_bottom and sc["title"]["top"] >= 100     # safe top (units.safe)


# good: numbers -------------------------------------------------------------------------------------------------------
def test_good_numbers_take_the_accent_and_years_do_not(project):
    p, tl = project(_scene(figures=[{"kind": "number", "value": 505990, "at_frame": 3, "position": [0.5, 0.7]},
                                    {"kind": "year", "value": 1492, "at_frame": 3, "position": [0.5, 0.3]}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    num, year = sc["figures"]
    assert num["colour"] == num["accent"] == "#ffd417" and "accent" not in year and year["colour"] == "#ffffff"
    light = {e["id"]: e.get("light") for e in L.elements(sc)}
    assert light["figure:0"] == "#ffd417"                                          # the ink ladder keeps the accent
    f = tl.segments[2].gsap_scene_config.figures[0]
    assert hyperframes.figure_end_frame(f, 30, 1.2, "back") == 3 + math.ceil(0.45 * 30)
    assert hyperframes.figure_end_frame(f, 30, 1.2) == 3 + math.ceil(0.9 * 30)    # the engine default stays


# good: rendered behaviour (Chrome) ------------------------------------------------------------------------------------
def test_good_title_tracks_in_and_is_never_wider_than_when_settled(project, tmp_path):
    p, tl = project(_scene(title_at_frame=6))
    d, _ = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    res = hyperframes.probe_dom(d, [0.2 + 0.03, 0.2 + 0.12, 0.2 + 0.25, 1.5], 1080, 1920)
    t = [next(r for r in it["rects"] if r["id"] == "title") for it in res["times"]]
    assert t[0]["opacity"] < 1 and t[-1]["opacity"] == 1
    assert all(abs(r["w"] - t[-1]["w"]) <= 1 for r in t)      # the width stays the settled width on every frame
    assert t[0]["h"] < 0.9 * t[-1]["h"]                       # ... spread, smaller letters close in and grow


def test_good_box_label_grows_with_its_typed_text(project, tmp_path):
    p, tl = project(_scene(regions=[{"entity": "Spanyol", "polygon": SQ, "label": "Spanyol", "box": True,
                                     "label_at_frame": 6}]))
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    box = L.hex_rgb(sc["regions"][0]["box"])
    res = hyperframes.probe_dom(d, [0.2 + 0.08, 0.2 + 0.6], 1080, 1920, tmp_path / "snaps")
    r = next(x for x in res["times"][-1]["rects"] if x["id"] == "region:0")
    x0, y0, x1, y1 = (round(v) for v in (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
    share = []
    for i in range(2):
        a = np.asarray(Image.open(tmp_path / "snaps" / f"snap_{i}.png").convert("RGB"), float)[y0:y1, x0:x1]
        share.append(float((np.abs(a - np.asarray(box)).max(axis=2) < 24).mean()))   # hex_rgb: 0-255
    assert share[0] < 0.6 * share[1] and share[1] > 0.3        # part way through the typing: part of the pill


def test_good_number_pop_stays_inside_the_frame(project, tmp_path):
    """BAD before: a 156 px number scaling elastically from 0 overshot the frame width at 0.8-0.9 s."""
    p, tl = project(_scene(figures=[{"kind": "number", "value": 505990, "suffix": " km²", "at_frame": 3,
                                     "position": [0.5, 0.7]}]))
    d, _ = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    res = hyperframes.probe_dom(d, [0.1 + k * 0.05 for k in range(10)], 1080, 1920)
    for it in res["times"]:
        f = next(r for r in it["rects"] if r["id"] == "figure:0")
        assert f["x"] >= 0 and f["x"] + f["w"] <= 1080, (it["t"], f["x"], f["w"])


def test_good_creative_staging_rule_quotes_the_profile_values():
    """The staging default in creative.md names the same values the renderer reads (one source, kept equal here)."""
    th, d = GEO.theme, GEO.data
    rule = next(r for r in d.draft.rules if r.startswith("Default, not a mandate: stage every beat"))
    assert f"Archivo Black {th.title_px:g} px" in rule and th.title_font == "title"
    for key in ("title_entrance", "title_anchor", "label_box_entrance", "figure_pop", "figure_accent"):
        assert f'`{key} = "{getattr(th, key)}"`' in rule, key
    assert f"`[layout.budget] animating = {d.layout.budget.animating}`" in rule
