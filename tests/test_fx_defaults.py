"""P20: page curl and map morph stay off in every built-in profile; Stage 4b components stay off unless a scene sets them."""
import re

from video_engine import config, hyperframes, profiles, timeline


def test_default_and_geoverum_profiles_keep_page_turn_and_morph_off():
    for name in ("default", "geoverum"):
        t = profiles.load(name).transitions
        assert "page_turn" not in {t.kind, *t.by_type.values(), *t.same_type.values()}, name
    text = profiles.flatten("geoverum")
    assert re.search(r"^page_turn\s*=\s*false\b", text, re.MULTILINE)
    assert re.search(r"^map_morph\s*=\s*false\b", text, re.MULTILINE)


def test_new_fx_fields_are_absent_from_a_plain_scene(project):
    d, tl = project()
    scene = hyperframes.scene_data(tl.segments[2], tl, d)
    for key in ("flag_characters", "cutaway", "insets", "particles"):
        assert key not in scene
    assert "geoid" not in scene.get("globe", {})
    sc = timeline.GsapScene(map_asset="m.jpg")
    assert sc.flag_characters is None and sc.cutaway is None and sc.insets is None
    assert sc.geoid is None and sc.particles is None
