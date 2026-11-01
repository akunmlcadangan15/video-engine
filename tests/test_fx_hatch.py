"""P17 region hatching: model, profile merge, scene data, default off and a DOM marker."""
import pytest
from pydantic import ValidationError

from video_engine import hyperframes, timeline

REGION = {"entity": "A", "polygon": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
          "hatch": {"angle": 45, "spacing_px": 10, "line_px": 2, "colour": "#FFFFFF", "alpha": 0.6}}


def _mut(extra):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(extra)
    return mut


def test_good_region_hatch_values():
    timeline.Region(**REGION)


@pytest.mark.parametrize("bad", [{"angle": -1}, {"angle": 181}, {"spacing_px": 1}, {"line_px": 0},
                                 {"colour": "white"}, {"alpha": 2}])
def test_bad_region_hatch_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.Region(**{**REGION, "hatch": bad})


def test_good_hatch_merges_profile_defaults(project):
    d, tl = project(_mut({"regions": [REGION]}))
    h = hyperframes.scene_data(tl.segments[2], tl, d)["regions"][0]["hatch"]
    assert h == {"angle": 45.0, "spacing_px": 10.0, "line_px": 2.0, "colour": "#ffffff", "alpha": 0.6}


def test_good_region_without_hatch_is_unchanged(project):
    d, tl = project(_mut({"regions": [{"entity": "A", "polygon": REGION["polygon"]}]}))
    assert "hatch" not in hyperframes.scene_data(tl.segments[2], tl, d)["regions"][0]


def test_good_hatch_dom_marker(project, tmp_path):
    d, tl = project(_mut({"regions": [REGION]}))
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(out, [1.0], 1080, 1920)
    assert "fx:hatch:0" in {r["id"] for r in res["times"][0]["rects"]}
