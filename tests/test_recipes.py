"""Story pattern compiler (video_engine/recipes.py): five builders turn narrative intent (places, countries, values,
times) into a native HyperFrames map scene. Camera keys, element timing and text placement come from the geometry
(bounds, projection, map size, frame safe area), never from fixed coordinates; every scene validates as GsapScene and
keeps its elements inside the R63 safe area at the views the builder chose."""
from __future__ import annotations

import json

import pytest

from video_engine import recipes, projection
from video_engine.gates.layer2 import SAFE_BOTTOM, SAFE_TOP, SAFE_X
from video_engine.timeline import GsapScene

W, H = 1080, 1920


def fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def box(iso, w, s, e, n, name=None):
    return {"type": "Feature", "properties": {"ISO_A2_EH": iso, "NAME": name or iso},
            "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


@pytest.fixture
def geo(tmp_path):
    p = tmp_path / "c.geojson"
    p.write_text(json.dumps(fc(box("TH", 98, 6, 105, 20, "Thailand"), box("VN", 102, 9, 109, 23, "Vietnam"),
                               box("CL", -75, -55, -67, -18, "Chile"), box("LK", 79.5, 6, 82, 9.8, "Sri Lanka"))))
    return p


def mp(bounds, proj="equirectangular", w=9000, h=None):
    from video_engine import geodata
    return recipes.MapInfo(path="assets/map.jpg", asset_id="map", width=w,
                           height=h or geodata.map_height(w, bounds, proj), bounds=bounds, projection=proj)


FR = recipes.Frame(W, H, fps=30, n=240, max_zoom=4.0)


def screen(sc: dict, m, f, key, x, y):
    """Screen px of normalised map point (x, y) at camera key `key` (dict with focus, zoom)."""
    from video_engine.hyperframes import _state
    mw, mh = recipes.cover(m, f)
    st = _state((key["focus"][0] * mw, key["focus"][1] * mh), key["zoom"], f.W, f.H, mw, mh)
    return x * mw * st["s"] + st["x"], y * mh * st["s"] + st["y"]


def inside(pt, pad=0):
    x, y = pt
    return SAFE_X - pad <= x <= W - SAFE_X + pad and SAFE_TOP - pad <= y <= H - SAFE_BOTTOM + pad


def valid(m, built):
    return GsapScene.model_validate({"map_asset": m.path, "map_asset_id": m.asset_id, "map_bounds": m.bounds,
                                     "map_projection": m.projection, **built.scene})


# country reveal ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("iso, bounds", [("TH", [-10, 85, 30, 125]), ("CL", [-60, -95, 0, -45]),
                                         ("LK", [0, 70, 20, 90])], ids=["compact", "tall", "small-island"])
def test_good_country_reveal_fits_the_country_whatever_its_shape(geo, iso, bounds):
    m = mp(bounds)
    b = recipes.country_reveal(m, FR, geojson=geo, countries=[iso])
    sc = valid(m, b).model_dump()
    keys = sc["camera_path"]
    assert keys[0]["at_frame"] == 0 and keys[-1]["zoom"] > keys[0]["zoom"]           # orient wide, then focus
    r = sc["regions"][0]
    assert r["flag"]["code"] == iso.lower() and r["label"] and r["at_frame"] >= keys[1]["at_frame"] - 15
    xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
    for corner in [(min(xs), min(ys)), (max(xs), max(ys))]:
        assert inside(screen(sc, m, FR, keys[-1], *corner), pad=2), (iso, corner)
    assert "fit" in b.why


def test_good_country_reveal_zoom_follows_country_size(geo):
    m = mp([-10, 60, 30, 125], w=30000)                    # resolution is no limit here
    z = {iso: recipes.country_reveal(m, FR, geojson=geo, countries=[iso]).scene["camera_path"][-1]["zoom"]
         for iso in ("TH", "LK")}
    assert z["LK"] > z["TH"]                                       # smaller country, deeper zoom


def test_good_two_countries_reveal_in_order_and_end_on_both(geo):
    m = mp([-10, 85, 30, 125])
    sc = recipes.country_reveal(m, FR, geojson=geo, countries=["TH", "VN"]).scene
    assert [r["entity"] for r in sc["regions"]] == ["Thailand", "Vietnam"]
    assert sc["regions"][0]["at_frame"] < sc["regions"][1]["at_frame"]
    last = sc["camera_path"][-1]
    for r in sc["regions"]:
        for p in (r["polygon"][0], r["polygon"][2]):
            assert inside(screen(sc, m, FR, last, *p), pad=2)


# follow route --------------------------------------------------------------------------------------------------------
def test_good_follow_route_draws_and_keeps_destination_in_frame():
    m = mp([0, 90, 30, 120])
    pts = [(13.75, 100.5), (17.97, 102.6), (21.03, 105.85)]
    b = recipes.follow_route(m, FR, points=pts, labels=("Bangkok", "Hanoi"))
    sc = valid(m, b).model_dump()
    r, keys = sc["routes"][0], sc["camera_path"]
    assert len(r["points"]) == 3 and r["points"][0] == pytest.approx(list(projection.to_map(*pts[0], m.bounds)), abs=1e-4)
    end = r["at_frame"] + round(r["draw_s"] * FR.fps)
    arrive = [k for k in keys if k["at_frame"] >= end - 1]
    assert arrive, "a camera key lands when the route ends"
    assert inside(screen(sc, m, FR, arrive[0], *r["points"][-1]))
    pins = sc["pins"]
    assert pins[0]["at_frame"] <= r["at_frame"] and pins[1]["at_frame"] >= end - 2


# compare places ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a, b, mode", [
    ({"label": "Jakarta", "lat": -6.2, "lng": 106.8}, {"label": "Bangkok", "lat": 13.75, "lng": 100.5}, "adjacent"),
    ({"label": "Jakarta", "lat": -6.2, "lng": 106.8}, {"label": "Bandung", "lat": -6.9, "lng": 107.6}, "bands"),
], ids=["far-apart", "close"])
def test_good_compare_places_layout_from_geometry(a, b, mode):
    m = mp([-20, 95, 22, 123])                         # a portrait extent: both capitals can share the frame
    a, b = {**a, "value": 10.6, "unit": " jt", "decimals": 1}, {**b, "value": 14.9, "unit": " jt", "decimals": 1}
    built = recipes.compare_places(m, FR, a=a, b=b, source="Contoh ilustratif")
    sc = valid(m, built).model_dump()
    assert f"layout {mode}" in built.why
    figs, pins, view = sc["figures"], sc["pins"], sc["camera_path"][-1]
    big = max(figs, key=lambda f: f["value"])
    assert big["at_frame"] > min(figs, key=lambda f: f["value"])["at_frame"] and big["entity"] == b["label"]
    for fg in figs:
        assert inside((fg["position"][0] * W, fg["position"][1] * H))
    for fg, pn in zip(figs, pins):                       # a figure never sits on a place
        _, py = screen(sc, m, FR, view, pn["x"], pn["y"])
        assert abs(fg["position"][1] * H - py) > 100
    for pn in pins:                                       # the label box (centred above the pin) stays in the safe area
        x, y = screen(sc, m, FR, view, pn["x"], pn["y"])
        half = recipes.label_half_px([pn["label"]])
        assert inside((x - half, y - recipes.PIN_UP_PX)) and inside((x + half, y))
    ups = sorted(zip(figs, pins), key=lambda t: t[1]["y"])   # the northern place keeps the upper figure
    assert ups[0][0]["position"][1] < ups[1][0]["position"][1]
    if mode == "bands":
        assert ups[0][0]["position"][1] < 0.3 < 0.7 < ups[1][0]["position"][1]
    assert any("ilustratif" in (c.get("subtitle") or c["title"]).lower() for c in sc["cards"])


# explain flow --------------------------------------------------------------------------------------------------------
def test_good_explain_flow_reveals_paths_in_narration_order():
    m = mp([-10, 90, 40, 130])
    paths = [[(5, 101), (10, 106), (15, 111)], [(25, 117), (20, 110)], [(2, 102), (8, 116)]]
    sc = valid(m, recipes.explain_flow(m, FR, paths=paths, at_s=[0.5, 2.5, 4.5], legend="Arah aliran")).model_dump()
    assert [r["at_frame"] for r in sc["routes"]] == [15, 75, 135]
    assert sc["cards"][0]["title"] == "Arah aliran"
    last = sc["camera_path"][-1]
    for r in sc["routes"]:
        for p in r["points"]:
            assert inside(screen(sc, m, FR, last, *p), pad=2)


# stat emphasis -------------------------------------------------------------------------------------------------------
def test_good_stat_emphasis_one_value_drives_counter_and_card():
    m = mp([0, 90, 30, 130])
    b = recipes.stat_emphasis(m, FR, value=2350, unit=" km", decimals=0, at_s=1.0, source="BPS 2024",
                              place={"lat": 13.75, "lng": 100.5, "label": "Bangkok"})
    sc = valid(m, b).model_dump()
    f = sc["figures"][0]
    assert f["value"] == 2350 and f["motion"] == "counter" and f["suffix"] == " km" and f["at_frame"] == 30
    assert sc["cards"][0]["subtitle"] == "Sumber: BPS 2024" and sc["cards"][0]["at_frame"] == 30
    assert inside((f["position"][0] * W, f["position"][1] * H))


# dispatch and errors -------------------------------------------------------------------------------------------------
def test_good_compile_dispatches_by_pattern(geo):
    m = mp([-10, 85, 30, 125])
    b = recipes.compile({"pattern": "country_reveal", "countries": ["th"]}, m, FR, geojson=geo)
    assert b.scene["regions"][0]["flag"] == "th"


@pytest.mark.parametrize("recipe, msg", [
    ({"pattern": "fireworks"}, "unknown pattern"),
    ({"pattern": "country_reveal", "countries": ["TH"], "colour": "red"}, "colour: unknown field"),
    ({"pattern": "follow_route", "points": [[13.7, 100.5]]}, "at least 2 points"),
    ({"pattern": "country_reveal", "countries": ["CL"]}, "outside the map bounds"),
], ids=["pattern", "unknown-param", "one-point", "off-map"])
def test_bad_recipe_errors_name_the_problem(geo, recipe, msg):
    with pytest.raises(ValueError, match=msg):
        recipes.compile(recipe, mp([-10, 85, 30, 125]), FR, geojson=geo)


def test_bad_geometry_that_cannot_fit_is_an_error_not_a_crop():
    m = mp([0, 90, 30, 130])                          # landscape map: 30 degrees of longitude cannot fit
    with pytest.raises(ValueError, match="does not fit the vertical frame"):
        recipes.explain_flow(m, FR, paths=[[(5, 92), (10, 128)]])


# draft map_recipe ---------------------------------------------------------------------------------------------------
from test_draft import no_sleep, proj  # noqa: E402,F401  (fixtures)
from test_draft_scene import plan  # noqa: E402


@pytest.fixture
def recipe_proj(proj, tmp_path, monkeypatch):
    from video_engine import config
    lib = tmp_path / "geo"
    d = lib / "natural-earth" / "natural-earth-vector" / "geojson"
    d.mkdir(parents=True)
    (d / "ne_50m_admin_0_countries.geojson").write_text(json.dumps(fc(box("TH", 98, 6, 105, 20, "Thailand"))))
    monkeypatch.setattr(config, "GEO_LIBRARY", lib)
    (proj / "assets" / "map.json").write_text(json.dumps({"map_bounds": [-10, 85, 30, 125]}))
    return proj


def test_good_draft_map_recipe_compiles_to_native_scene(recipe_proj):
    from video_engine import draft
    inp = draft.collect(recipe_proj)
    p = plan(map_recipe={"pattern": "country_reveal", "countries": ["TH"]})
    del p["segments"][1]["map_pins"]
    tl, notes, _ = draft.assemble(inp, p)
    sc = tl.segments[1].gsap_scene_config
    assert sc.regions[0].entity == "Thailand" and sc.regions[0].flag.code == "th" and len(sc.camera_path) >= 2
    assert any("country_reveal" in n for n in notes)
    assert "map_recipe" in draft.schema(inp)["properties"]["segments"]["items"]["properties"]
    assert "country_reveal" in draft.prompt(inp)


@pytest.mark.parametrize("extra, msg", [
    ({"map_recipe": {"pattern": "country_reveal", "countries": ["TH"]}}, "drop map_pins"),
    ({"map_recipe": {"pattern": "volcano"}, "map_pins": []}, "segment 1: map_recipe: unknown pattern"),
])
def test_bad_draft_map_recipe_conflicts_and_unknown(recipe_proj, extra, msg):
    from video_engine import draft
    inp = draft.collect(recipe_proj)
    p = plan(**{k: v for k, v in extra.items() if k != "map_pins"})
    if "map_pins" in extra:
        p["segments"][1]["map_pins"] = extra["map_pins"]
    with pytest.raises(ValueError, match=msg):
        draft.assemble(inp, p)


def test_bad_draft_map_recipe_needs_map_bounds(proj):
    from video_engine import draft
    inp = draft.collect(proj)
    p = plan(map_recipe={"pattern": "stat_emphasis", "value": 3})
    del p["segments"][1]["map_pins"]
    with pytest.raises(ValueError, match="no sidecar map_bounds"):
        draft.assemble(inp, p)


def test_good_zoom_capped_by_map_resolution(geo):
    small = recipes.country_reveal(mp([0, 70, 20, 90], w=1500), FR, geojson=geo, countries=["LK"]).scene
    large = recipes.country_reveal(mp([0, 70, 20, 90], w=9000), FR, geojson=geo, countries=["LK"]).scene
    assert small["camera_path"][-1]["zoom"] == 1.0 < large["camera_path"][-1]["zoom"]   # 1500 px cannot zoom at all
    m = mp([0, 70, 20, 90], w=4000)
    z = recipes.country_reveal(m, FR, geojson=geo, countries=["LK"]).scene["camera_path"][-1]["zoom"]
    assert 1.0 < z <= recipes.zoom_cap(m, FR) + 1e-6
    assert m.width / (recipes.cover(m, FR)[0] * z) >= recipes.TEXEL_MIN - 1e-3


@pytest.mark.parametrize("pts", [[(-6.2, 106.8), (3.1, 101.7)], [(13.75, 100.5), (21.0, 105.8)],
                                 [(0, 100), (0, 130)], [(10, 100)]], ids=["pair", "route", "wide", "one"])
@pytest.mark.parametrize("proj", ["equirectangular", "mercator"])
def test_good_map_extent_portrait_with_margin(pts, proj):
    from video_engine import geodata
    b = recipes.map_extent(pts, FR, projection_name=proj)
    w = 4000
    m = recipes.MapInfo("a.jpg", "a", w, geodata.map_height(w, b, proj), b, proj)
    assert m.width / m.height == pytest.approx(W / H * 1.25, rel=0.05)
    for la, lo in pts:
        x, y = projection.to_map(la, lo, b, proj)
        assert 0.15 < x < 0.85 and 0.15 < y < 0.85


def test_good_every_camera_leg_moves_enough():
    """R56: every leg of every builder moves >= 100 px, also when the fitted view is already at zoom 1."""
    m = mp([-11.8, 97.0, 8.7, 111.5], w=5000)
    jk = {"label": "Jakarta", "lat": -6.2, "lng": 106.8, "value": 1}
    kl = {"label": "Kuala Lumpur", "lat": 3.1, "lng": 101.7, "value": 2}
    scenes = [recipes.compare_places(m, FR, a=jk, b=kl, source="x").scene,
              recipes.explain_flow(m, FR, paths=[[(-8, 100), (5, 108)]]).scene,
              recipes.stat_emphasis(m, FR, value=3, place={"lat": -6.2, "lng": 106.8, "label": "Jakarta"}).scene,
              recipes.follow_route(m, FR, points=[(-6.2, 106.8), (3.1, 101.7)], labels=["Jakarta", "KL"]).scene]
    for sc in scenes:
        k = sc["camera_path"]
        for a, b in zip(k, k[1:]):
            assert recipes.leg_motion(m, FR, a, b) >= 100
