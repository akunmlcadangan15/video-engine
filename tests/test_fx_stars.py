"""P14 deterministic starfield: model, seeded positions, default off and DOM marker."""
import pytest
from pydantic import ValidationError

from video_engine import hyperframes, timeline


def _mut(extra):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(extra)
    return mut


STARS = {"backdrop": {"stars": {"count": 120, "seed": 7, "twinkle": 0.15, "parallax": 0.2}}}


def test_good_star_values():
    sc = timeline.GsapScene(map_asset="assets/map.jpg", **STARS)
    assert sc.backdrop.stars.seed == 7


@pytest.mark.parametrize("bad", [{"count": -1}, {"count": 5001}, {"seed": -1}, {"twinkle": 2}, {"parallax": -0.1}])
def test_bad_star_values_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.GsapScene(map_asset="assets/map.jpg", backdrop={"stars": bad})


def test_good_star_seed_defaults_to_project_and_segment(project):
    d, tl = project(_mut({"backdrop": {"stars": {"count": 40}}}))
    a = hyperframes.scene_data(tl.segments[2], tl, d)["backdrop"]["stars"]
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(tl.segments[2], tl, d)["backdrop"]["stars"]
    assert a["seed"] == b["seed"] and a["stars"] == b["stars"]
    assert a["seed"] != 7
    assert len(a["stars"]) == 40
    assert all(0 <= s["x"] <= 1 and 0 <= s["y"] <= 1 and 1 <= s["r"] <= 2 for s in a["stars"])


def test_good_star_explicit_seed_changes_positions(project):
    d, tl = project(_mut(STARS))
    a = hyperframes.scene_data(tl.segments[2], tl, d)["backdrop"]["stars"]
    d2, tl2 = project(_mut({"backdrop": {"stars": {"count": 120, "seed": 8}}}))
    b = hyperframes.scene_data(tl2.segments[2], tl2, d2)["backdrop"]["stars"]
    assert a["stars"] != b["stars"]


def test_good_scene_without_stars_is_unchanged(project, tmp_path):
    d, tl = project()
    assert "backdrop" not in hyperframes.scene_data(tl.segments[2], tl, d)
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    assert "fx_backdrop.js" not in (out / "index.html").read_text() and "backdrop" not in sc


def test_good_stars_dom_marker(project, tmp_path):
    d, tl = project(_mut(STARS))
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(out, [1.5], 1080, 1920)
    assert "fx:backdrop" in {r["id"] for r in res["times"][0]["rects"]}
