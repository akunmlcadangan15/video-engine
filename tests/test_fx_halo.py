"""P14 atmospheric rim on a globe: model, scene block, default off and a WebGL DOM marker."""
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, timeline

HALO = {"colour": "#7EC8FF", "width_pct": 3.0, "alpha": 0.6}


def _globe(project, halo=True):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        for k in ("camera_path", "beats", "routes", "borders", "morph", "props", "layers", "map_bounds"):
            sc.pop(k, None)
        sc.update(map_asset="assets/globe.jpg", pins=[{"label": "Titik A", "lat": 10, "lng": 20}],
                  globe={"keys": [{"at_frame": 0, "lat": 10, "lng": 20, "altitude": 2.0},
                                  {"at_frame": 60, "lat": 12, "lng": 22, "altitude": 1.5}],
                         **({"halo": HALO} if halo else {})})
    d, tl = project(mut)
    img = d / "assets" / "globe.jpg"
    if not img.exists():
        Image.new("RGB", (1024, 512), (30, 60, 120)).save(img)
    return d, tl


def test_good_halo_values():
    timeline.GlobeHalo(**HALO)


@pytest.mark.parametrize("bad", [{"colour": "cyan"}, {"width_pct": 0}, {"width_pct": 21}, {"alpha": 2}])
def test_bad_halo_values_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.GlobeHalo(**bad)


def test_good_halo_scene_block(project):
    d, tl = _globe(project)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    assert sc["globe"]["halo"] == {**HALO, "colour": "#7ec8ff"}


def test_good_globe_without_halo_is_unchanged(project):
    d, tl = _globe(project, halo=False)
    assert "halo" not in hyperframes.scene_data(tl.segments[2], tl, d)["globe"]


def test_good_halo_dom_marker(project, tmp_path):
    d, tl = _globe(project)
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(out, [1.0], 1080, 1920)
    ids = {r["id"] for r in res["times"][0]["rects"]}
    assert "fx:halo" in ids
