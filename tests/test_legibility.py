"""Theme outline/shadow/glow paint (any profile) and the adaptive legibility of profile [legibility] (Geoverum):
APCA math, polarity choice, halo colour formula, escalation, polarity stability (hysteresis), gate R64, E01 relaxed
to region fills."""
import json

import numpy as np
import pytest
from PIL import Image

from video_engine import hyperframes, legibility as L, profiles
from video_engine.gates import layer2

SQ = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]
LEGIBLE = dict(text_outline_px=2.5, text_outline_colour="#1a1410", text_outline_alpha=0.75, text_shadow_dy_px=3,
               text_shadow_blur_px=8, text_shadow_alpha=0.6, pin_style="teardrop")
BLUE = "#557a9e"
GEO = profiles.load("geoverum").legibility.model_dump()
# the halo-first chooser (default ladder_order) on the Geoverum keys: a light halo, then the vignette, then darker ink
HALO_FIRST = {"halo_alpha": 0.6, "halo_alpha_max": 0.85, "vignette_alpha_min": 0.35, "vignette_alpha_max": 0.70,
              "vignette_feather": 0.4, "ladder_order": "halo_first", "polarity_fallback": True, "glow_colour": "ink",
              "entity_text_l_min": 0.0, "entity_text_l_max": 0.45, "entity_text_c_scale": 1.0}
LG = {**GEO, **HALO_FIRST}


def _cased(p):
    """The halo-first Geoverum keys before [print]: region outlines and leaders with a casing."""
    if p.name != "geoverum":
        return p
    lg = p.data.legibility.model_copy(update={"region_casing_px": 1.0, "leader_casing_px": 1.75, "entity_outline_px": 3.5,
                                              "basemap_chroma": 0.65, "entity_fill_alpha": 0.26})
    return profiles.Profile(p.name, p.data.model_copy(update={"legibility": lg}), p.blake3 + "-cased")


def _plain_labels(monkeypatch):
    """Geoverum with label_style "plain": pin labels, title and figures take the adaptive ink instead of a box."""
    real = profiles.of

    def of(tl):
        p = real(tl)
        th = p.data.theme.model_copy(update={"label_style": "plain"})
        return profiles.Profile(p.name, p.data.model_copy(update={"theme": th}), p.blake3 + "-plain")
    monkeypatch.setattr(profiles, "of", of)


def _halo_first(monkeypatch):
    """Geoverum timelines render with the halo-first chooser and without [print] (the halo and counter machinery)."""
    real = profiles.of

    def of(tl):
        p = real(tl)
        if p.name != "geoverum":
            return p
        d = p.data.model_copy(update={"legibility": p.data.legibility.model_copy(update=HALO_FIRST), "print": None})
        return profiles.Profile(p.name, d, p.blake3 + "-halo-first")
    monkeypatch.setattr(profiles, "of", of)
PAPER, DARK_MAP = [236, 226, 204], [38, 44, 52]
LABEL = {"id": "pin:0", "kind": "label", "start": 1.0, "end": 3.0, "entity": None, "px": 52}
TITLE = {"id": "title", "kind": "title", "start": 1.0, "end": 3.0, "entity": "Sekutu", "colour": "#4B4D8B", "px": 112}


def _profile(monkeypatch, **theme):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=theme)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("chan", data, "x"))


def _scene(**kw):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(**kw)
    return mut


def _geo(**kw):
    def mut(d):
        d["profile"] = "geoverum"
        d["segments"][2]["gsap_scene_config"].update(**kw)
    return mut


def _probe(tl, p, out, times=(1.5,), bg=""):
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, out / "hf")
    res = hyperframes.probe_dom(d, list(times), 1080, 1920, out / "snaps", bg=bg)
    return res, [{r["id"]: r for r in it["rects"]} for it in res["times"]], sc


def _sample(rgb, t=1.0, n=400, ring=None):
    """A uniform background sample (n px) under a 100 x 40 box; ring = adjacent pixels (default: the same colour)."""
    xy = np.stack([np.linspace(0, 100, n), np.linspace(0, 40, n)], axis=1)
    rgb = np.tile(np.asarray(rgb, float), (n, 1))
    rg = rgb[:40] if ring is None else np.tile(np.asarray(ring, float), (40, 1))
    return L.Sample(t, rgb, xy, (0, 0, 100, 40), rg, xy[:40])


def _grey(y):
    """sRGB grey with APCA Y = y."""
    return [255 * y ** (1 / 2.4)] * 3


# GOOD: colour math ----------------------------------------------------------------------------------------------------
def test_good_apca_reference_values():
    """apca-w3 0.1.9 reference pairs (Lc rounded): #888 on #fff 63.1, #fff on #888 -68.5, #000 on #aaa 58.1,
    #112233 on #ddeeff 91.7, #ddeeff on #112233 -93.1."""
    lc = lambda t, b: float(L.apca_lc(L.apca_y(L.hex_rgb(t)), L.apca_y(L.hex_rgb(b))))
    assert lc("#888888", "#ffffff") == pytest.approx(63.06, abs=0.1)
    assert lc("#ffffff", "#888888") == pytest.approx(-68.54, abs=0.1)
    assert lc("#000000", "#aaaaaa") == pytest.approx(58.15, abs=0.1)
    assert lc("#112233", "#ddeeff") == pytest.approx(91.67, abs=0.1)
    assert lc("#ddeeff", "#112233") == pytest.approx(-93.07, abs=0.1)
    assert lc("#777777", "#777777") == 0


def test_good_oklch_round_trip():
    for h in ("#136868", "#4b4d8b", "#b56a95", "#5f4025", "#f1e8d4"):
        assert L.oklch_hex(*L.rgb_oklch(L.hex_rgb(h))) == h


# GOOD / BAD: polarity choice ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bg, pol", [(PAPER, "dark"), (_grey(0.30), "dark"), (_grey(0.19), "dark"),
                                     (_grey(0.17), "light"), (DARK_MAP, "light")],
                         ids=["GOOD-paper-dark", "GOOD-mid-dark-default", "GOOD-just-above-0.18-dark",
                              "GOOD-just-below-0.18-light", "GOOD-dark-map-light"])
def test_good_polarity_rule(bg, pol):
    assert L.polarity([_sample(bg)], LG) == pol


def test_bad_white_text_on_paper_is_never_chosen():
    c = L.choose(LABEL, [_sample(PAPER, t) for t in (1.0, 2.0, 3.0)], LG, 30)
    assert [st.polarity for _, st in c.segments] == ["dark"] and not c.flagged
    white = L.Style("light", L.light_ink(LG), "#ffffff", 0, 0, 0)
    assert not L.passes(L.evaluate(_sample(PAPER), white, LG), "label", LG, LG["lc_fail_margin"])


def test_good_dark_map_gets_light_text_with_capped_glow():
    c = L.choose(LABEL, [_sample(DARK_MAP, t) for t in (1.0, 2.0, 3.0)], LG, 30)
    (_, st), = c.segments
    assert st.polarity == "light" and not c.flagged
    assert LG["glow_px"] <= 4 and LG["glow_alpha"] <= 0.25 and LG["shadow_dy_px"] <= 2 and LG["shadow_alpha"] <= 0.35


# GOOD / BAD: halo colour formula --------------------------------------------------------------------------------------
def test_good_halo_is_background_p50_lifted_and_desaturated():
    p50 = L.hex_rgb("#7a9a6a")                                      # mid green map
    Lb, Cb, hb = L.rgb_oklch(p50)
    Lh, Ch, hh = L.rgb_oklch(L.hex_rgb(L.halo_colour(p50, LG)))
    assert LG["halo_l_min"] - 0.01 <= Lh <= LG["halo_l_max"] + 0.01
    assert Ch == pytest.approx(Cb * LG["halo_c_scale"], abs=0.01)
    assert abs((hh - hb + 180) % 360 - 180) < 6                     # same hue


@pytest.mark.parametrize("p50", ["#ffffff", "#fbfaf7", "#f1e8d4", "#3a5a8a"],
                         ids=["BAD-white-bg", "BAD-near-white", "BAD-paper", "BAD-saturated-blue"])
def test_bad_halo_is_never_white_nor_saturated(p50):
    h = L.halo_colour(L.hex_rgb(p50), LG)
    Lh, Ch, _ = L.rgb_oklch(L.hex_rgb(h))
    assert h != "#ffffff" and Lh <= LG["halo_l_max"] + 0.005
    assert Ch <= L.rgb_oklch(L.hex_rgb(p50))[1] * LG["halo_c_scale"] + 0.005


# GOOD / BAD: escalation -----------------------------------------------------------------------------------------------
def test_good_escalation_ladder_order():
    lad = L.ladder("dark", LABEL, LG, "#e9e2d6")
    steps = [st.step for st in lad]
    assert steps == sorted(steps) and steps[:2] == [0, 1] and 2 in steps and 3 in steps
    assert lad[0].halo_alpha == LG["halo_alpha"] and lad[1].halo_alpha == LG["halo_alpha_max"]
    vig = [st.vignette_alpha for st in lad if st.step == 2]
    assert vig[0] == LG["vignette_alpha_min"] and vig[-1] == LG["vignette_alpha_max"]
    ls = [L.rgb_oklch(L.hex_rgb(st.ink))[0] for st in lad]
    assert ls[0] == pytest.approx(LG["ink_l"], abs=0.01) and min(ls) >= LG["ink_l_min"] - 0.005
    assert all(b <= a + 1e-9 for a, b in zip(ls, ls[1:]))


def test_good_escalation_stops_at_first_passing_step():
    assert L.choose(LABEL, [_sample(PAPER)], LG, 30).segments[0][1].step == 0              # paper: plain halo
    mid = L.choose(LABEL, [_sample(_grey(0.26))], LG, 30).segments[0][1]                   # mid map: escalates
    assert mid.polarity == "dark" and mid.step >= 1


def test_bad_unreachable_target_is_flagged():
    """Dark map with dark adjacent pixels at the rule polarity forced off: nothing passes -> flagged, R64 fails."""
    lg = {**LG, "light_p50_max": 0.0, "halo_alpha_max": 0.6, "vignette_alpha_max": 0.35, "vignette_alpha_min": 0.35}
    c = L.choose(LABEL, [_sample(_grey(0.12), ring=[0, 0, 0])], {**lg, "light_l": 0.3}, 30)
    assert c.flagged


def test_good_entity_text_ink_capped_and_ochre_never_text():
    assert L.rgb_oklch(L.hex_rgb(L.dark_ink(TITLE, LG)))[0] <= LG["entity_text_l_max"] + 0.005
    assert L.dark_ink({**TITLE, "colour": "#C99D4E"}, LG) == LG["text_fallback"].lower()


# GOOD / BAD: polarity stability (hysteresis) --------------------------------------------------------------------------
def _flicker(n=24, fps=30):
    """Background oscillating each frame around the polarity threshold (Y 0.16 / 0.20)."""
    return [_sample(_grey(0.16 if k % 2 else 0.20), t=1.0 + k / fps) for k in range(n)]


def test_bad_naive_per_frame_polarity_flickers():
    assert L.switches(L.naive_polarity(_flicker(), LG)) >= 20


def test_good_choice_holds_one_polarity_over_flicker():
    c = L.choose(LABEL, _flicker(), LG, 30)
    assert len(c.segments) == 1 and L.switches([st.polarity for _, st in c.segments]) == 0


NEAR_BLACK = [12, 12, 14]
CAPPED = {**LG, "halo_alpha_max": 0.6, "vignette_steps": 1, "vignette_alpha_min": 0.0, "vignette_alpha_max": 0.0}


def _paper_then_black(n=60):
    return [_sample(PAPER if k < n // 2 else NEAR_BLACK, t=1.0 + k / 30) for k in range(n)]


def test_good_schedule_switches_once_when_the_crossfade_midpoint_passes():
    """Title with lc_title 45 and a fast EMA (50 ms): paper for 1 s, then near-black for 1 s, escalation capped (halo
    0.6, no vignette) so no single style fits. The fallback switches once at 2.0 s; the crossfade midpoint (grey ink,
    halo alpha mostly faded) reaches Lc 40 there."""
    s, lg = _paper_then_black(), {**CAPPED, "lc_title": 45, "ema_tau_ms": 50}
    c = L.choose(TITLE, s, lg, 30)
    assert [st.polarity for _, st in c.segments] == ["dark", "light"] and not c.flagged
    assert c.segments[1][0] == pytest.approx(2.0)
    a, b = c.segments[0][1], c.segments[1][1]
    mid = L.midpoint(a, b, float(np.median(L.apca_y(s[30].rgb))), lg)
    assert L.passes(L.evaluate(s[30], mid, lg), "title", lg, lg["lc_fail_margin"])


def test_bad_schedule_never_switches_through_a_failing_grey_midpoint():
    """Same backgrounds at the profile values (lc_title 60, EMA 250 ms): at the crossfade midpoint the grey ink sits
    on a still-strong halo (Lc ~24 < 55), so the switch is refused and the element is flagged (R64 then reports it)
    instead of flashing through an unreadable grey."""
    c = L.choose(TITLE, _paper_then_black(), CAPPED, 30)
    assert len(c.segments) == 1 and c.flagged


def test_bad_schedule_ignores_runs_shorter_than_hold_frames():
    """A 4-frame dark blip (< hold_frames 8) inside paper never causes a switch."""
    s = [_sample(NEAR_BLACK if 10 <= k < 14 else PAPER, t=1.0 + k / 30) for k in range(40)]
    lads = {p: L.ladder(p, TITLE, CAPPED, "#ece4d6") for p in ("dark", "light")}
    c = L.schedule(TITLE, s, CAPPED, 30, "dark", lads)
    assert len(c.segments) == 1 and c.segments[0][1].polarity == "dark"


# GOOD / BAD: R64 threshold --------------------------------------------------------------------------------------------
def _r64_dom(tmp_path, bg_rgb, ink, pol="dark", halo="rgba(236, 226, 214, 0.6)", kind="label"):
    """One probed frame: a 300 x 120 background, a glyph block, the element rect and its ink state."""
    bg = np.zeros((120, 300, 3), np.uint8) + np.array(bg_rgb, np.uint8)
    mask = np.full((120, 300, 3), 255, np.uint8)
    for x in range(60, 240, 10):
        mask[40:80, x:x + 4] = 0                                    # vertical strokes, like glyph stems
    Image.fromarray(bg).save(tmp_path / "bg.png")
    Image.fromarray(mask).save(tmp_path / "mask.png")
    eid = {"label": "pin:0", "title": "title"}[kind]
    st = {"flag": False, "pol": pol, "ink": ink, "va": 0, "step": 0}
    r = {"id": eid, "text": "Surabaya", "x": 60, "y": 40, "w": 180, "h": 40, "opacity": 1, "color": ink,
         "stroke": halo, "stroke_px": 6.24, "paint_order": "stroke fill", "ink_state": json.dumps(st)}
    el = {**(LABEL if kind == "label" else TITLE), "start": 1.0, "end": 1.0}          # one sample time: t = 1.0
    item = {"t": 1.0, "rects": [r], "bg": str(tmp_path / "bg.png"), "mask": str(tmp_path / "mask.png"),
            "clip": {"x": 0, "y": 0, "w": 300, "h": 120}}
    return {0: {"times": [], "legibility": {"lg": LG, "elements": [el], "times": [item]}}}


def _geo_tl(project):
    return project(_geo())[1]


def test_good_R64_dark_ink_on_paper_passes(project, tmp_path):
    fs = layer2.r64_legibility(_geo_tl(project), _r64_dom(tmp_path, PAPER, "rgb(47, 33, 21)"))
    assert [f.level for f in fs] == ["pass"]
    (row,) = layer2.r64_rows(_r64_dom(tmp_path, PAPER, "rgb(47, 33, 21)"))
    assert row["lc"] >= LG["lc_label"] and row["polarity"] == "dark" and len(row["y"]) == 3


def test_good_R64_light_text_on_dark_map_passes(project, tmp_path):
    fs = layer2.r64_legibility(_geo_tl(project), _r64_dom(tmp_path, DARK_MAP, "rgb(246, 239, 226)", pol="light",
                                                         halo="rgba(0, 0, 0, 0)"))
    assert [f.level for f in fs] == ["pass"]


def test_bad_R64_dark_ink_on_dark_map_fails(project, tmp_path):
    fs = layer2.r64_legibility(_geo_tl(project), _r64_dom(tmp_path, DARK_MAP, "rgb(47, 33, 21)", halo="rgba(0, 0, 0, 0)"))
    assert fs and fs[0].level == "fail" and "pin:0" in fs[0].message


def test_bad_R64_fails_just_below_target_minus_margin_and_passes_just_above(project, tmp_path):
    """Ink grey chosen so Lc lands either side of lc_label - lc_fail_margin (70) on paper, halo off."""
    tl = _geo_tl(project)
    def lc_of(v):
        return float(L.apca_lc(L.apca_y([v] * 3), L.apca_y(PAPER)))
    lo = max(v for v in range(256) if lc_of(v) >= 71)                     # Lc ~71: pass
    hi = min(v for v in range(256) if lc_of(v) <= 68)                     # Lc ~68: fail
    ok = layer2.r64_legibility(tl, _r64_dom(tmp_path, PAPER, f"rgb({lo}, {lo}, {lo})", halo="rgba(0, 0, 0, 0)"))
    bad = layer2.r64_legibility(tl, _r64_dom(tmp_path, PAPER, f"rgb({hi}, {hi}, {hi})", halo="rgba(0, 0, 0, 0)"))
    assert [f.level for f in ok] in (["pass"], ["warn"]) and [f.level for f in bad][0] == "fail"


def test_bad_R64_fails_an_element_missing_at_a_sample_time(project, tmp_path):
    """An adaptive element the probe did not measure at one of its sample times (no ink state) fails R64."""
    dom = _r64_dom(tmp_path, PAPER, "rgb(47, 33, 21)")
    dom[0]["legibility"]["elements"][0]["end"] = 2.0                         # 12 sample times, 1 measured
    fs = layer2.r64_legibility(_geo_tl(project), dom)
    assert any(f.level == "fail" and "not measured at 11 of 12" in f.message for f in fs)
    del dom[0]["legibility"]["times"][0]["rects"][0]["ink_state"]
    dom[0]["legibility"]["elements"][0]["end"] = 1.0
    assert [f.level for f in layer2.r64_legibility(_geo_tl(project), dom)] == ["fail"]


def test_good_R64_off_without_legibility_section(project):
    _, tl = project()
    assert profiles.of(tl).legibility is None
    assert layer2.r64_legibility(tl, {2: {"times": [{"png": "x", "rects": []}]}}) == []


def test_bad_R64_ignores_a_declared_but_unpainted_stroke():
    """The gate reads the painted stroke: a stroke without paint-order stroke-under-fill counts as no halo."""
    assert layer2._stroke({"stroke_px": 5, "stroke": "rgba(26, 20, 16, 0.75)", "paint_order": "normal"})[1] == 0.0
    assert layer2._stroke({"stroke_px": 0, "stroke": "rgba(26, 20, 16, 0.75)", "paint_order": "stroke fill"})[1] == 0.0


# GOOD: end to end (Geoverum profile, real render) ---------------------------------------------------------------------
def test_good_geoverum_scene_paints_adaptive_ink_and_passes_R64(project, tmp_path, monkeypatch):
    _halo_first(monkeypatch)
    _plain_labels(monkeypatch)
    real = profiles.of
    monkeypatch.setattr(profiles, "of", lambda tl: _cased(real(tl)))
    p, tl = project(_geo(title="SEKUTU", title_entity="Sekutu", camera_preset="static",
                         regions=[{"entity": "Sekutu", "polygon": SQ}]))
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    assert sc["legibility"]["v"] == L.code_hash() and "title" in sc["inks"] and "pin:0" in sc["inks"]
    times = L.all_times(sc, sc["legibility"])
    res = hyperframes.probe_dom(d, times, 1080, 1920, tmp_path / "snaps", bg="bg")
    res["legibility"] = {"lg": sc["legibility"], "elements": L.elements(sc), "times": res["times"]}
    title = next(r for r in res["times"][-1]["rects"] if r["id"] == "title")
    assert L.state(title)["ink"] == sc["inks"]["title"]["segs"][0]["ink"]
    assert layer2._rgb_hex(title["color"]) == sc["inks"]["title"]["segs"][0]["ink"]
    hx = layer2._halo(title)                         # halo: ::after band, no text stroke
    assert title["stroke_px"] == 0 and hx["alpha"] > 0 and hx["w"] == pytest.approx(LG["halo_width_em"] * hx["px"])
    assert hx["blur"] == pytest.approx(LG["halo_blur_ratio"] * hx["w"])
    fs = layer2.r64_legibility(tl, {2: res})
    # text, halo geometry and counters pass; the pin head passes the line target; the indigo region outline crosses
    # the test card's saturated blue bar, where its opposite-tone casing carries the line above the fail floor
    assert not [f.message for f in fs if f.level == "fail"]
    lines = layer2.r64_line_rows({2: res})
    assert min(w["lc"] for w in lines if w["id"] == "pin:0") >= LG["lc_line"]
    assert min(w["lc"] for w in lines if w["id"] == "region:0") >= LG["lc_line_fail"]
    assert layer2.e01_entity_colours(tl, {2: res})[-1].level == "pass"


def test_good_default_profile_scene_has_no_adaptive_ink(project, tmp_path):
    p, tl = project(_scene(title="Surabaya"))
    _, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    assert "legibility" not in sc and "inks" not in sc and profiles.load().legibility is None


# GOOD / BAD: E01 relaxed to region fills ------------------------------------------------------------------------------
def test_good_E01_ignores_title_text_colour(project):
    _, tl = project(_scene(title="SEKUTU", title_entity="Sekutu"))
    want = layer2.entities.colours(tl)["Sekutu"]
    dom = {2: {"times": [{"paints": [{"entity": "Sekutu", "kind": "text", "color": "rgb(47, 33, 21)"},
                                     {"entity": "Sekutu", "kind": "fill", "color": "rgb(%d, %d, %d)" %
                                      tuple(int(want[k:k + 2], 16) for k in (1, 3, 5))}]}]}}
    assert [f.level for f in layer2.e01_entity_colours(tl, dom)] == ["pass"]


def test_bad_E01_region_fill_off_entity_colour_fails(project):
    _, tl = project(_scene(title="SEKUTU", title_entity="Sekutu"))
    dom = {2: {"times": [{"paints": [{"entity": "Sekutu", "kind": "fill", "color": "rgb(136, 159, 184)"}]}]}}
    msgs = [f.message for f in layer2.e01_entity_colours(tl, dom) if f.level == "fail"]
    assert len(msgs) == 1 and "fill of entity 'Sekutu'" in msgs[0]


# GOOD / BAD: theme paint (all profiles) -------------------------------------------------------------------------------
def test_good_default_theme_paints_no_outline(project, tmp_path):
    p, tl = project(_scene(title="Surabaya"))
    assert profiles.load().theme.text_outline_px == 0
    _, (it,), _ = _probe(tl, p, tmp_path)
    assert it["title"]["stroke_px"] == 0 and it["pin:0"]["stroke_px"] == 0


def test_good_outline_and_shadow_painted_from_theme(project, tmp_path, monkeypatch):
    _profile(monkeypatch, **LEGIBLE)
    p, tl = project(_scene(title="Surabaya"))
    _, (it,), _ = _probe(tl, p, tmp_path)
    for k in ("title", "pin:0"):
        r = it[k]
        assert r["stroke_px"] == 5 and r["paint_order"].startswith("stroke")
        assert r["stroke"] == "rgba(26, 20, 16, 0.75)"
    assert layer2._stroke(it["title"]) == ([26, 20, 16], 0.75)


def test_good_title_tint_mixes_toward_white():
    assert hyperframes.tint(BLUE, 0) == BLUE and hyperframes.tint(BLUE, 1) == "#ffffff"
    assert hyperframes.tint(BLUE, 0.3) == "#88a2bb"


def test_good_soft_glow_keeps_fill_at_its_opacity(project, tmp_path, monkeypatch):
    """With a soft glow the region interior shows exactly region_fill_opacity of the entity colour."""
    _profile(monkeypatch, entities={"Sekutu": BLUE}, region_edge="glow", region_fill_opacity=0.34,
             region_glow_alpha=0.45, region_glow_px=10)
    p, tl = project(_scene(pins=[], camera_preset="static", regions=[{"entity": "Sekutu", "polygon": SQ}]))
    p0, tl0 = project(_scene(pins=[], camera_preset="static", regions=[{"entity": "Sekutu", "polygon": SQ, "at_frame": 59}]))
    reg = np.asarray(Image.open(_probe(tl, p, tmp_path / "r")[0]["times"][0]["png"]).convert("RGB"), dtype=float)
    plain = np.asarray(Image.open(_probe(tl0, p0, tmp_path / "p")[0]["times"][0]["png"]).convert("RGB"), dtype=float)
    col = np.array([0x55, 0x7a, 0x9e], dtype=float)
    d = col - plain
    m = (np.abs(d) > 40) & (np.abs(reg - plain) > 4)
    alpha = np.median(((reg - plain)[m]) / d[m])
    assert 0.30 <= alpha <= 0.38


@pytest.mark.parametrize("key, val, msg", [
    ("text_outline_colour", "brown", "#rrggbb"),
    ("title_tint", 0.8, "less than or equal to 0.6"),
    ("text_outline_px", 9, "less than or equal to 6"),
    ("region_glow_alpha", 1.5, "less than or equal to 1"),
])
def test_bad_theme_schema(key, val, msg):
    raw = profiles.load().data.model_dump()
    raw["theme"][key] = val
    with pytest.raises(Exception) as e:
        profiles.ProfileData.model_validate(raw)
    assert msg in str(e.value)


# GOOD / BAD: [legibility] schema --------------------------------------------------------------------------------------
def test_good_geoverum_legibility_keys_within_brief_ranges():
    g = GEO
    assert 0.22 <= g["ink_l"] <= 0.30 and g["ink_c"] <= 0.04 and 50 <= g["ink_hue"] <= 70 and g["ink_l_min"] == 0.18
    assert 0.06 <= g["label_halo_width_em"] <= 0.10 and 0.06 <= g["halo_width_em"] <= 0.10
    assert g["halo_alpha"] == g["halo_alpha_max"] == 0 and g["ladder_order"] == "ink_first"   # no glyph halo on paper
    assert g["vignette_scale"] == 1.4 and g["vignette_alpha_max"] <= 0.22 and g["paper_colour"].lower() == "#f1e8d4"
    assert g["glow_colour"] == "entity" and g["glow_alpha"] <= 0.25 and not g["polarity_fallback"]
    assert (g["entity_text_l_min"], g["entity_text_l_max"], g["entity_text_c_scale"]) == (0.32, 0.36, 0.8)
    assert g["region_casing_px"] == 0 and g["leader_casing_px"] == 0 and 2 <= g["entity_outline_px"] <= 2.5
    assert (g["lc_title"], g["lc_label"], g["lc_floor"], g["lc_fail_margin"]) == (60, 75, 15, 5)
    assert (g["hold_frames"], g["crossfade_ms"], g["ema_tau_ms"]) == (8, 150, 250)


@pytest.mark.parametrize("key, val", [("ink_l", 0.5), ("halo_alpha_max", 0.95), ("vignette_scale", 2.0),
                                      ("halo_l_min", 0.8), ("text_fallback", "brown"), ("glow_px", 8),
                                      ("entity_fill_alpha", 0.4)],
                         ids=["BAD-ink-too-light", "BAD-halo-too-opaque", "BAD-vignette-too-big",
                              "BAD-halo-too-dark", "BAD-colour-not-hex", "BAD-glow-too-wide", "BAD-fill-too-strong"])
def test_bad_legibility_schema(key, val):
    with pytest.raises(Exception):
        profiles.Legibility.model_validate({**LG, key: val})


def test_bad_legibility_inverted_pair():
    with pytest.raises(Exception, match="halo_alpha"):
        profiles.Legibility.model_validate({**LG, "halo_alpha": 0.8, "halo_alpha_max": 0.7})


# GOOD / BAD: printed ink (Geoverum, ladder_order "ink_first") --------------------------------------------------------
PIN = {**LABEL, "ink": "#2A231D", "polarity": "dark"}


def test_good_ink_first_ladder_darkens_ink_before_the_knock_back():
    lad = L.ladder("dark", PIN, GEO, GEO["paper_colour"])
    steps = [st.step for st in lad]
    assert steps == sorted(steps) and steps[0] == 0 and 1 in steps and steps[-1] == 2
    assert all(st.halo_alpha == 0 for st in lad) and all(st.vignette_alpha == 0 for st in lad if st.step < 2)
    ls = [L.rgb_oklch(L.hex_rgb(st.ink))[0] for st in lad if st.step < 2]
    assert lad[0].ink == "#2A231D" and all(b < a for a, b in zip(ls, ls[1:])) and min(ls) >= GEO["ink_l_min"] - 0.005
    vig = [st for st in lad if st.step == 2]
    assert vig[0].vignette_alpha == GEO["vignette_alpha_min"] and vig[-1].vignette_alpha == GEO["vignette_alpha_max"]
    assert all(st.ink == vig[0].ink for st in vig) and all(st.halo == GEO["paper_colour"] for st in lad)


def test_good_printed_ink_on_paper_has_no_halo_and_passes():
    st = L.choose(PIN, [_sample(PAPER)], GEO, 30).segments[0][1]
    assert (st.polarity, st.ink, st.halo_alpha, st.vignette_alpha, st.step) == ("dark", "#2A231D", 0, 0, 0)


def test_bad_printed_ink_on_a_dark_patch_never_turns_white():
    """polarity_fallback false: a paper element over a dark patch escalates the ink and the knock-back, and is flagged
    when nothing passes, instead of switching to light text."""
    c = L.choose(PIN, [_sample(DARK_MAP)], GEO, 30)
    assert all(st.polarity == "dark" for _, st in c.segments) and c.flagged
    on = L.choose(PIN, [_sample(DARK_MAP)], {**GEO, "polarity_fallback": True}, 30)
    assert on.segments[0][1].polarity == "light" and not on.flagged


def test_good_entity_text_ink_clamped_to_the_print_band():
    for col in ("#4B4D8B", "#B56A95", "#136868", "#d8c8f0"):
        Lh, Ch, _ = L.rgb_oklch(L.hex_rgb(L.dark_ink({**TITLE, "colour": col}, GEO)))
        assert GEO["entity_text_l_min"] - 0.006 <= Lh <= GEO["entity_text_l_max"] + 0.006
        assert Ch <= L.rgb_oklch(L.hex_rgb(col))[1] * GEO["entity_text_c_scale"] + 0.01


def test_bad_light_text_takes_the_fixed_light_override():
    assert L.light_ink(GEO, {"light": "#F7F2E6"}) == "#F7F2E6" and L.light_ink(GEO) != "#F7F2E6"
