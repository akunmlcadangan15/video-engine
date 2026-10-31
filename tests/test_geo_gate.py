"""G02 geo/fact gate: region polygon sanity, beat pins inside their region, label/region contradictions and the
Natural Earth land check. GOOD and BAD cases."""
import json

import pytest

from video_engine import config, profiles
from video_engine.gates import geo

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]


def scene(d, pins=None, regions=None, beats=None, bounds=None):
    d["profile"] = "geoverum"
    sc = d["segments"][2]["gsap_scene_config"]
    sc["pins"] = pins or [{"label": "Titik A", "x": 0.5, "y": 0.5}]
    if regions is not None:
        sc["regions"] = regions
    if beats is not None:
        sc["beats"] = beats
    if bounds is not None:
        sc["map_bounds"] = bounds
    return d


def levels(fs, gate="G02"):
    return [f.level for f in fs if f.gate == gate]


def test_good_region_polygon_and_pin_inside_it(project):
    _, tl = project(lambda d: scene(d, regions=[{"entity": "Sekutu", "polygon": SQ}],
                                    beats=[{"at_frame": 0, "region": 0, "pins": [0]}]))
    fs = geo.run(tl)
    assert levels(fs) == ["pass"]


def test_bad_pin_outside_its_beat_region(project):
    _, tl = project(lambda d: scene(d, pins=[{"label": "Titik A", "x": 0.1, "y": 0.1}],
                                    regions=[{"entity": "Sekutu", "polygon": SQ}],
                                    beats=[{"at_frame": 0, "region": 0, "pins": [0]}]))
    fs = geo.run(tl)
    assert levels(fs) == ["fail"] and "outside beat region 0" in fs[0].message


def test_bad_region_polygon_crosses_itself(project):
    bowtie = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.5, 0.1], [0.2, 0.8]]
    _, tl = project(lambda d: scene(d, regions=[{"entity": "Sekutu", "polygon": bowtie}]))
    fs = geo.run(tl)
    assert levels(fs) == ["fail"] and "crosses itself" in fs[0].message


def test_bad_pin_label_names_a_region_it_sits_outside(project):
    _, tl = project(lambda d: scene(d, pins=[{"label": "Indonesia", "x": 0.1, "y": 0.1}],
                                    regions=[{"entity": "Indonesia", "polygon": SQ}]))
    fs = geo.run(tl)
    assert levels(fs) == ["fail"] and "names region 'Indonesia'" in fs[0].message


def _land(root):
    d = root / "natural-earth" / "natural-earth-vector" / "geojson"
    d.mkdir(parents=True)
    (d / "ne_110m_land.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}}]}))
    return root


def test_good_and_bad_land_check_against_the_geo_library(project, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE_GEO_LIBRARY", str(_land(tmp_path / "geo")))
    geo._land.cache_clear()
    pins = [{"label": "Kota", "lat": 0.0, "lng": 0.0}, {"label": "Laut", "lat": 1.5, "lng": 1.5}]
    _, tl = project(lambda d: scene(d, pins=pins, bounds=[-2.0, -2.0, 2.0, 2.0]))
    assert geo.on_land(0.0, 0.0) is True and geo.on_land(1.5, 1.5) is False
    warn = geo.run(tl, require_land=False)
    assert levels(warn) == ["warn"] and "not on land" in warn[0].message
    fail = geo.run(tl, require_land=True)
    assert levels(fail) == ["fail"]


def test_good_land_check_skips_without_the_geo_library(project, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE_GEO_LIBRARY", str(tmp_path / "missing"))
    geo._land.cache_clear()
    pins = [{"label": "Kota", "lat": 0.0, "lng": 0.0}]
    _, tl = project(lambda d: scene(d, pins=pins, bounds=[-2.0, -2.0, 2.0, 2.0]))
    assert geo.on_land(0.0, 0.0) is None and levels(geo.run(tl, require_land=True)) == ["pass"]


def test_bad_geo_table_unknown_key():
    with pytest.raises(Exception):
        profiles.GeoGate.model_validate({"require_land": True, "ocean": 1})
