"""Stage 4 visuals (P12-P19: backdrops, halo, graticule, timezones, hatch, bubbles, flag characters, cutaway, geoid,
particles) and P20 transitions (page_turn, map_morph) remain opt-in per scene and off by default across built-in profiles."""
import re

from video_engine import hyperframes, profiles, timeline
from video_engine.timeline import GsapScene

STAGE4 = ("backdrop", "timezones", "bubbles", "flag_characters", "cutaway", "geoid", "particles")


def _kinds(t) -> set[str]:
    return {t.kind, *t.by_type.values(), *t.same_type.values()}


def test_good_scene_config_defaults_leave_every_stage4_visual_off():
    fields = GsapScene.model_fields
    assert all(fields[k].default is None for k in STAGE4)
    sc = timeline.GsapScene(map_asset="m.jpg")
    assert sc.flag_characters is None and sc.cutaway is None and sc.insets is None
    assert sc.geoid is None and sc.particles is None


def test_good_default_and_geoverum_scenes_draw_no_stage4_visual(project):
    for name in ("default", "geoverum"):
        def mut(d, name=name):
            d["profile"] = name
        path, tl = project(mut)
        assert tl.profile == name
        sc = hyperframes.scene_data(tl.segments[2], tl, path)
        drawn = [k for k in (*STAGE4, "halo", "graticule", "grid", "stars", "hatch", "insets") if sc.get(k)]
        assert drawn == [], (name, drawn)
        assert "geoid" not in sc.get("globe", {})
        assert profiles.of(tl).data.fx is not None          # style values exist, but only as parameters


def test_good_default_and_geoverum_profiles_keep_page_turn_and_morph_off():
    for name in ("default", "geoverum"):
        t = profiles.load(name).transitions
        assert "page_turn" not in _kinds(t), name
    t_geo = profiles.load("geoverum").transitions
    assert t_geo.page_turn is False and t_geo.map_morph is False
    text = profiles.flatten("geoverum")
    assert re.search(r"^page_turn\s*=\s*false\b", text, re.MULTILINE)
    assert re.search(r"^map_morph\s*=\s*false\b", text, re.MULTILINE)
