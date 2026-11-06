"""Anti-AI-slop gates AS01-AS06 and the [motion] tokens. Labelled GOOD and BAD cases, no renders and no network."""
from pathlib import Path

import pytest
from conftest import words
from pydantic import ValidationError

from video_engine import gate_registry as reg, profiles
from video_engine.gates import anti_slop

FPS = 30


def gate(name="default"):
    return profiles.load(name).gates.anti_slop


def overlay(d, ov, seg=0):
    d["segments"][seg]["overlays"] = [ov]


def html(project_dir: Path, text: str) -> Path:
    p = Path(project_dir) / "compositor" / "index.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def built_html(project_dir: Path, text: str) -> Path:
    p = Path(project_dir) / "built" / "seg000" / "index.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p.parent


# AS01 decorative panels ----------------------------------------------------------------------------------------------
def test_good_AS01_plain_label(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 800, 150],
                                              "text": "Surabaya"}))
    assert anti_slop.as01_panel(tl, gate()) == []


def test_bad_AS01_lower_third_side_strip_warns(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "lt", "kind": "lower_third", "box": [100, 1400, 800, 170],
                                              "text": "Surabaya", "subtitle": "1945"}))
    fs = anti_slop.as01_panel(tl, gate())
    assert [f.level for f in fs] == ["warn"] and "side accent strip" in fs[0].message and fs[0].seg == 0


def test_bad_AS01_text_card_side_strip_warns(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "card", "kind": "text_card", "box": [90, 300, 900, 300],
                                              "text": "Surabaya, 10 November 1945"}))
    assert [f.level for f in anti_slop.as01_panel(tl, gate())] == ["warn"]


def test_bad_AS01_geoverum_side_strip_warns(project):
    _d, tl = project(lambda doc: (doc.update(profile="geoverum"),
                                  overlay(doc, {"id": "lt", "kind": "lower_third", "box": [100, 1400, 800, 170],
                                                "text": "Surabaya", "subtitle": "1945"})))
    fs = anti_slop.as01_panel(tl, gate("geoverum"))
    assert [f.level for f in fs] == ["warn"] and "side accent strip" in fs[0].message


def test_good_AS01_is_not_required_on_geoverum(project):
    """On Geoverum, an AS01 side strip remains an advisory warning through severity."""
    from video_engine.gates import severity
    _d, tl = project(lambda doc: (doc.update(profile="geoverum"),
                                  overlay(doc, {"id": "lt", "kind": "lower_third", "box": [100, 1400, 800, 170],
                                                "text": "Surabaya", "subtitle": "1945"})))
    fs = anti_slop.as01_panel(tl, gate("geoverum"))
    assert "AS01" not in severity.required(tl)
    assert [f.level for f in severity.apply(fs, tl)] == ["warn"]


# AS02 copy -----------------------------------------------------------------------------------------------------------
def test_good_AS02_plain_copy(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 800, 150],
                                              "text": "Surabaya, 10 November 1945"}))
    assert anti_slop.as02_copy(tl, words(("Pertempuran", 0, 10)), gate()) == []


def test_bad_AS02_buzzword_and_theater(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 900, 150],
                                              "text": "Streamline the cutting-edge workflow, theater"}))
    msgs = [f.message for f in anti_slop.as02_copy(tl, None, gate())]
    assert any("buzzword" in m for m in msgs) and any("theater" in m for m in msgs)


def test_bad_AS02_em_dash_saturation(project):
    text = "Satu \u2014 dua \u2014 tiga \u2014 empat \u2014 lima \u2014 enam \u2014 tujuh \u2014 delapan"
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 900, 150],
                                              "text": text}))
    fs = anti_slop.as02_copy(tl, None, gate())
    assert any("em dash" in f.message for f in fs)


def test_bad_AS02_aphoristic_cadence(project):
    text = "Bukan jalan. No. Bukan sungai. No. Bukan batas. No."
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 900, 150],
                                              "text": text}))
    assert any("aphoristic" in f.message for f in anti_slop.as02_copy(tl, None, gate()))


# AS03 motion ---------------------------------------------------------------------------------------------------------
def test_good_AS03_default_motion_tokens(project):
    _, tl = project()
    assert anti_slop.as03_motion(tl, gate()) == []


def test_bad_AS03_ease_in_entrance_and_scale_near_zero(project, monkeypatch):
    _, tl = project()
    pr = profiles.load()
    m = profiles.MotionProfile(entrance="power2.in", entrance_scale=0.4)
    data = pr.data.model_copy(update={"motion": m})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("badmotion", data, "x"))
    msgs = [f.message for f in anti_slop.as03_motion(tl, gate())]
    assert any("ease-in" in m for m in msgs) and any("near zero" in m for m in msgs)


def test_bad_AS03_bounce_figure(project):
    def bounce(doc):
        doc["segments"][2]["gsap_scene_config"]["figures"] = [{"kind": "year", "value": 1945, "motion": "bounce"}]
    _, tl = project(bounce)
    assert any("bounce" in f.message for f in anti_slop.as03_motion(tl, gate()))


# AS04 readable on-screen time ----------------------------------------------------------------------------------------
def test_good_AS04_text_holds_long_enough(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 800, 150],
                                              "text": "Surabaya", "start_frame": 0, "end_frame": 60}))
    assert anti_slop.as04_read_time(tl, gate()) == []


def test_bad_AS04_text_flashes_too_short(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 800, 150],
                                              "text": "Pertempuran besar di Surabaya", "start_frame": 0,
                                              "end_frame": 15}))
    fs = anti_slop.as04_read_time(tl, gate())
    assert [f.level for f in fs] == ["warn"] and "reads" in fs[0].message


def test_good_AS04_profile_can_raise_read_time_to_fail(project):
    _d, tl = project(lambda doc: overlay(doc, {"id": "name", "kind": "label", "box": [100, 1400, 800, 150],
                                              "text": "Pertempuran besar di Surabaya", "start_frame": 0,
                                              "end_frame": 15}))
    g = profiles.AntiSlopGate(levels={"AS04": "fail"})
    assert [f.level for f in anti_slop.as04_read_time(tl, g)] == ["fail"]


# AS05 composition source tells ---------------------------------------------------------------------------------------
def test_good_AS05_plain_composition(project):
    d, _tl = project()
    html(d, "<style>.label{color:#fff;border-left:1px solid #fff}</style><p>Halo</p>")
    assert anti_slop.as05_source_tells(d, gate()) == []


def test_bad_AS05_source_tells(project):
    d, _tl = project()
    html(d, """<style>
    .title{background-clip:text;-webkit-text-fill-color:transparent}
    .panel{backdrop-filter:blur(20px);border-left:6px solid #ff0}
    .grid{background:repeating-linear-gradient(90deg,#000,#000 1px,#fff 1px,#fff 20px)}
    .glow{box-shadow:0 0 24px #f0f}
    .blob{clip-path:polygon(0 0,10% 0,20% 10%,30% 0,40% 20%,50% 0,60% 10%,70% 0,80% 20%,90% 0,100% 0,100% 100%,0 100%)}
    .kicker{font-size:12px}
    </style>""")
    tells = " ".join(f.message for f in anti_slop.as05_source_tells(d, gate()))
    for what in ("gradient text", "glass panel", "side accent strip", "stripe", "glow", "clip-path", "kicker"):
        assert what in tells


def test_good_AS05_one_px_border_is_not_a_side_strip(project):
    d, _tl = project()
    html(d, "<style>.row{border-left:1px solid #fff}</style>")
    assert anti_slop.as05_source_tells(d, gate()) == []


def test_good_AS05_built_engine_effects_are_allowlisted(project, tmp_path):
    from video_engine import hyperframes
    d, tl = project()
    out, _scene = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "built" / "seg002")
    text = (out / "index.html").read_text()
    assert "box-shadow:0 0 14px" in text          # the template's built-in pin glow is a source tell
    assert anti_slop.as05_built_html([out], gate()) == []
    assert anti_slop.as06_built_html([out], tl, gate()) == []


def test_bad_AS05_built_html_flags_project_injected_tell(project, tmp_path):
    from video_engine import hyperframes
    d, tl = project()
    out, _scene = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "built" / "seg002")
    (out / "index.html").write_text((out / "index.html").read_text()
                                    + "\n<style>.hero{background-clip:text;-webkit-text-fill-color:transparent}</style>\n")
    fs = anti_slop.as05_built_html([out], gate())
    assert [f.level for f in fs] == ["warn"] and "gradient text" in fs[0].message
    assert "seg002/index.html" in fs[0].message


def test_bad_AS05_built_html_flags_project_injected_colour(project, tmp_path):
    from video_engine import hyperframes
    d, tl = project()
    out, _scene = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "built" / "seg002")
    (out / "index.html").write_text((out / "index.html").read_text()
                                    + "\n<style>.hero{color:#7c3aed}</style>\n")
    fs = anti_slop.as06_built_html([out], tl, gate())
    assert [f.level for f in fs] == ["warn"] and "AI-tell" in fs[0].message


# AS06 palette tell hues ----------------------------------------------------------------------------------------------
def test_good_AS06_brand_palette_colour(project):
    d, tl = project()
    html(d, "<style>.a{color:#ff4d4d}</style>")          # default palette red
    assert anti_slop.as06_palette(d, tl, gate()) == []


def test_good_AS06_geoverum_teal_is_declared(project):
    d, tl = project(lambda doc: doc.update(profile="geoverum"))
    html(d, "<style>.a{color:#136868}</style>")          # Geoverum palette teal
    assert anti_slop.as06_palette(d, tl, gate("geoverum")) == []


def test_bad_AS06_violet_literal(project):
    d, tl = project()
    html(d, "<style>.a{color:#7c3aed}</style>")
    fs = anti_slop.as06_palette(d, tl, gate())
    assert [f.level for f in fs] == ["warn"] and "AI-tell" in fs[0].message


def test_bad_AS06_cyan_on_dark_literal(project):
    d, tl = project()
    html(d, "<style>.a{color:#00d5ff}</style>")
    assert [f.level for f in anti_slop.as06_palette(d, tl, gate())] == ["warn"]


# motion tokens -------------------------------------------------------------------------------------------------------
def test_good_motion_tokens_are_frames_at_30_fps():
    m = profiles.MotionProfile()
    s = m.seconds()
    assert s["micro"] == pytest.approx(4 / 30)
    assert s["overlay"] == pytest.approx(7 / 30)
    assert s["panel"] == pytest.approx(12 / 30)
    assert s["stagger"] == pytest.approx(2 / 30)
    rv = m.render_values()
    assert rv["entrance"] == "power2.out" and rv["entrance_scale"] == pytest.approx(0.96)


@pytest.mark.parametrize("kw", [{"micro_frames": 20}, {"overlay_frames": 30},
                                {"entrance": " "}, {"ease_out": (1.5, 0, 0.3, 1)}])
def test_bad_motion_token_values_rejected(kw):
    with pytest.raises(ValidationError):
        profiles.MotionProfile(**kw)


def test_bad_anti_slop_unknown_rule_level_and_hue_range():
    with pytest.raises(ValidationError, match="unknown rule"):
        profiles.AntiSlopGate(levels={"AS99": "fail"})
    with pytest.raises(ValidationError, match="forbidden_hues"):
        profiles.AntiSlopGate(forbidden_hues=[(300, 200)])


def test_good_anti_slop_defaults_are_warn():
    g = profiles.AntiSlopGate()
    assert g.level == "warn" and all(g.level_for(i) == "warn" for i in reg.ANTI_SLOP_IDS)
