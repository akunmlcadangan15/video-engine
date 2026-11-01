"""P13 graphic dialogue bubbles: model, anchor resolution, determinism and a DOM placement check."""
import pytest
from pydantic import ValidationError

from video_engine import hyperframes, timeline

BUBBLE = {"text": "Land ho!", "anchor": {"xy": [0.5, 0.4]}, "side": "auto", "at_frame": 10,
          "out_frame": 80, "style": "light"}


def _mut(extra):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(extra)
    return mut


def test_good_bubble_values():
    timeline.Bubble(**BUBBLE)


@pytest.mark.parametrize("bad", [{**BUBBLE, "text": ""}, {**BUBBLE, "text": "x" * 61},
                                 {**BUBBLE, "anchor": {"xy": [0.5, 0.4], "pin": 0}},
                                 {**BUBBLE, "anchor": {}}, {**BUBBLE, "out_frame": 10},
                                 {**BUBBLE, "anchor": {"xy": [1.2, 0.4]}}])
def test_bad_bubble_values_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.Bubble(**bad)


def test_bad_bubble_pin_index_rejected():
    with pytest.raises(ValidationError, match="pin index 4 out of range"):
        timeline.GsapScene(map_asset="assets/map.jpg", bubbles=[{**BUBBLE, "anchor": {"pin": 4}}])


def test_bad_bubble_overlay_index_rejected(project):
    with pytest.raises(Exception, match="overlay index 2 out of range"):
        project(_mut({"bubbles": [{**BUBBLE, "anchor": {"overlay": 2}}]}))


def test_good_bubble_anchor_kinds(project):
    d, tl = project(_mut({"regions": [{"entity": "A", "polygon": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6]]}],
                          "bubbles": [{**BUBBLE, "anchor": {"pin": 0}},
                                      {**BUBBLE, "anchor": {"region": 0}},
                                      {**BUBBLE, "anchor": {"xy": [0.5, 0.4]}}]}))
    bs = hyperframes.scene_data(tl.segments[2], tl, d)["bubbles"]
    assert [b["anchor"]["kind"] for b in bs] == ["pin", "region", "xy"]
    assert bs[2]["anchor"] == {"kind": "xy", "x": 0.5, "y": 0.4}


def test_good_bubble_data_is_deterministic(project):
    d, tl = project(_mut({"bubbles": [BUBBLE]}))
    a = hyperframes.scene_data(tl.segments[2], tl, d)["bubbles"]
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(tl.segments[2], tl, d)["bubbles"]
    assert a == b


def test_good_scene_without_bubbles_is_unchanged(project, tmp_path):
    d, tl = project()
    assert "bubbles" not in hyperframes.scene_data(tl.segments[2], tl, d)
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    assert "fx_bubbles.js" not in (out / "index.html").read_text()


def test_good_bubble_dom_fits_safe_area(project, tmp_path):
    d, tl = project(_mut({"bubbles": [BUBBLE]}))
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(out, [1.0], 1080, 1920)
    b = next(r for r in res["times"][0]["rects"] if r["id"] == "bubble:0")
    assert b["opacity"] > 0.9
    assert b["x"] >= 60 and b["x"] + b["w"] <= 1080 - 60
    assert b["y"] >= 100 and b["y"] + b["h"] <= 0.72 * 1920
    pin = next(r for r in res["times"][0]["rects"] if r["id"] == "pin:0")
    assert (b["x"] >= pin["x"] + pin["w"] or b["x"] + b["w"] <= pin["x"]
            or b["y"] >= pin["y"] + pin["h"] or b["y"] + b["h"] <= pin["y"])
