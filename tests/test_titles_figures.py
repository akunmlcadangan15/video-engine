"""Big titles (entity colour + glow, E01), years (bold serif) and numbers (elastic bounce or counter) and the counter
tick sound events."""
import pytest
from pydantic import ValidationError

from video_engine import entities, hyperframes, profiles
from video_engine.gates import layer1, layer2, style
from video_engine.timeline import Figure

TH = {"number_group": ".", "number_decimal": ","}


def _scene(**kw):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(pins=[], cards=[], **kw)
    return mut


def _theme(monkeypatch, **kw):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=kw)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))


def _probe(tl, p, out, times):
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, out / "hf")
    res = hyperframes.probe_dom(d, times, 1080, 1920, None)
    return d, res, [{r["id"]: r for r in it["rects"]} for it in res["times"]]


# good ---------------------------------------------------------------------------------------------------------------
def test_good_title_painted_in_entity_colour(project, tmp_path):
    p, tl = project(_scene(title="Sekutu Mendarat", title_entity="Sekutu", title_at_frame=0))
    _, res, (it,) = _probe(tl, p, tmp_path, [1.0])
    want = entities.colours(tl)["Sekutu"]
    assert [x for x in res["times"][0]["paints"] if x["kind"] == "text"] == \
        [{"entity": "Sekutu", "kind": "text", "color": res["times"][0]["paints"][0]["color"]}]
    assert layer2._rgb_hex(res["times"][0]["paints"][0]["color"]) == want
    assert layer2.e01_entity_colours(tl, {2: res}) == []                      # E01 checks region fills only
    assert not layer2.check_rects(list(it.values()), 1080, 1920, 1.0, 2)      # inside the safe area
    assert it["title"]["text"] == "Sekutu Mendarat"


def test_good_title_font_follows_theme_and_ships_only_when_used(project, tmp_path, monkeypatch):
    p, tl = project(_scene(title="Surabaya"))
    d, _, (inter,) = _probe(tl, p, tmp_path / "i", [1.0])
    assert sorted(x.name for x in (d / "assets").iterdir() if x.suffix in (".otf", ".ttf")) == sorted(hyperframes.HF_FONTS)
    _theme(monkeypatch, title_font="title")
    d, _, (heavy,) = _probe(tl, p, tmp_path / "t", [1.0])
    assert (d / "assets" / "ArchivoBlack-Regular.ttf").is_file()
    assert abs(heavy["title"]["w"] - inter["title"]["w"]) > 10


def test_good_year_bold_serif_with_elastic_bounce(project, tmp_path):
    p, tl = project(_scene(figures=[{"kind": "year", "value": 1945, "at_frame": 15, "position": [0.5, 0.4]}]))
    d, _, (early, peak, landed) = _probe(tl, p, tmp_path, [0.5 + 1 / 30, 0.5 + 0.18, 0.5 + 1.0])
    assert (d / "assets" / "PlayfairDisplay[wght].ttf").is_file() and "EngSerif" in (d / "index.html").read_text()
    assert landed["figure:0"]["text"] == "1945"
    h = landed["figure:0"]["h"]
    assert early["figure:0"]["h"] < 0.5 * h < peak["figure:0"]["h"] / 2.2        # grows from 0, overshoots (elastic)
    c = landed["figure:0"]
    assert (c["x"] + c["w"] / 2, c["y"] + c["h"] / 2) == pytest.approx((540, 0.4 * 1920), abs=2)


@pytest.mark.parametrize("ease", [None, "expo.out", "sine.inOut"])
def test_good_counter_matches_python_rule_and_lands_on_value(project, tmp_path, ease):
    """The DOM counter text equals the Python twin for the profile's [motion] entrance ease (the template tweens the
    counter with MO.entrance; the twin was fixed to power2.out before)."""
    fig = {"kind": "number", "value": 20000, "motion": "counter", "count_s": 1.0, "at_frame": 6, "suffix": " jiwa"}
    over = (lambda d: d.update(profile_overrides={"motion": {"entrance": ease}})) if ease else (lambda d: None)
    p, tl = project(lambda d: (_scene(figures=[fig])(d), over(d)))
    F = tl.segments[2].gsap_scene_config.figures[0]
    frames = [6, 9, 15, 21, 36, 45]
    _, _, its = _probe(tl, p, tmp_path, [f / 30 for f in frames])
    th = profiles.of(tl).render_values("hf")
    assert th["motion"]["entrance"] == (ease or "power2.out")
    want = [hyperframes.figure_text(F, hyperframes.counter_value(F, f, 30, 1.0, th["motion"]["entrance"]), th)
            for f in frames]
    assert [it["figure:0"]["text"] for it in its] == want
    assert want[0] == "0 jiwa" and want[-1] == "20.000 jiwa" and want[2] not in want[1::4]


def test_good_counter_ticks_emitted_as_sfx_events(project):
    p, tl = project(_scene(figures=[{"kind": "number", "value": 12, "motion": "counter", "count_s": 1.0, "at_frame": 3},
                                    {"kind": "year", "value": 1945, "at_frame": 0}]))
    ev = hyperframes.sfx_events(tl)
    s = tl.segments[2]
    assert ev and all(e["kind"] == "counter_tick" and e["figure"] == 0 and e["seg"] == 2 for e in ev)
    assert len(ev) == 12 and [e["frame"] for e in ev] == sorted({e["frame"] for e in ev})   # one per shown value
    assert s.start_frame + 3 < ev[0]["frame"] and ev[-1]["frame"] <= s.start_frame + 33


def test_good_figure_text_rules():
    n = Figure(kind="number", value=1234567.891, decimals=2, prefix="± ", suffix=" km")
    assert hyperframes.figure_text(n, n.value, TH) == "± 1.234.567,89 km"
    assert hyperframes.figure_text(n, -0.004, TH) == "± 0,00 km"
    assert hyperframes.figure_text(Figure(kind="year", value=1945), 1945, TH) == "1945"


def test_good_title_and_figures_feed_style_events(project):
    p, tl = project(_scene(title="Sekutu", figures=[{"kind": "number", "value": 6000, "at_frame": 9}]))
    s = tl.segments[2]
    ev = style.element_events(s, tl.settings.fps)
    assert (s.start_frame, "Sekutu") in ev and (s.start_frame + 9, "6000") in ev


# bad ----------------------------------------------------------------------------------------------------------------
def test_good_title_text_off_entity_colour_is_not_an_E01_finding(project):
    """E01 is relaxed to region fills: title text colour belongs to R64 (adaptive ink)."""
    p, tl = project(_scene(title="Sekutu", title_entity="Sekutu"))
    dom = {2: {"times": [{"paints": [{"entity": "Sekutu", "kind": "text", "color": "rgb(255, 255, 255)"}]}]}}
    assert layer2.e01_entity_colours(tl, dom) == []


def test_bad_figure_entrance_past_segment_end_fails_T02(project):
    n = project()[1].segments[2].n_frames
    p, tl = project(_scene(figures=[{"kind": "number", "value": 5, "motion": "counter", "at_frame": n - 5}]))
    assert any(x.gate == "T02" and x.level == "fail" and "entrance ends" in x.message for x in layer1.run_layer1(tl, p, None))


def test_bad_title_entity_without_title_fails_T02(project):
    p, tl = project(_scene(title_entity="Sekutu"))
    assert any(x.gate == "T02" and "without a title" in x.message for x in layer1.run_layer1(tl, p, None))


@pytest.mark.parametrize("fig, msg", [
    ({"kind": "year", "value": 1945.5}, "whole number"),
    ({"kind": "number", "value": 3, "count_from": 1}, "motion 'counter' only"),
    ({"kind": "number", "value": 3, "position": [1.2, 0.5]}, "within 0..1"),
    ({"kind": "number", "value": 3, "motion": "spin"}, "'bounce' or 'counter'"),
])
def test_bad_figure_schema(fig, msg):
    with pytest.raises(ValidationError, match=msg):
        Figure(**fig)


@pytest.mark.parametrize("name, p, want", [("power2.out", 0.5, 0.875), ("power2", 0.5, 0.875), ("power2.in", 0.5, 0.125),
                                           ("power2.inOut", 0.25, 0.0625), ("none", 0.3, 0.3),
                                           ("sine.out", 1 / 3, 0.5), ("expo.in", 0.0, 0.0), ("expo.out", 1.0, 1.0),
                                           ("back.out(1.7)", 1.0, 1.0), ("circ.in", 1.0, 1.0)])
def test_good_gsap_ease_twin_values(name, p, want):
    assert hyperframes.gsap_ease(name)(p) == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("name", ["elastic.out(1, 0.3)", "power2.sideways"])
def test_bad_gsap_ease_twin_names_what_it_cannot_mirror(name):
    from video_engine.util import EngineError
    with pytest.raises(EngineError, match="ease"):
        hyperframes.gsap_ease(name)
