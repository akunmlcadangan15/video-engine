"""P17 timezone bands on a flat map: model, map-pixel geometry, default off and a DOM marker."""
import pytest
from pydantic import ValidationError

from video_engine import hyperframes, timeline

TZ = {"timezones": {"bands": [{"from_lng": 95, "to_lng": 115, "label": "UTC+7", "colour": "#7EC8FF"}], "alpha": 0.25}}


def _mut(extra):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(extra)
    return mut


def test_good_timezone_values():
    sc = timeline.GsapScene(map_asset="assets/map.jpg", **TZ)
    assert sc.timezones.bands[0].label == "UTC+7"


@pytest.mark.parametrize("bad", [{"from_lng": 120, "to_lng": 110, "label": "UTC+7"},
                                 {"from_lng": 90, "to_lng": 105, "label": ""},
                                 {"from_lng": -181, "to_lng": 105, "label": "UTC+7"},
                                 {"from_lng": 90, "to_lng": 105, "label": "X" * 13}])
def test_bad_timezone_band_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.GsapScene(map_asset="assets/map.jpg", timezones={"bands": [bad]})


def test_bad_timezone_alpha_rejected():
    with pytest.raises(ValidationError):
        timeline.GsapScene(map_asset="assets/map.jpg", timezones={**TZ["timezones"], "alpha": 2})


def test_good_bands_use_map_bounds(project):
    d, tl = project(_mut({**TZ, "map_bounds": [-10, 90, 10, 120]}))
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    mw, mh = sc["map_size"]
    b = sc["timezones"]["bands"][0]
    assert b["x0"] == pytest.approx((95 - 90) / 30 * mw)
    assert b["x1"] == pytest.approx((115 - 90) / 30 * mw)
    assert b["ly"] == pytest.approx(mh / 2)


def test_good_scene_without_timezones_is_unchanged(project, tmp_path):
    d, tl = project()
    assert "timezones" not in hyperframes.scene_data(tl.segments[2], tl, d)
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    assert "fx_timezones.js" not in (out / "index.html").read_text()


def test_good_timezones_dom_marker(project, tmp_path):
    d, tl = project(_mut(TZ))
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    res = hyperframes.probe_dom(out, [1.0], 1080, 1920)
    ids = {r["id"] for r in res["times"][0]["rects"]}
    assert "fx:timezones" in ids and "tz:0" in ids
