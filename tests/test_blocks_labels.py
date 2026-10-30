"""Block regions, solid box labels, neon borders, region changes, path props, map morph and globe regions."""
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import entities, hyperframes, legibility as L, profiles, props
from video_engine.gates import layer1, layer2, style
from video_engine.util import EngineError

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
TH = profiles.load().render_values("hf")


def _sc(**kw):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(kw)
    return mut


def _theme(monkeypatch, **kw):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=kw)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))


def _boxed(monkeypatch):
    _theme(monkeypatch, region_style="block", label_style="box")


def _lc(ink, bg):
    return abs(float(L.apca_lc(L.apca_y(L.hex_rgb(ink)), L.apca_y(L.hex_rgb(bg)))))


def _probe(tl, p, tmp_path, times):
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    return hyperframes.probe_dom(d, list(times), 1080, 1920), sc


# colours -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("c", ["#C99D4E", "#136868", "#4B4D8B", "#9C443B", "#F0F0F0", "#101010"])
def test_good_block_colour_keeps_hue_in_the_lightness_band_and_is_more_saturated(c):
    L0, C0, h0 = L.rgb_oklch(L.hex_rgb(c))
    L1, C1, h1 = L.rgb_oklch(L.hex_rgb(entities.block_colour(c, TH)))
    assert TH["block_l_min"] - 0.01 <= L1 <= TH["block_l_max"] + 0.01
    if C0 > 0.03:                                  # a hue to keep (greys have none)
        assert C1 > C0 and abs((h1 - h0 + 180) % 360 - 180) < 6


@pytest.mark.parametrize("c", ["#C99D4E", "#75A58B", "#FFD84A", "#437AA6"])
def test_good_box_colour_gives_white_text_its_lc(c):
    assert _lc("#ffffff", entities.box_colour(c, TH)) >= TH["label_box_lc"]


# scene data ----------------------------------------------------------------------------------------------------------
def test_good_block_region_scene_entry_with_label_and_change(project, monkeypatch):
    _boxed(monkeypatch)
    p, tl = project(_sc(regions=[{"entity": "A", "polygon": SQ, "at_frame": 3, "label": "ALPHA",
                                  "changes": [{"at_frame": 30, "entity": "B"}]}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    r, col = sc["regions"][0], entities.colours(tl)
    mw, mh = sc["map_size"]
    assert r["style"] == "block" and r["block"] == entities.block_colour(col["A"], TH)
    assert r["box"] == entities.box_colour(col["A"], TH) and r["edge_colour"] == TH["block_edge_colour"]
    assert r["draw_s"] == TH["block_draw_s"]
    assert L.rgb_oklch(L.hex_rgb(r["side"]))[0] < L.rgb_oklch(L.hex_rgb(r["block"]))[0]   # the side is darker
    assert r["label"] == {"text": "ALPHA", "x": round(0.5 * mw, 2), "y": round(0.5 * mh, 2), "at_frame": 3}
    (c,) = r["changes"]
    assert (c["entity"], c["at_frame"], c["block"]) == ("B", 30, entities.block_colour(col["B"], TH))


def test_good_box_style_puts_title_and_figures_in_boxes_not_adaptive(project, monkeypatch):
    _boxed(monkeypatch)
    p, tl = project(_sc(title="JUDUL", figures=[{"kind": "year", "value": 1893, "at_frame": 5}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert sc["title"]["box"] == sc["figures"][0]["box"] == TH["label_box_colour"]
    assert [e["id"] for e in L.elements(sc)] == []


def test_good_plain_style_keeps_title_without_box(project):
    p, tl = project(_sc(title="JUDUL"))
    assert "box" not in hyperframes.scene_data(tl.segments[2], tl, p)["title"]


def test_good_region_changes_and_borders_are_graphic_events(project):
    _, tl = project(_sc(regions=[{"entity": "A", "polygon": SQ, "changes": [{"at_frame": 20, "entity": "B"}]}],
                        borders=[{"points": SQ[:2], "at_frame": 40}]))
    ev = style.element_events(tl.segments[2], 30)
    assert {(120 + 20, ""), (120 + 40, "")} <= set(ev)


# props on a path -----------------------------------------------------------------------------------------------------
def test_good_path_at_walks_arc_length_with_the_segment_heading():
    path = {"pts": [[0, 0], [100, 0], [100, 300]], "f": 10}
    assert props.path_at(path, 0) == (0, 0, 0)
    assert props.path_at(path, 10) == pytest.approx((100, 300, 90))
    x, y, a = props.path_at(path, 5)                    # glide(0.5) = 0.5: half of the 400 px length
    assert (x, y, a) == pytest.approx((100, 100, 90))
    assert props.path_at(path, 99)[:2] == pytest.approx((100, 300))


def test_good_rendered_path_prop_matches_props_state(project, tmp_path):
    from tests.test_props import _proj, MAP_PROP
    pr = {**{k: v for k, v in MAP_PROP.items() if k not in ("x", "y", "lat", "lng")},
          "path": [[0.3, 0.6], [0.5, 0.4], [0.6, 0.3]], "path_s": 1.5, "heading": True}
    d, tl = _proj(project, pr)
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    (e,) = sc["props"]
    frames = [0, 20, 45, 59]
    res = hyperframes.probe_props(out, [f / sc["fps"] for f in frames], 1080, 1920)
    for f, item in zip(frames, res["times"]):
        st, (g,) = props.state(e, sc["camera"], f, 1080, 1920), item["props"]
        assert (g["x"], g["y"]) == pytest.approx((st["x"], st["y"]), abs=1.0), f
    assert props.state(e, sc["camera"], 0, 1080, 1920)["rot"] == pytest.approx(-45, abs=3)


@pytest.mark.parametrize("bad, msg", [
    ({"path": [[0.5, 0.5], [0.5, 0.5]]}, "must not all be the same"),
    ({"path": [[0.5, 0.5], [1.5, 0.5]]}, "within 0..1"),
    ({"path": [[0.1, 0.1], [0.2, 0.2]], "x": 0.5}, "replaces x"),
], ids=["BAD-path-one-point", "BAD-path-off-map", "BAD-path-and-x"])
def test_bad_path_prop_rejected(project, bad, msg):
    from tests.test_props import _proj, MAP_PROP
    with pytest.raises(ValidationError, match=msg):
        _proj(project, {**{k: v for k, v in MAP_PROP.items() if k not in ("x", "y")}, **bad})


# schema and T02 -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cfg, msg", [
    ({"regions": [{"entity": "A", "polygon": SQ, "at_frame": 10, "changes": [{"at_frame": 10, "entity": "B"}]}]},
     "must be > 10"),
    ({"regions": [{"entity": "A", "polygon": SQ, "label_at": [0.5, 0.5]}]}, "need a label"),
    ({"regions": [{"entity": "A", "polygon": SQ, "style": "slab"}]}, "'tint' or 'block'"),
    ({"borders": [{"points": [[0.1, 0.1], [0.2, 1.2]]}]}, "within 0..1"),
    ({"morph": {"asset": "assets/map.jpg", "at_frame": 20, "back_at_frame": 10}}, "must be > at_frame"),
], ids=["BAD-change-order", "BAD-label-at-without-label", "BAD-style", "BAD-border-off-map", "BAD-morph-back"])
def test_bad_upgrade_schema(project, cfg, msg):
    with pytest.raises(ValidationError, match=msg):
        project(_sc(**cfg))


def test_bad_frames_after_the_segment_fail_T02(project):
    p, tl = project(_sc(regions=[{"entity": "A", "polygon": SQ, "label": "A", "label_at_frame": 70,
                                  "changes": [{"at_frame": 80, "entity": "B"}]}],
                        borders=[{"points": SQ, "at_frame": 61}],
                        morph={"asset": "assets/nope.jpg", "at_frame": 65}))
    msgs = [f.message for f in layer1.t02_integrity(tl, p) if f.level == "fail"]
    for want in ("label_at_frame 70", "change to 'B' at_frame 80", "border 0 at_frame 61", "morph at_frame 65",
                 "morph asset not found"):
        assert any(want in m for m in msgs), want


# rendered DOM --------------------------------------------------------------------------------------------------------
def test_good_block_and_box_label_render_and_pass_E01_and_R64(project, monkeypatch, tmp_path):
    _boxed(monkeypatch)
    p, tl = project(_sc(regions=[{"entity": "A", "polygon": SQ, "label": "ALPHA"}], title="JUDUL"))
    res, sc = _probe(tl, p, tmp_path, (1.5,))
    item = res["times"][0]
    blocks = [q for q in item["paints"] if q["kind"] == "block"]
    assert blocks and all(layer2._rgb_hex(q["color"]) == sc["regions"][0]["block"] for q in blocks)
    assert [f.level for f in layer2.e01_entity_colours(tl, {2: res})] == ["pass"]
    rows = {r["id"]: r for r in layer2.r64_box_rows({2: res})}
    assert rows["region:0"]["bg"] == sc["regions"][0]["box"] and rows["region:0"]["ink"] == "#ffffff"
    assert rows["title"]["bg"] == TH["label_box_colour"] and rows["title"]["lc"] >= rows["title"]["target"]


YEAR_BOX = dict(year_box_colour="#f6efe0", year_box_ink="#2a231d", year_box_px=88, year_box_texture="grain-paper002.jpg")


def test_good_year_box_is_textured_light_with_dark_ink_and_passes_R64(project, monkeypatch, tmp_path):
    _theme(monkeypatch, label_style="box", **YEAR_BOX)
    p, tl = project(_sc(figures=[{"kind": "year", "value": 1893, "at_frame": 0, "position": [0.5, 0.3]},
                                 {"kind": "number", "value": 7, "at_frame": 0, "position": [0.5, 0.6]}]))
    res, sc = _probe(tl, p, tmp_path, (1.5,))
    year, num = sc["figures"]
    assert year["box"] == "#f6efe0" and year["texture"] == "grain-paper002.jpg" and 0.5 < year["dim"] < 1
    assert num["box"] == TH["label_box_colour"] and not num.get("texture")
    assert (tmp_path / "hf" / "assets" / "grain-paper002.jpg").is_file()
    rects = {r["id"]: r for r in res["times"][0]["rects"]}
    assert rects["figure:0"]["h"] < rects["figure:1"]["h"] * 0.75     # 88 px against figure_px
    rows = {r["id"]: r for r in layer2.r64_box_rows({2: res})}
    dim = round(0xf6 * year["dim"])
    assert rows["figure:0"]["ink"] == "#2a231d" and rows["figure:0"]["bg"].lower().startswith(f"#{dim:02x}")
    assert rows["figure:0"]["lc"] >= rows["figure:0"]["target"]


def test_bad_R64_counts_the_darkest_grain_of_a_box_texture():
    rect = {"id": "figure:0", "text": "1893", "opacity": 1, "color": "rgb(140, 140, 140)", "bg": "rgb(246, 239, 224)",
            "box": "60"}
    (clean,) = layer2.r64_box_rows({2: {"times": [{"t": 1.0, "rects": [rect]}]}})
    (grainy,) = layer2.r64_box_rows({2: {"times": [{"t": 1.0, "rects": [{**rect, "box_dim": "0.8"}]}]}})
    assert grainy["lc"] < clean["lc"] and grainy["lc"] < 60


def test_bad_year_box_texture_without_colour_or_file_rejected(project, monkeypatch):
    with pytest.raises(ValidationError, match="year_box_texture"):
        profiles.load().theme.model_validate({**profiles.load().theme.model_dump(), "year_box_texture": "x.jpg"})
    _theme(monkeypatch, label_style="box", **{**YEAR_BOX, "year_box_texture": "missing.jpg"})
    p, tl = project(_sc(figures=[{"kind": "year", "value": 1893, "at_frame": 0, "position": [0.5, 0.3]}]))
    with pytest.raises(EngineError, match="missing.jpg"):
        hyperframes.scene_data(tl.segments[2], tl, p)


def test_bad_R64_fails_a_box_below_its_lc():
    rect = {"id": "region:0", "text": "A", "opacity": 1, "color": "rgb(255, 255, 255)", "bg": "rgb(250, 220, 120)",
            "box": "60"}
    (row,) = layer2.r64_box_rows({2: {"times": [{"t": 1.0, "rects": [rect]}]}})
    assert row["lc"] < row["target"] == 60


def test_good_region_change_repaints_block_and_box(project, monkeypatch, tmp_path):
    _boxed(monkeypatch)
    p, tl = project(_sc(regions=[{"entity": "A", "polygon": SQ, "label": "ALPHA",
                                  "changes": [{"at_frame": 15, "entity": "B"}]}]))
    res, sc = _probe(tl, p, tmp_path, (0.3, 1.8))
    r = sc["regions"][0]
    before, after = ({layer2._rgb_hex(q["color"]) for q in i["paints"] if q["kind"] == "block"} for i in res["times"])
    assert before == {r["block"]} and after == {r["changes"][0]["block"]}
    box = [next(x for x in i["rects"] if x["id"] == "region:0")["bg"] for i in res["times"]]
    assert [layer2._rgb_hex(b) for b in box] == [r["box"], r["changes"][0]["box"]]


def test_good_morph_crossfades_a_second_map_in(project, tmp_path):
    import numpy as np
    d, _ = project()
    Image.new("RGB", (750, 800), (200, 30, 30)).save(d / "assets" / "paper.jpg")
    p, tl = project(_sc(morph={"asset": "assets/paper.jpg", "at_frame": 20, "s": 0.3, "sepia": 0, "paper": 0}))
    out, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    assert (out / sc["morph"]["img"]).is_file()
    res = hyperframes.probe_dom(out, [0.2, 1.5], 1080, 1920, tmp_path / "snap")
    a, b = (np.asarray(Image.open(i["png"]).convert("RGB"), dtype=np.int16)[900:1000, 400:600] for i in res["times"])
    red = lambda x: float((x[..., 0] - x[..., 1:].mean(axis=-1)).mean())
    assert red(b) > red(a) + 60                    # the red morph map covers the base map after its fade


def test_bad_morph_with_another_aspect_ratio_is_rejected(project, tmp_path):
    d, _ = project()
    Image.new("RGB", (800, 400), (0, 0, 0)).save(d / "assets" / "wide.jpg")
    p, tl = project(_sc(morph={"asset": "assets/wide.jpg"}))
    with pytest.raises(EngineError, match="aspect ratio"):
        hyperframes.scene_data(tl.segments[2], tl, p)


# globe ---------------------------------------------------------------------------------------------------------------
def test_good_globe_regions_project_to_the_screen_and_labels_carry_lat_lng(project, monkeypatch):
    from tests.test_globe import _globe_proj, KEYS
    _boxed(monkeypatch)
    poly = [[(lng + 180) / 360, (90 - lat) / 180] for lat, lng in ((25, 25), (25, 35), (15, 35), (15, 25))]
    d, _ = _globe_proj(project)
    def mut(doc):
        sc = doc["segments"][2]["gsap_scene_config"]
        for k in ("camera_path", "camera_preset", "map_bounds"):
            sc.pop(k, None)
        sc.update(map_asset="assets/globe.jpg", pins=[], globe={"keys": KEYS},
                  regions=[{"entity": "A", "polygon": poly, "label": "A"}])
    p, tl = project(mut)
    r = hyperframes.scene_data(tl.segments[2], tl, p)["regions"][0]
    assert r["ll"][0] == pytest.approx([25, 25], abs=1e-4) and r["label"]["ll"] == pytest.approx([20, 30], abs=0.6)
    assert all(0 < x < 1080 and 0 < y < 1920 for x, y in r["points"])    # lat 20 lng 30 faces the first key


@pytest.mark.parametrize("extra", [{"borders": [{"points": SQ[:2]}]}, {"morph": {"asset": "assets/map.jpg"}}],
                         ids=["BAD-globe-borders", "BAD-globe-morph"])
def test_bad_globe_rejects_borders_and_morph(project, extra):
    from tests.test_globe import KEYS
    def mut(doc):
        sc = doc["segments"][2]["gsap_scene_config"]
        sc.pop("camera_preset", None)
        sc.update(globe={"keys": KEYS}, pins=[], **extra)
    with pytest.raises(Exception, match="globe"):
        project(mut)
