"""Stage 4 visuals (P12-P19: backdrops, halo, graticule, timezones, hatch, bubbles, flag characters, cutaway, geoid,
particles) are opt-in per scene: approved 2026-11-01 as optional, off by default, never forced by a profile."""
from video_engine import hyperframes, profiles
from video_engine.timeline import GsapScene

STAGE4 = ("backdrop", "timezones", "bubbles", "flag_characters", "cutaway", "geoid", "particles")


def test_good_scene_config_defaults_leave_every_stage4_visual_off():
    fields = GsapScene.model_fields
    assert all(fields[k].default is None for k in STAGE4)


def test_good_default_and_geoverum_scenes_draw_no_stage4_visual(project):
    for name in ("default", "geoverum"):
        def mut(d, name=name):
            d["profile"] = name
        path, tl = project(mut)
        assert tl.profile == name
        sc = hyperframes.scene_data(tl.segments[2], tl, path)
        drawn = [k for k in (*STAGE4, "halo", "graticule", "grid", "stars", "hatch") if sc.get(k)]
        assert drawn == [], (name, drawn)
        assert profiles.of(tl).data.fx is not None          # style values exist, but only as parameters
