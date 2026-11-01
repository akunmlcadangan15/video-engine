"""P15 flag-icon characters: schema, credits, scene wiring, deterministic seed and the DOM box."""
import pytest
from pydantic import ValidationError

from video_engine import assets, hyperframes, timeline
from video_engine.gates import layer1


def _scene(d):
    sc = d["segments"][2]["gsap_scene_config"]
    sc["flag_characters"] = [{"flag": "th", "xy": [0.5, 0.55], "height_pct": 14, "eyes": "look_at:0",
                              "at_frame": 0, "out_frame": 90}]


def test_good_flag_character_and_bad_values():
    c = timeline.FlagCharacter(flag="th", xy=[0.5, 0.55], height_pct=14, eyes="look_at:0")
    assert c.flag.code == "th" and c.out_frame is None
    assert timeline.FlagCharacter(flag="id", xy=[0.2, 0.3], eyes="look_left").eyes == "look_left"
    for bad in ({"flag": "th", "xy": [1.2, 0.5]}, {"flag": "th", "xy": [0.5, 0.5], "height_pct": 0},
                {"flag": "th", "xy": [0.5, 0.5], "eyes": "wink"},
                {"flag": "th", "xy": [0.5, 0.5], "at_frame": 20, "out_frame": 20}):
        with pytest.raises(ValidationError):
            timeline.FlagCharacter(**bad)
    with pytest.raises(ValidationError, match="look_at pin 1 out of range"):
        timeline.GsapScene(map_asset="m.jpg", flag_characters=[{"flag": "th", "xy": [0.5, 0.5], "eyes": "look_at:1"}])


def test_good_scene_credits_build_and_dom(project, tmp_path):
    path, tl = project(_scene)
    credited, added = assets.ensure_credits(tl, path)
    assert any(x["asset_id"] == "flag_th" and x["license"] == "MIT" for x in added)
    assert any(r[3] == "flag character" for r in layer1.visual_refs(credited))
    build, scene = hyperframes.build_dir(credited.segments[2], credited, path, tmp_path / "hf")
    c = scene["flag_characters"][0]
    assert (build / c["href"]).is_file() and (build / "fx2.js").is_file()
    assert sum(v * v for v in c["look"]) == pytest.approx(1.0, abs=1e-3)   # look_at pin: a unit direction
    res = hyperframes.probe_dom(build, [15 / scene["fps"]], 1080, 1920)
    rects = {r["id"]: r for r in res["times"][0]["rects"]}
    assert rects["flagchar:0"]["w"] > 0 and rects["flagchar:0"]["opacity"] > 0.5
    assert not res.get("errors")


def test_good_scene_seed_and_data_are_deterministic(project):
    path, tl = project(_scene)
    seg = tl.segments[2]
    a = hyperframes.scene_data(seg, tl, path)
    b = hyperframes.scene_data(seg, tl, path)
    hyperframes.clear_scene_cache()
    c = hyperframes.scene_data(seg, tl, path)
    assert a["flag_characters"] == b["flag_characters"] == c["flag_characters"]
    assert c["flag_characters"][0]["seed"] >= 0


def test_good_unset_flag_characters_keep_scene_identical(project):
    path, tl = project()
    seg = tl.segments[2]
    before = hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)
    seg.gsap_scene_config.flag_characters = [timeline.FlagCharacter(flag="th", xy=[0.5, 0.5])]
    assert "flag_characters" in hyperframes.scene_data(seg, tl, path)
    seg.gsap_scene_config.flag_characters = None
    hyperframes.clear_scene_cache()
    assert (hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)) == before
