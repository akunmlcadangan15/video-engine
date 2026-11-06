"""Geographic data package: one lat/lng <-> map transform per scene projection (equirectangular, Web Mercator) for pins,
props, gates and geometry; regions with several rings (islands, holes) under the even-odd rule; Natural Earth
features converted to regions with one flag domain over all islands (video_engine/projection.py, geodata.py)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import geodata, hyperframes, projection, props
from video_engine.gates import geo
from video_engine.timeline import GsapScene, Pin, Region

from test_draft import no_sleep, proj  # noqa: F401  (fixtures)

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
HOLE = [[0.45, 0.45], [0.55, 0.45], [0.55, 0.55], [0.45, 0.55]]
ISLE = [[0.1, 0.8], [0.2, 0.8], [0.2, 0.9], [0.1, 0.9]]
SEA = [-12.0, 94.0, 22.0, 142.0]   # south, west, north, east


# projection ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("proj", ["equirectangular", "mercator"])
def test_good_projection_round_trip(proj):
    for lat, lng in [(-6.2, 106.8), (14.6, 121.0), (0.0, 118.0), (21.9, 141.9), (-11.9, 94.1)]:
        x, y = projection.to_map(lat, lng, SEA, proj)
        assert 0 <= x <= 1 and 0 <= y <= 1
        la, lo = projection.to_lat_lng(x, y, SEA, proj)
        assert abs(la - lat) < 1e-9 and abs(lo - lng) < 1e-9


def test_good_mercator_differs_from_linear_at_high_latitude():
    b = [30.0, 0.0, 70.0, 40.0]
    _, ye = projection.to_map(60.0, 10.0, b, "equirectangular")
    _, ym = projection.to_map(60.0, 10.0, b, "mercator")
    assert abs(ye - 0.25) < 1e-12                     # linear: 10 of 40 degrees from the top
    expect = (projection.merc_y(70) - projection.merc_y(60)) / (projection.merc_y(70) - projection.merc_y(30))
    assert abs(ym - expect) < 1e-12 and ym > ye + 0.05


def test_good_dateline_east_bounds_beyond_180():
    b = [-30.0, 160.0, 10.0, 200.0]                    # Pacific map across the antimeridian
    x, _ = projection.to_map(-17.7, -178.0, b, "equirectangular")   # Fiji east of 180
    assert abs(x - (182.0 - 160.0) / 40.0) < 1e-12


def test_bad_mercator_bounds_past_the_pole_limit():
    with pytest.raises(ValidationError, match="85.05"):
        GsapScene(map_asset="assets/map.jpg", map_bounds=[50, 0, 89, 40], map_projection="mercator")


def test_good_pins_props_and_gates_share_the_transform(project):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(map_bounds=[30, 0, 70, 40], map_projection="mercator",
                  pins=[{"label": "Oslo", "lat": 59.9, "lng": 10.75}])
    p, tl = project(mut)
    sc = tl.segments[2].gsap_scene_config
    mw, mh = 1000.0, 1000.0
    x, y = projection.to_map(59.9, 10.75, sc.map_bounds, "mercator")
    assert hyperframes._pin_xy(sc.pins[0], sc, mw, mh) == pytest.approx((x * mw, y * mh))
    assert geo._pin_xy(sc.pins[0], sc) == pytest.approx((x, y))
    la, lo = geo._pin_ll(Pin(label="P", x=x, y=y), sc)
    assert la == pytest.approx(59.9) and lo == pytest.approx(10.75)
    prop = type("P", (), {"lat": 59.9, "lng": 10.75, "asset": "a.png", "path": None, "x": None})()
    assert props.map_point(prop, sc, mw, mh) == pytest.approx((x * mw, y * mh))


# multi-ring regions -------------------------------------------------------------------------------------------------
def test_good_region_rings_keep_old_polygon_compatible():
    r = Region(entity="A", polygon=SQ, rings=[HOLE, ISLE])
    assert r.polygon == SQ and r.all_rings() == [SQ, HOLE, ISLE]
    assert Region(entity="B", polygon=SQ).all_rings() == [SQ]


@pytest.mark.parametrize("rings, msg", [([[[0.1, 0.1], [0.2, 0.2]]], "at least 3"),
                                        ([[[0.1, 0.1], [1.3, 0.2], [0.2, 0.3]]], "within 0..1")])
def test_bad_region_ring_schema(rings, msg):
    with pytest.raises(ValidationError, match=msg):
        Region(entity="A", polygon=SQ, rings=rings)


def test_bad_multi_ring_region_on_globe_is_pending():
    with pytest.raises(ValidationError, match="rings"):
        GsapScene(map_asset="assets/w.jpg", regions=[{"entity": "A", "polygon": SQ, "rings": [ISLE]}],
                  globe={"keys": [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 2}]})


def test_good_geo_gate_point_in_archipelago_even_odd():
    r = Region(entity="A", polygon=SQ, rings=[HOLE, ISLE])
    assert geo.in_region(0.35, 0.35, r) and geo.in_region(0.15, 0.85, r)
    assert not geo.in_region(0.5, 0.5, r) and not geo.in_region(0.9, 0.1, r)


def test_good_scene_data_rings_and_flag_domain_cover_all_islands(project):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["regions"] = [{"entity": "A", "polygon": SQ, "rings": [HOLE, ISLE]}]
    p, tl = project(mut)
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    R = sc["regions"][0]
    mw, mh = sc["map_size"]
    assert len(R["rings"]) == 3 and R["rings"][2][0] == [round(0.1 * mw, 2), round(0.8 * mh, 2)]
    assert R["bbox"] == [round(0.1 * mw, 2), round(0.3 * mh, 2), round(0.7 * mw, 2), round(0.9 * mh, 2)]


def test_good_rendered_hole_stays_unpainted_and_island_painted(project, tmp_path):
    big = [[0.2, 0.25], [0.8, 0.25], [0.8, 0.65], [0.2, 0.65]]
    hole = [[0.4, 0.38], [0.6, 0.38], [0.6, 0.52], [0.4, 0.52]]
    isle = [[0.15, 0.75], [0.35, 0.75], [0.35, 0.85], [0.15, 0.85]]

    def shot(regs, name):
        def mut(d):
            sc = d["segments"][2]["gsap_scene_config"]
            sc.update(pins=[], cards=[], camera_path=[{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.0},
                                                      {"at_frame": 40, "focus": [0.5, 0.5], "zoom": 1.0}])
            if regs is not None:
                sc["regions"] = regs
        pp, tl = project(mut)
        d, scene = hyperframes.build_dir(tl.segments[2], tl, pp, tmp_path / name)
        res = hyperframes.probe_dom(d, [1.0], 1080, 1920, tmp_path / ("s" + name))
        return np.asarray(Image.open(res["times"][0]["png"]).convert("RGB")).astype(int), scene

    with_r, scene = shot([{"entity": "A", "polygon": big, "rings": [hole, isle]}], "r")
    plain, _ = shot(None, "n")
    diff = np.abs(with_r - plain).max(axis=2) > 25
    cam = scene["camera"]["start"]
    mw, mh = scene["map_size"]
    px = lambda x, y: (round(y * mh * cam["s"] + cam["y"]), round(x * mw * cam["s"] + cam["x"]))
    win = lambda x, y: diff[px(x, y)[0] - 8:px(x, y)[0] + 8, px(x, y)[1] - 8:px(x, y)[1] + 8].mean()
    assert win(0.3, 0.3) > 0.8          # main body painted
    assert win(0.5, 0.45) < 0.1         # hole left clear
    assert win(0.25, 0.8) > 0.8         # island painted


# Natural Earth -> region --------------------------------------------------------------------------------------------
def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _feat(iso, geom):
    return {"type": "Feature", "properties": {"ISO_A2_EH": iso, "ISO_A2": iso, "NAME": iso}, "geometry": geom}


ARCH = _feat("XA", {"type": "MultiPolygon", "coordinates": [
    [[[100, 0], [110, 0], [110, 10], [100, 10], [100, 0]], [[104, 4], [106, 4], [106, 6], [104, 6], [104, 4]]],
    [[[120, 0], [122, 0], [122, 2], [120, 2], [120, 0]]],
    [[[200 - 360, 0], [201 - 360, 0], [201 - 360, 1], [200 - 360, 0]]]]})     # far outside the map


def test_good_feature_to_region_multipolygon_holes_and_crop(tmp_path):
    f = tmp_path / "c.geojson"
    f.write_text(json.dumps(_fc(ARCH)))
    reg = geodata.country_region(f, "xa", bounds=[-5, 95, 15, 125], projection="equirectangular", entity="Xa")
    assert reg["entity"] == "Xa" and reg["flag"] == "xa"
    r = Region.model_validate({k: v for k, v in reg.items() if k != "flag"})   # "xa" has no flag file
    assert len(r.all_rings()) == 3                        # main, hole, second island; the far one is dropped
    xs = [p[0] for p in r.polygon]
    assert min(xs) == pytest.approx((100 - 95) / 30, abs=1e-4) and max(xs) == pytest.approx((110 - 95) / 30, abs=1e-4)
    lx, ly = geodata.label_anchor(r)                      # the centroid falls in the hole: moved inside the land
    assert geo.in_region(lx, ly, r) and (100 - 95) / 30 < lx < (110 - 95) / 30


def test_good_crop_clips_ring_at_map_edge(tmp_path):
    f = tmp_path / "c.geojson"
    f.write_text(json.dumps(_fc(ARCH)))
    reg = geodata.country_region(f, "XA", bounds=[-5, 105, 15, 125], projection="mercator")
    r = Region.model_validate({k: v for k, v in reg.items() if k != "flag"})
    assert all(0 <= v <= 1 for ring in r.all_rings() for p in ring for v in p)
    assert min(p[0] for p in r.polygon) == pytest.approx(0.0)


def test_bad_unknown_iso_and_nothing_in_bounds(tmp_path):
    f = tmp_path / "c.geojson"
    f.write_text(json.dumps(_fc(ARCH)))
    with pytest.raises(ValueError, match="no feature"):
        geodata.country_region(f, "QQ", bounds=[-5, 95, 15, 125])
    with pytest.raises(ValueError, match="outside the map bounds"):
        geodata.country_region(f, "XA", bounds=[40, 0, 60, 20])


def test_good_simplify_keeps_shape_within_tolerance():
    ring = [[math.cos(t) * 0.3 + 0.5, math.sin(t) * 0.3 + 0.5] for t in np.linspace(0, 2 * math.pi, 2000)[:-1]]
    s = geodata.simplify(ring, 0.002)
    assert 20 < len(s) < 400
    def seg_dist(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)
    assert max(min(seg_dist(p, a, b) for a, b in zip(s, s[1:] + s[:1])) for p in ring) <= 0.002 + 1e-9


def test_good_base_map_sidecar_and_true_shape(tmp_path):
    f = tmp_path / "c.geojson"
    f.write_text(json.dumps(_fc(ARCH)))
    for proj in ("equirectangular", "mercator"):
        out = geodata.base_map(f, tmp_path / f"{proj}.jpg", bounds=[-5, 95, 15, 125], projection=proj, width=600)
        side = json.loads(out.with_suffix(".json").read_text())
        assert side["map_bounds"] == [-5, 95, 15, 125] and side["projection"] == proj and len(side["data_sha256"]) == 64
        w, h = Image.open(out).size
        assert (w, h) == (600, geodata.map_height(600, [-5, 95, 15, 125], proj))
        im = np.asarray(Image.open(out).convert("RGB")).astype(int)
        x, y = projection.to_map(2.0, 103.0, [-5, 95, 15, 125], proj)   # inside the main land square
        assert tuple(im[round(y * h), round(x * w)]) != geodata.OCEAN
    assert geodata.map_height(600, [-5, 95, 15, 125], "mercator") != geodata.map_height(600, [-5, 95, 15, 125])


def test_good_draft_regions_rings_and_mercator_sidecar(proj):
    from video_engine import draft
    from test_draft_scene import plan
    (proj / "assets" / "map.json").write_text(json.dumps({"map_bounds": [-11, 95, 6, 141], "projection": "mercator"}))
    inp = draft.collect(proj)
    sc = draft.assemble(inp, plan(map_regions=[{"entity": "A", "polygon": SQ, "rings": [HOLE, ISLE]}]))[0] \
        .segments[1].gsap_scene_config
    assert sc.map_projection == "mercator" and sc.regions[0].rings == [HOLE, ISLE]
