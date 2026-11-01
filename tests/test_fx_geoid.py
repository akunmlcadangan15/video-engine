"""P18 exaggerated Earth: schema (globe only), seeded harmonics, deterministic scene data and the three.js render."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import fx2, hyperframes, timeline

GEOID = {"exaggeration": 0.08, "colour_ramp": ["#2B5BD7", "#3FB27F", "#F2D04B", "#D9483B"],
         "rotate_deg_s": 12, "at_frame": 0}


def _globe(project):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc["map_asset"] = "assets/globe.jpg"
        sc["pins"] = []
        sc["globe"] = {"keys": [{"at_frame": 0, "lat": 12, "lng": 40, "altitude": 3.0}]}
        sc["geoid"] = dict(GEOID)
    d, tl = project(mut)
    Image.new("RGB", (512, 256), (30, 80, 140)).save(d / "assets" / "globe.jpg")
    return d, tl


def test_good_geoid_model_and_bad_values():
    g = timeline.Geoid(**GEOID)
    assert g.colour_ramp[-1] == "#D9483B"
    for bad in ({"exaggeration": 0.6}, {"colour_ramp": ["#fff"]}, {"rotate_deg_s": 400}):
        with pytest.raises(ValidationError):
            timeline.Geoid(**bad)
    with pytest.raises(ValidationError, match="geoid needs a globe scene"):
        timeline.GsapScene(map_asset="m.jpg", geoid=dict(GEOID))


def test_good_scene_harmonics_and_determinism(project):
    d, tl = _globe(project)
    a = hyperframes.scene_data(tl.segments[2], tl, d)
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(tl.segments[2], tl, d)
    g = a["globe"]["geoid"]
    assert a["globe"]["geoid"] == b["globe"]["geoid"]
    assert len(g["harmonics"]) == 6 and g["exaggeration"] == 0.08
    vals = [fx2.geoid_height(g["harmonics"], lat, lng) for lat in range(-80, 81, 20) for lng in range(-180, 181, 30)]
    assert min(vals) >= -1.0 and max(vals) <= 1.0 and max(vals) - min(vals) > 0.2
    assert fx2.geoid_height(g["harmonics"], 12, 40) == pytest.approx(fx2.geoid_height(g["harmonics"], 12, 40))


def test_good_build_and_three_js_render(project, tmp_path):
    d, tl = _globe(project)
    build, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    html = (build / "index.html").read_text()
    assert "globe.js" in html and "fx2.js" not in html
    assert (build / "assets" / "three" / "three.module.js").is_file()
    dom = hyperframes.probe_dom(build, [1.0], 1080, 1920, snap_dir=tmp_path / "s")
    assert not dom.get("errors")
    shot = np.asarray(Image.open(tmp_path / "s" / "snap_0.png").convert("RGB"), dtype=float)
    assert shot.std() > 5 and (shot.max(axis=2) - shot.min(axis=2)).mean() > 3   # coloured relief, not a flat frame


def test_good_unset_geoid_keeps_scene_identical(project):
    d, tl = _globe(project)
    seg = tl.segments[2]
    seg.gsap_scene_config.geoid = None
    before = hyperframes.scene_data(seg, tl, d), hyperframes.keys(seg, tl, d)
    seg.gsap_scene_config.geoid = timeline.Geoid(**GEOID)
    assert "geoid" in hyperframes.scene_data(seg, tl, d)["globe"]
    seg.gsap_scene_config.geoid = None
    hyperframes.clear_scene_cache()
    assert (hyperframes.scene_data(seg, tl, d), hyperframes.keys(seg, tl, d)) == before
