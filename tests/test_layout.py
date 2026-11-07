"""Beats layout of map scenes (profile [layout], video_engine/layout.py) and gates L01-L10. Labelled GOOD and BAD."""
import json
import math

import pytest
from pydantic import ValidationError

from video_engine import config, layout, profiles, timeline
from video_engine.gates import layout_gates as G
from video_engine.layout import Item, LayoutProfile, Span
from video_engine.util import blake3_json

L = LayoutProfile()


def T(key, kind, box, px=52.0, text="Teks", **kw):
    return Item(key, kind, box, px, text, **kw)


def region(i, poly, level=1.0):
    return Item(f"region:{i}", "region", None, poly=poly, level=level)


SQUARE = [(100, 500), (800, 500), (800, 1000), (100, 1000)]


# L01 overlap ----------------------------------------------------------------------------------------------------------
def test_good_l01_texts_apart_by_text_clear():
    a = T("label:0", "label", (100, 600, 300, 650), pin=0)
    b = T("label:1", "label", (100, 666, 300, 700), pin=1)       # 16 px below
    assert G.overlap([a, b], L) == []


def test_bad_l01_texts_closer_than_text_clear():
    a = T("label:0", "label", (100, 600, 300, 650), pin=0)
    b = T("label:1", "label", (100, 660, 300, 700), pin=1)       # 10 px below
    assert "closer than 16 px" in G.overlap([a, b], L)[0]


def test_good_l01_label_may_sit_offset_from_its_own_head():
    h = T("head:0", "head", (100, 600, 144, 644), pin=0)
    lab = T("label:0", "label", (150, 600, 400, 650), pin=0)
    assert G.overlap([h, lab], L) == []


def test_bad_l01_label_on_another_pin_head_and_leader_through_text():
    h = T("head:1", "head", (100, 600, 144, 644), pin=1)
    lab = T("label:0", "label", (150, 600, 400, 650), pin=0)
    ld = Item("leader:2", "leader", None, pin=2, seg=((120, 500), (500, 630)))
    probs = G.overlap([h, lab, ld], L)
    assert any("head:1" in m for m in probs) and any("leader:2 crosses label:0" in m for m in probs)


def test_good_l01_data_card_is_one_group():
    y = T("fig:0", "year", (51, 311, 348, 438), 128)
    t = T("fig:1", "time", (359, 374, 543, 432), 56)
    assert G.overlap([y, t], L) == []


def test_bad_l01_dom_box_differs_from_plan():
    it = T("label:0", "label", (0, 0, 1, 1), css=(100, 600, 300, 652), pin=0)
    good = [{"id": "pin:0", "x": 101, "y": 601, "w": 200, "h": 52, "opacity": 1}]
    bad = [{"id": "pin:0", "x": 140, "y": 601, "w": 200, "h": 52, "opacity": 1}]
    assert G.dom_match([it], good) == []                                   # GOOD
    assert "rendered at" in G.dom_match([it], bad)[0]                      # BAD
    assert "rendered but not planned" in G.dom_match([], good)[0]          # BAD: unmanaged element visible


# L02 boundary ---------------------------------------------------------------------------------------------------------
def test_good_l02_label_inside_region_with_edge_clear():
    assert G.boundary([region(0, SQUARE), T("label:0", "label", (200, 600, 500, 650), pin=0)], L) == []


def test_bad_l02_label_runs_off_the_region_edge():
    probs = G.boundary([region(0, SQUARE), T("label:0", "label", (600, 600, 900, 650), text="Jembatan Merah", pin=0)], L)
    assert "crosses region 0 edge" in probs[0]


def test_good_and_bad_l02_area_label_inset():
    cs_ok = layout.rot_corners(450, 750, 240, 48, 0)
    cs_bad = layout.rot_corners(220, 750, 240, 48, 0)                     # 20 px from the left edge
    ok = T("area:0", "area", layout.corners_box(cs_ok), 44, "SEKUTU", poly=cs_ok, region=0)
    bad = T("area:0", "area", layout.corners_box(cs_bad), 44, "SEKUTU", poly=cs_bad, region=0)
    assert G.boundary([region(0, SQUARE), ok], L) == []
    assert "not inside region 0" in G.boundary([region(0, SQUARE), bad], L)[0]


# L03 budget -----------------------------------------------------------------------------------------------------------
def test_good_l03_four_texts_two_animating():
    items = [T(f"label:{i}", "label", (0, 0, 1, 1)) for i in range(2)] + [T("title:0", "title", (0, 0, 1, 1), 112),
                                                                          T("caption:0", "caption", (0, 0, 1, 1), 72)]
    assert G.budget(items, L, 2) == []


def test_bad_l03_five_texts_two_big_numbers_two_regions_three_animating():
    items = [T(f"label:{i}", "label", (0, 0, 1, 1)) for i in range(3)] + \
            [T("fig:0", "year", (0, 0, 1, 1), 128), T("fig:1", "time", (0, 0, 1, 1), 100)] + \
            [region(0, SQUARE), region(1, SQUARE)]
    probs = " | ".join(G.budget(items, L, 3))
    for want in ("5 texts", "3 labelled pins", "3 elements animating", "2 numbers of 100 px", "2 highlighted regions"):
        assert want in probs


# L04 hero -------------------------------------------------------------------------------------------------------------
def test_good_l04_year_is_hero_caption_excluded():
    items = [T("fig:0", "year", None, 128), T("fig:1", "time", None, 56), T("area:0", "area", None, 44),
             T("caption:0", "caption", None, 120)]
    assert G.hero(items, L) == []


def test_bad_l04_two_similar_sizes():
    assert "1.5 x next" in G.hero([T("title:0", "title", None, 112), T("fig:0", "year", None, 100)], L)[0]


# L05 grid -------------------------------------------------------------------------------------------------------------
def card(time_ink_x0=367.0, time_base=421.0, top=304.0, year_x0=64.0):
    y = T("fig:0", "year", (0, 0, 1, 1), 128, "1945", ink=(year_x0, 324, 335, 423), css=(year_x0, top, 334, top + 128),
          base=421.0)
    t = T("fig:1", "time", (0, 0, 1, 1), 56, "06.00", ink=(time_ink_x0, 382, 535, 422), css=(367, 374, 535, 430),
          base=time_base)
    return [y, t]


def test_good_l05_card_on_grid_and_baseline():
    assert G.grid(card(), L) == []


def test_bad_l05_off_column_off_unit_off_baseline_wrong_gap():
    probs = " | ".join(G.grid(card(time_ink_x0=380, time_base=425, top=310, year_x0=90), L))
    for want in ("off the column edges", "not a multiple of 8", "baseline", "after the year ink"):
        assert want in probs


# L06 zones ------------------------------------------------------------------------------------------------------------
def test_good_l06_title_in_z1_label_in_z2():
    items = [T("title:0", "title", (50, 307, 600, 415), 112, ink=(64, 320, 586, 400)),
             T("label:0", "label", (200, 600, 500, 650))]
    assert G.zones(items, L) == []


def test_bad_l06_title_in_z2_label_in_bleed():
    items = [T("title:0", "title", (50, 507, 600, 615), 112, ink=(64, 520, 586, 600)),
             T("label:0", "label", (700, 1080, 1000, 1130))]
    probs = " | ".join(G.zones(items, L))
    assert "outside Z1" in probs and "outside Z2" in probs and "safe area" in probs


# L07 routes -----------------------------------------------------------------------------------------------------------
def test_good_l07_route_clear_of_text():
    r = Item("route:0", "route", None, poly=[(100, 900), (300, 900)])
    assert G.route_clear([r, T("label:0", "label", (100, 600, 300, 650))], L) == []


def test_bad_l07_route_starts_at_the_title():
    r = Item("route:0", "route", None, poly=[(300, 400), (300, 900)])
    probs = " | ".join(G.route_clear([r, T("title:0", "title", (64, 304, 586, 420), 112)], L))
    assert "starts or ends at text title:0" in probs and "px from title:0" in probs


# L08 stability --------------------------------------------------------------------------------------------------------
def plan_stub(tracks=None, spans=None, beats=None):
    return layout.Plan(1080, 1920, 30, 300, 1000, 1000, L, {}, [], {"legs": []}, [(0, 0)], ["A"], [], [], [],
                       spans or {}, [], tracks=tracks or {}, beats=beats or [])


def lab(name):
    return layout.Label(0, 0, 52, name)


def test_good_l08_one_hard_move():
    assert G.stability(plan_stub({0: [(10, lab("TR"), False), (100, lab("TL"), True)]})) == []


def test_bad_l08_two_moves_within_500ms_and_two_soft_moves():
    p = plan_stub({0: [(10, lab("TR"), False), (100, lab("TL"), False), (110, lab("BR"), False)]})
    probs = " | ".join(G.stability(p))
    assert "closer than 0.5 s" in probs and "2 non-urgent moves" in probs


# L09 caption ground ---------------------------------------------------------------------------------------------------
def test_good_l09_caption_on_plain_basemap():
    cap = T("caption:0", "caption", (200, 1130, 880, 1250), 72, "pukul 6.")
    assert G.caption_ground([region(0, SQUARE), cap], L) == []


def test_bad_l09_caption_inside_the_red_fill():
    cap = T("caption:0", "caption", (200, 900, 700, 1000), 72, "pukul 6.")
    assert "over a highlighted fill" in G.caption_ground([region(0, SQUARE), cap], L)[0]


def test_good_l09_dimmed_region_is_not_highlighted():
    cap = T("caption:0", "caption", (200, 900, 700, 1000), 72, "pukul 6.")
    assert G.caption_ground([region(0, SQUARE, level=0.38), cap], L) == []


# L10 timing -----------------------------------------------------------------------------------------------------------
def test_good_l10_hold_stagger_exit():
    p = plan_stub(spans={"label:0": [Span(30, 9, 90, 8)], "head:0": [Span(24, 18, 96, 8)], "label:1": [Span(200, 9)]},
                  beats=[{"at": 0, "settle_end": 24, "entries": {"head:0": 24, "label:0": 30}, "exits": {}}])
    assert G.timing(p) == []


def test_bad_l10_short_hold_fast_stagger_entry_before_settle_long_exit():
    p = plan_stub(spans={"label:0": [Span(26, 9, 50, 15)], "head:0": [Span(24, 18, 96, 8)]},
                  beats=[{"at": 0, "settle_end": 25, "entries": {"head:0": 24, "label:0": 26}, "exits": {}}])
    p.spans["label:1"] = [Span(280, 9)]                                    # n = 300: on screen 0.37 s at the end
    probs = " | ".join(G.timing(p))
    for want in ("held", "exit 0.50 s", "before the camera settles", "staggered", "before the segment ends"):
        assert want in probs


# models ---------------------------------------------------------------------------------------------------------------
def test_good_settle_key_with_move_s_and_bad_move_s_on_glide():
    timeline.CameraKey(at_frame=10, focus=[0.5, 0.5], move="settle", move_s=0.8)
    with pytest.raises(ValidationError, match="move_s applies to move 'settle' only"):
        timeline.CameraKey(at_frame=10, focus=[0.5, 0.5], move="glide", move_s=0.8)


def test_bad_beats_title_and_card_together_and_out_of_range():
    with pytest.raises(ValidationError, match="title or the data card"):
        timeline.Beat(at_frame=0, title=True, card=[0])
    with pytest.raises(ValidationError, match="pins index 3 out of range"):
        timeline.GsapScene(map_asset="m.jpg", pins=[{"label": "A", "x": 0.5, "y": 0.5}], beats=[{"at_frame": 0, "pins": [3]}])


def test_bad_layout_profile_gap_off_the_unit():
    with pytest.raises(ValidationError, match="multiple of grid.unit"):
        LayoutProfile.model_validate({"labels": {"offset": 15}})


def test_good_default_profile_has_no_layout_and_keeps_its_hash():
    p = profiles.load()
    assert p.layout is None and p.print is None
    dump = json.loads(p.data.model_dump_json())
    for k in ("layout", "print", "camera", "motion", "transitions", "render", "grade", "resources", "flags", "fx", "type", "format", "framing"):   # off/default tables add nothing
        dump.pop(k)
    assert dump["gates"].pop("camera") is None
    assert dump["gates"].pop("anti_slop") is not None   # default rule levels add nothing to the hash
    for k in ("cta", "ending_keyword", "duration_exact", "geo"):
        assert dump["gates"].pop(k) is None      # absent new gate tables add nothing either
    assert dump["gates"].pop("require") == []   # an empty required list adds nothing either
    assert dump["gates"].pop("advisory") == []  # nor does an empty advisory list
    dump["theme"].pop("reveal_ease")             # the default reveal ease adds nothing (theme.reveal_ease)
    assert p.blake3 == blake3_json(dump)
    # Shipped default.toml, the same digest test_landscape and test_gate_severity lock in full.
    assert p.blake3 == "1048453ef3a189d0b65c981fe187575821cf5520bc26298dd48282ff72e61181"
    assert profiles.load("geoverum").layout is not None


# planner on a synthetic project ---------------------------------------------------------------------------------------
def beats_doc(doc):
    doc["profile"] = "geoverum"
    s = doc["segments"][2]
    s["end_frame"] = 620
    s["gsap_scene_config"] = {
        "map_asset": "assets/map.jpg", "map_asset_id": "map", "title": "SEKUTU",
        "pins": [{"label": "Titik A", "x": 0.45, "y": 0.40}, {"label": "Titik B", "x": 0.45, "y": 0.58}],
        "regions": [{"entity": "Sekutu", "polygon": [[0.3, 0.33], [0.7, 0.33], [0.7, 0.47], [0.3, 0.47]]},
                    {"entity": "Indonesia", "polygon": [[0.36, 0.5], [0.66, 0.5], [0.66, 0.62], [0.36, 0.62]]}],
        "routes": [{"points": [[0.4, 0.3], [0.45, 0.40]], "to_pin": 0}],
        "figures": [{"kind": "year", "value": 1945}],
        "beats": [{"at_frame": 0, "region": 0, "pins": [0], "routes": [0], "title": True},
                  {"at_frame": 180, "region": 0, "markers": [0], "card": [0],
                   "area_labels": [{"text": "Sekutu", "region": 0}], "camera": False},
                  {"at_frame": 360, "region": 1, "pins": [1]}]}


def no_print(monkeypatch):
    """Geoverum without [print]: the Z1 title and data card layout."""
    real = profiles.of

    def of(tl):
        p = real(tl)
        if p.name != "geoverum":
            return p
        return profiles.Profile(p.name, p.data.model_copy(update={"print": None}), p.blake3 + "-no-print")
    monkeypatch.setattr(profiles, "of", of)


def test_good_planner_plan_passes_every_layout_gate(project, monkeypatch):
    no_print(monkeypatch)
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    p = layout.apply_segment(seg, tl, d)
    assert p is not None and seg.gsap_scene_config.camera_path[0].at_frame == 0
    assert abs(p.title["ink"][0] - 64) < 0.5 and p.title["y"] % 8 == 0
    assert all(p.tracks[i] and p.tracks[i][0][1].name in L.labels.order for i in (0, 1)) and p.log == []
    fs = G.check_segment(seg, tl, d, [], None)
    fails = [f.line() for f in fs if f.level == "fail"]
    assert fails == []
    assert {f.gate for f in fs if f.level == "pass"} == set(G.GATES)


def test_good_layout_grid_and_label_offset_scale_with_width_and_height(project, monkeypatch):
    """Grid steps, Z1 rounding and label offsets use kx/ky, not raw 1080x1920 px."""
    no_print(monkeypatch)
    d1, tl1 = project(beats_doc)
    d2, tl2 = project(lambda d: (beats_doc(d), d["settings"].update(width=1188, height=2112)))
    layout.apply_segment(tl1.segments[2], tl1, d1)
    p2 = layout.apply_segment(tl2.segments[2], tl2, d2)
    ky = 2112 / 1920
    assert p2.title["y"] == pytest.approx(304 * ky)
    assert abs(p2.title["y"] / (8 * ky) - round(p2.title["y"] / (8 * ky))) < 1e-9
    c1 = layout._Planner(tl1.segments[2], tl1, d1).candidates("Titik A")[0]
    c2 = layout._Planner(tl2.segments[2], tl2, d2).candidates("Titik A")[0]
    assert c2.dx - c1.dx == pytest.approx(L.labels.offset * (1188 / 1080 - 1))


def test_good_label_style_box_reaches_layout_titles_and_figures(project, monkeypatch):
    """label_style box is no longer limited to scenes without a layout (print still owns the look when it is on)."""
    no_print(monkeypatch)
    real = profiles.of

    def boxed(tl):
        p = real(tl)
        th = p.data.theme.model_copy(update={"label_style": "box"})
        return profiles.Profile(p.name, p.data.model_copy(update={"theme": th}), p.blake3 + "-box")
    monkeypatch.setattr(profiles, "of", boxed)
    d, tl = project(beats_doc)
    from video_engine import hyperframes
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    assert sc["title"]["box"] and all("box" in f for f in sc["figures"])


@pytest.mark.skipif(not config.NODE.is_file(), reason="vendor node/Chrome not installed")
def test_good_print_heading_renders_with_layout_without_legibility(project, tmp_path, monkeypatch):
    """[print] depends on [layout], not on [legibility]: its heading element is still built and probed."""
    real = profiles.of

    def of(tl):
        p = real(tl)
        if p.name != "geoverum":
            return p
        return profiles.Profile(p.name, p.data.model_copy(update={"legibility": None}), p.blake3 + "-noleg")

    monkeypatch.setattr(profiles, "of", of)

    def doc(d):
        beats_doc(d)
        d["segments"][2]["gsap_scene_config"]["beats"][1]["heading"] = "10 November"

    d, tl = project(doc)
    from video_engine import hyperframes
    hf, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(hf, [(120 + 110 + 40) / 30], 1080, 1920)
    ids = {r["id"] for it in res["times"] for r in it["rects"]}
    assert "heading:1" in ids   # keyed by beat index


def test_good_print_title_at_frame_is_the_regions_entry_not_the_last_frame(project):
    """[print] titles live in an area span; title_at_frame follows that span, never the segment end."""
    d, tl = project(beats_doc)
    seg = tl.segments[2]
    p = layout.apply_segment(seg, tl, d)
    ta = next(a for a in p.areas.values() if a.title)
    assert seg.gsap_scene_config.title_at_frame == p.spans[f"area:{ta.key}"][0].t_in
    assert 0 <= seg.gsap_scene_config.title_at_frame < seg.n_frames - 1


def test_good_hidden_print_title_does_not_show_at_the_last_frame(project):
    def doc(d):
        beats_doc(d)
        d["segments"][2]["gsap_scene_config"]["beats"] = [{"at_frame": 0, "pins": [0], "title": True}]

    d, tl = project(doc)
    seg = tl.segments[2]
    layout.apply_segment(seg, tl, d)
    assert seg.gsap_scene_config.title_at_frame == 0


def test_good_area_label_outside_a_small_region_keeps_its_leader(project, monkeypatch):
    """A label that cannot fit inside its region is placed outside with a leader; the template draws that leader."""
    no_print(monkeypatch)
    real = profiles.of

    def of(tl):
        pr = real(tl)
        if pr.name != "geoverum":
            return pr
        return profiles.Profile(pr.name, pr.data.model_copy(update={"camera": profiles.CameraProfile(max_zoom=1.0)}),
                                pr.blake3 + "-z1")

    monkeypatch.setattr(profiles, "of", of)

    def doc(d):
        beats_doc(d)
        sc = d["segments"][2]["gsap_scene_config"]
        sc["regions"] = [{"entity": "Sekutu", "polygon": [[0.48, 0.48], [0.52, 0.48], [0.52, 0.52], [0.48, 0.52]]}]
        sc["beats"] = [{"at_frame": 0, "region": 0,
                        "area_labels": [{"text": "SEKUTU RAYA SEKALI", "region": 0}]}]

    d, tl = project(doc)
    p = layout.apply_segment(tl.segments[2], tl, d)
    areas = [a for a in p.areas.values() if a.text == "SEKUTU RAYA SEKALI"]
    assert areas and areas[0].leader is not None and not areas[0].inside
    html = (config.TEMPLATE_DIR / "index.html").read_text()
    assert "leader:area:'+a.key" in html and "if(a.leader)" in html


def test_good_area_leader_is_drawn_by_the_template():
    """An area label placed outside its polygon gets a rendered leader, not only a gate-only segment."""
    html = (config.TEMPLATE_DIR / "index.html").read_text()
    assert "if(a.leader){" in html and "'leader:area:'+a.key" in html and "cam.insertBefore(ln,el)" in html and "ZS=window.__zs" in html and "window.__zsArm()" in html


def test_good_print_block_no_longer_requires_legibility():
    """The template print block guards only [print] and [layout]; its legibility reads sit behind `if(A)`."""
    html = (config.TEMPLATE_DIR / "index.html").read_text()
    assert "if(!P||!LY)return;" in html
    assert "if(!P||!LY||!LG)return;" not in html


def test_bad_planner_beats_without_layout_profile(project):
    def mut(doc):
        beats_doc(doc)
        doc["profile"] = "default"
    d, tl = project(mut)
    fs = G.run(tl, d, [], None)
    assert fs[0].gate == "L06" and fs[0].level == "fail" and "[layout]" in fs[0].message


# legibility sample windows follow the layout spans --------------------------------------------------------------------
def leg_scene(lay=None):
    sc = {"fps": 30, "n_frames": 300, "theme": {"pin_style": "teardrop", "pin_label_px": 52, "title_px": 112,
                                                 "figure_px": 128},
          "pins": [{"at_frame": 30}], "title": {"at_frame": 10}, "figures": [{"at_frame": 60, "motion": "pop"}]}
    if lay:
        sc["layout"] = lay
    return sc


def test_good_legibility_samples_only_while_the_beat_shows_the_element():
    from video_engine import legibility
    lay = {"els": {"label:0": [[1.0, 0.3, 3.0, 0.25], [6.0, 0.3, None, 0.0]], "title:0": [[0.5, 0.3, 2.0, 0.25]]}}
    els = {e["id"]: e for e in legibility.elements(leg_scene(lay))}
    assert (round(els["pin:0"]["start"], 3), round(els["pin:0"]["end"], 3)) == (6.4, round(299 / 30 - 1 / 30, 3))
    assert round(els["title"]["end"], 3) == round(2.0 - 1 / 30, 3)
    assert "figure:0" not in els                                           # no span: never shown, nothing to sample


def test_bad_legibility_without_layout_samples_to_the_segment_end():
    from video_engine import legibility
    els = {e["id"]: e for e in legibility.elements(leg_scene())}
    assert els["title"]["end"] == 299 / 30                                 # a beats exit at 2 s would be sampled hidden


# GOOD / BAD: [print] camera fit frames the above-pin label; the fit zoom stops at the camera key limit ------------------
def _planner(project, monkeypatch=None, mut=None):
    if monkeypatch:
        no_print(monkeypatch)

    def doc(d):
        beats_doc(d)
        if mut:
            mut(d)
    d, tl = project(doc)
    return layout._Planner(tl.segments[2], tl, d)


def test_good_print_fit_includes_the_label_above_the_pin(project):
    P = _planner(project)
    b = P.sc.beats[2]                                   # region 1 + pin 1
    lift = P.label_lift()
    assert lift > P.pr.label_px                         # gap + leader + gap + one label line
    top = P.content_box(b, 1.0)[1]
    assert top <= P.pins[1][1] + layout.HEAD[1] - lift + 1e-6


def test_bad_without_print_no_lift_labels_sit_beside_the_head(project, monkeypatch):
    P = _planner(project, monkeypatch)
    assert P.label_lift() == 0.0


def test_good_fit_of_a_lone_pin_stops_at_zoom_max(project):
    def lone(d):
        d["segments"][2]["gsap_scene_config"]["beats"] = [{"at_frame": 0, "pins": [0]}]
    P = _planner(project, mut=lone)
    _, z = P.fit(P.sc.beats[0])
    pulse = P.theme.get("camera_pulse")      # push-in pulses: the key leaves headroom, the pulse peak stops there
    peak = z * math.exp(pulse["zoom"]) if pulse and pulse["dir"] > 0 else z
    assert peak == pytest.approx(P.max_zoom, abs=1e-3) and z <= P.max_zoom
    timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], zoom=z)          # a valid camera key


def test_bad_camera_key_beyond_zoom_max_is_rejected():
    with pytest.raises(ValidationError):
        timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], zoom=timeline.ZOOM_LIMIT + 0.01)


# GOOD / BAD: L04 hero ignores hand notes (Kalam annotations), not other text ------------------------------------------
def test_good_l04_hand_note_excluded_from_hero():
    items = [T("title:0", "title", None, 88), T("label:0", "label", None, 54), T("note:0", "note", None, 76)]
    assert G.hero(items, L) == []


def test_bad_l04_note_sized_label_still_counts():
    items = [T("title:0", "title", None, 88), T("label:0", "label", None, 76)]
    assert "1.5 x next" in G.hero(items, L)[0]


def test_bad_layout_budget_animating_must_be_positive():
    with pytest.raises(ValidationError, match="animating"):
        LayoutProfile.model_validate({"budget": {"animating": 0}})
