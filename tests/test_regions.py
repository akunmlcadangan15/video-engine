"""Region highlights: entity fill + white or glow edge, one colour per entity (profile map + palette), E01 gate."""
import pytest
from pydantic import ValidationError

from video_engine import entities, hyperframes, profiles
from video_engine.gates import layer1, layer2, style
from video_engine.util import EngineError

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]


def _regions(regs, seg=2):
    def mut(d):
        d["segments"][seg]["gsap_scene_config"]["regions"] = regs
    return mut


def _theme(monkeypatch, **kw):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=kw)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))


def _probe(tl, p, tmp_path, times=(0.0, 1.0)):
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    return hyperframes.probe_dom(d, list(times), 1080, 1920), sc


# good ---------------------------------------------------------------------------------------------------------------
def test_good_palette_by_first_appearance_profile_map_wins(project, monkeypatch):
    _theme(monkeypatch, entities={"Belanda": "#FF8A3D"}, palette=["#ff8a3d", "#111111", "#222222"])
    _, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ}, {"entity": "Belanda", "polygon": SQ},
                              {"entity": "Indonesia", "polygon": SQ}, {"entity": "Sekutu", "polygon": SQ}]))
    # the palette colour taken by the profile map is skipped for the other entities
    assert entities.colours(tl) == {"Sekutu": "#111111", "Belanda": "#ff8a3d", "Indonesia": "#222222"}


def test_good_scene_regions_in_map_pixels_with_theme_edge(project):
    p, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ, "at_frame": 6},
                              {"entity": "Indonesia", "polygon": SQ, "edge": "glow"}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    mw, mh = sc["map_size"]
    r0, r1 = sc["regions"]
    assert r0["points"][2] == [round(0.7 * mw, 2), round(0.7 * mh, 2)] and r0["at_frame"] == 6
    assert (r0["edge"], r1["edge"]) == ("white", "glow")          # default theme edge, region override
    assert r0["colour"] != r1["colour"]


def test_good_no_regions_scene_unchanged(project):
    p, tl = project()
    assert "regions" not in hyperframes.scene_data(tl.segments[2], tl, p)


def test_good_beats_region_uses_the_profile_region_style(project):
    """A beats scene no longer forces tint: the Geoverum block style reaches its scene data."""
    from test_layout import beats_doc

    def mut(d):
        beats_doc(d)
        d["segments"][2]["gsap_scene_config"]["beats"] = [{"at_frame": 0, "region": 0, "pins": [0]}]

    p, tl = project(mut)
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert sc["regions"][0]["style"] == "block" and sc["regions"][0]["block"]
    d = tl.segments[2].gsap_scene_config
    d.regions[0].style = "tint"
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert sc["regions"][0]["style"] == "tint" and "block" not in sc["regions"][0]


def test_good_render_values_exclude_entity_map():
    th = profiles.load().render_values("hf")
    assert "entities" not in th and "palette" not in th and th["region_edge"] == "white"
    assert profiles.load("geoverum").theme.region_edge == "glow"


def test_good_region_fades_in_and_is_painted_in_entity_colour(project, tmp_path):
    p, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ, "at_frame": 15}]))
    res, sc = _probe(tl, p, tmp_path, (0.0, 1.5))
    col = sc["regions"][0]["colour"]
    assert all(layer2._rgb_hex(i["paints"][0]["color"]) == col for i in res["times"])
    f = layer2.e01_entity_colours(tl, {2: res})
    assert [x.level for x in f] == ["pass"] and "1 region fills" in f[0].message


def test_good_region_counts_as_graphic_event_without_text(project):
    _, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ, "at_frame": 9}]))
    s = tl.segments[2]
    assert (s.start_frame + 9, "") in style.element_events(s, 30)


def test_good_no_entities_no_E01_finding(project):
    _, tl = project()
    assert layer2.e01_entity_colours(tl, {2: {"times": [{"t": 0, "rects": [], "paints": []}]}}) == []


def test_good_region_pixels_show_fill_and_edge(project, tmp_path):
    """A rendered region tints a real share of the frame against the same scene without it."""
    import numpy as np
    from PIL import Image
    p, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ}]))
    p0, tl0 = project()
    shots = []
    for name, (pp, t) in {"reg": (p, tl), "plain": (p0, tl0)}.items():
        d, sc = hyperframes.build_dir(t.segments[2], t, pp, tmp_path / name)
        shots.append(np.asarray(Image.open(hyperframes.probe_dom(d, [1.0], 1080, 1920, tmp_path / f"s_{name}")["times"][0]["png"]), dtype=np.int16))
    diff = np.abs(shots[0] - shots[1]).max(axis=2)
    assert (diff > 20).mean() > 0.05          # the fill tints a real share of the frame


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reg, msg", [
    ({"entity": "A", "polygon": SQ[:2]}, "at least 3"),
    ({"entity": "A", "polygon": [[0.1, 0.1], [1.2, 0.1], [0.5, 0.5]]}, "within 0..1"),
    ({"entity": "", "polygon": SQ}, "at least 1 character"),
    ({"entity": "A", "polygon": SQ, "edge": "neon"}, "'white' or 'glow'"),
], ids=["BAD-two-points", "BAD-point-off-map", "BAD-empty-entity", "BAD-edge"])
def test_bad_region_schema(project, reg, msg):
    with pytest.raises(EngineError, match=msg):
        project(_regions([reg]))


def test_bad_region_after_segment_fails_T02(project):
    p, tl = project(_regions([{"entity": "A", "polygon": SQ, "at_frame": 60}]))
    assert any(f.level == "fail" and "region 'A'" in f.message for f in layer1.t02_integrity(tl, p))


def test_bad_painted_colour_differs_from_entity_colour_fails_E01(project):
    _, tl = project(_regions([{"entity": "Sekutu", "polygon": SQ}]))
    res = {"times": [{"t": 0, "rects": [], "paints": [{"entity": "Sekutu", "kind": "fill", "color": "rgb(1, 2, 3)"}]}]}
    f = layer2.e01_entity_colours(tl, {2: res})
    assert [x.level for x in f] == ["fail"] and "#010203" in f[0].message


def test_bad_entities_sharing_a_colour_warn_E01(project, monkeypatch):
    _theme(monkeypatch, palette=["#ff0000"])
    _, tl = project(_regions([{"entity": "A", "polygon": SQ}, {"entity": "B", "polygon": SQ}]))
    assert any(f.level == "warn" and "share colour" in f.message for f in layer2.e01_entity_colours(tl, {}))


def test_bad_theme_colour_not_hex():
    with pytest.raises(ValidationError, match="#rrggbb"):
        profiles.Theme(**{**profiles.load().theme.model_dump(), "palette": ["red"]})
