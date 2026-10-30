"""3D camera, part 3: Blue Marble globe (gsap_scene_config.globe): schema, camera math, textures and detail patch,
Chrome render against the Python projection, pins, gates M04, R56 and P01."""
import math

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import globe, hyperframes, timeline
from video_engine.gates import layer1, layer2, style

W, H = 1080, 1920
KEYS = [{"at_frame": 0, "lat": 20, "lng": 30, "altitude": 3.0}, {"at_frame": 40, "lat": 40, "lng": 50, "altitude": 0.8, "tilt": 20}]
PINS = [{"label": "Near", "lat": 40, "lng": 50}, {"label": "Far", "lat": -40, "lng": -130}]
FORBIDDEN = ("camera_path", "beats", "regions", "routes", "props", "layers", "map_bounds")
SIAM = [{"f": 0, "lat": 20, "lng": 75, "alt": 5.0, "tilt": 0},
        {"f": 80, "lat": 17.5, "lng": 89, "alt": 3.0, "tilt": 4},
        {"f": 150, "lat": 15, "lng": 100.5, "alt": 1.7, "tilt": 10},
        {"f": 330, "lat": 13, "lng": 100.8, "alt": 1.3, "tilt": 25}]


def _camera(keys=SIAM, n=331):
    return {"keys": [k.copy() for k in keys], "n": n, "start": 0.75, "end": 1.0, "drift": 0.06, "alt_min": 0.005}


def _noise(path, w=2048, h=1024, block=16, seed=3):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h // block, w // block), dtype=np.uint8).repeat(block, 0).repeat(block, 1)
    Image.fromarray(a).convert("RGB").save(path, quality=95)


def _globe_proj(project, keys=KEYS, pins=PINS, extra=None, mapsize=(2048, 1024), block=16):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        for k in FORBIDDEN:
            sc.pop(k, None)
        sc.update(map_asset="assets/globe.jpg", pins=pins, globe={"keys": keys, **(extra or {})})
    d, _ = project()
    (d / "assets" / "globe.jpg").unlink(missing_ok=True)
    _noise(d / "assets" / "globe.jpg", *mapsize, block=block)
    d, tl = project(mut)
    return d, tl


def _scene(d, tl):
    return hyperframes.scene_data(tl.segments[2], tl, d)


# math -----------------------------------------------------------------------------------------------------------------
def test_good_view_centre_projects_to_the_frame_centre_and_far_side_is_hidden():
    v = {"lat": 10, "lng": -60, "alt": 1.5, "tilt": 0}
    assert globe.project(v, 10, -60, W, H) == pytest.approx((W / 2, H / 2), abs=1e-6)
    assert globe.project(v, -10, 120, W, H) is None
    x, y = globe.project(v, 15, -60, W, H)          # north is up
    assert y < H / 2 and x == pytest.approx(W / 2, abs=1e-6)
    x, _ = globe.project(v, 10, -55, W, H)          # east is right
    assert x > W / 2


def test_good_ray_hit_inverts_project_under_tilt():
    v = {"lat": 35, "lng": 20, "alt": 0.6, "tilt": 30}
    for lat, lng in ((35, 20), (37, 22), (33, 17)):
        x, y = globe.project(v, lat, lng, W, H)
        assert globe.ray_hit(v, x, y, W, H) == pytest.approx((lat, lng), abs=1e-6)


def test_good_camera_eases_between_keys_and_drifts_after_the_last():
    G = {"keys": [{"f": k["at_frame"], "lat": k["lat"], "lng": k["lng"], "alt": k["altitude"], "tilt": k.get("tilt", 0)}
                  for k in KEYS], "drift": 0.1, "alt_min": 0.005, "start": 0.75, "end": 1.0}
    a, m, b = (globe.camera_at(G, f, 30) for f in (0, 20, 40))
    assert (a["lat"], a["alt"]) == pytest.approx((20, 3.0)) and (b["lat"], b["alt"], b["tilt"]) == pytest.approx((40, 0.8, 20))
    assert 0.8 < m["alt"] < 3.0 and 20 < m["lat"] < 40
    assert globe.camera_at(G, 70, 30)["alt"] == pytest.approx(0.8 * math.exp(-0.1))


def test_good_globe_speed_is_continuous_through_intermediate_keys():
    G = _camera()
    speeds = globe.motion(G, 331, 30, W, H)
    for k in G["keys"]:
        v = globe.camera_at(G, k["f"], 30)
        assert v == pytest.approx({c: k[c] for c in ("lat", "lng", "alt", "tilt")}, abs=1e-10)
    for c in (0, 1):
        s = [v[c] for v in speeds]
        for k in (80, 150):
            before, after = sum(s[k-10:k-2]) / 8, sum(s[k+3:k+11]) / 8
            assert min(s[k-1:k+2]) >= 0.7 * min(before, after)
    zoom = [v[0] for v in speeds[:330]]
    peaks = [0] + [i for i in range(1, 329) if zoom[i] >= zoom[i-1] and zoom[i] > zoom[i+1]] + [329]
    for i in range(1, 329):
        if zoom[i] < zoom[i-1] and zoom[i] < zoom[i+1]:
            left, right = max(p for p in peaks if p < i), min(p for p in peaks if p > i)
            assert zoom[i] >= 0.7 * min(zoom[left], zoom[right])


def test_good_globe_key_hold_stops_the_camera():
    G = _camera()
    G["keys"][1]["hold"] = 10
    v = globe.camera_at(G, 80, 30)
    assert all(globe.camera_at(G, f, 30) == pytest.approx(v, abs=1e-9) for f in range(80, 91))
    assert globe.camera_at(G, 85, 30) == pytest.approx({c: G["keys"][1][c] for c in ("lat", "lng", "alt", "tilt")})
    zoom = [v[0] for v in globe.motion(G, 331, 30, W, H)]
    assert max(zoom[80:90]) < 1e-9
    assert max(zoom[78], zoom[91]) < 0.1 * max(zoom)
    with pytest.raises(ValidationError, match="hold"):
        timeline.Globe(keys=[{**KEYS[0], "hold": 40}, KEYS[1]])


def test_good_globe_start_moves_and_ends_without_hold():
    G = _camera()
    zoom = [v[0] for v in globe.motion(G, 331, 30, W, H)]
    assert zoom[0] >= 0.25 * max(zoom)
    assert min(zoom[320:330]) >= 0.9 * sum(zoom[150:330]) / 180


def test_good_globe_wraps_longitude_and_flows_into_drift():
    keys = [{"f": 0, "lat": 0, "lng": 170, "alt": 2, "tilt": 0},
            {"f": 30, "lat": 10, "lng": -170, "alt": 1, "tilt": 5}]
    G = _camera(keys, 60)
    assert globe.camera_at(G, 15, 30)["lng"] == pytest.approx(-180, abs=2)
    before, at, after = (math.log(globe.camera_at(G, f, 30)["alt"]) for f in (29.999, 30, 30.001))
    assert (at - before) / 0.001 == pytest.approx((after - at) / 0.001, rel=0.001)
    G["keys"][-1]["hold"] = 10
    assert globe.camera_at(G, 40, 30)["alt"] == pytest.approx(1)
    assert globe.camera_at(G, 70, 30)["alt"] == pytest.approx(math.exp(-0.06))


@pytest.mark.parametrize("bad", [{"camera_path": [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1}]},
                                 {"map_bounds": [-90, -180, 90, 180]}])
def test_bad_globe_with_flat_map_fields_is_rejected(project, bad):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        for k in FORBIDDEN:
            sc.pop(k, None)
        sc.update(globe={"keys": KEYS}, pins=[], **bad)
    with pytest.raises(Exception, match="globe"):
        project(mut)


@pytest.mark.parametrize("keys", [[{"at_frame": 5, "lat": 0, "lng": 0, "altitude": 1}],
                                  [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 1}] * 2,
                                  [{"at_frame": 0, "lat": 95, "lng": 0, "altitude": 1}],
                                  [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 50}],
                                  [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 1, "tilt": 80}]])
def test_bad_globe_keys_are_rejected(keys):
    with pytest.raises((ValidationError, ValueError)):
        timeline.Globe(keys=keys)


def test_bad_non_2_to_1_map_is_rejected(project):
    d, tl = _globe_proj(project, mapsize=(1024, 1024))
    with pytest.raises(Exception, match="2:1"):
        _scene(d, tl)


# scene, textures --------------------------------------------------------------------------------------------------------
def test_good_scene_has_identity_camera_projected_pins_and_texture(project):
    d, tl = _globe_proj(project)
    sc = _scene(d, tl)
    assert sc["camera"]["start"] == {"x": 0.0, "y": 0.0, "s": 1.0} and sc["map_size"] == [W, H]
    assert "deep" not in sc and sc["globe"]["tex"].startswith("assets/globe_")
    near, far = sc["pins"]
    assert 0 <= near["px"] <= W and 0 <= near["py"] <= H and far["px"] == -1000.0
    tex = globe.texture(d / "assets" / "globe.jpg")
    assert Image.open(tex).size == (globe.TEX_W, globe.TEX_W // 2)


def test_bad_source_no_sharper_than_the_texture_gets_no_patch(project):
    d, tl = _globe_proj(project, keys=[{"at_frame": 0, "lat": 10, "lng": 10, "altitude": 0.02}], pins=[])
    assert "patches" not in _scene(d, tl)["globe"]


def test_good_deep_views_get_a_detail_patch_cut_at_the_right_place(project):
    keys = [{"at_frame": 0, "lat": 10, "lng": 10, "altitude": 0.05}]
    d, tl = _globe_proj(project, keys=keys, pins=[], mapsize=(16384, 8192), block=16)
    G = _scene(d, tl)["globe"]
    assert len(G["patches"]) == 1
    pp = G["patches"][0]
    assert pp["west"] < 10 < pp["east"] and pp["south"] < 10 < pp["north"] and pp["east"] - pp["west"] < 5
    assert pp["src"] == "map" and pp["have"] == pytest.approx(16384 / 360, rel=0.01) and pp["w"] < pp["h"] * 2
    img = np.asarray(Image.open(globe.patch(pp, d / "assets" / "globe.jpg", None)).convert("L"), dtype=float)
    src = np.asarray(Image.open(d / "assets" / "globe.jpg").convert("L"), dtype=float)
    ys, xs = np.mgrid[5:img.shape[0] - 5:7, 5:img.shape[1] - 5:7]
    lng = pp["west"] + xs / img.shape[1] * (pp["east"] - pp["west"])
    lat = pp["north"] - ys / img.shape[0] * (pp["north"] - pp["south"])
    want = src[((90 - lat) / 180 * src.shape[0]).astype(int), ((lng + 180) / 360 * src.shape[1]).astype(int)]
    assert img[ys, xs].std() > 20
    assert np.corrcoef(img[ys, xs].ravel(), want.ravel())[0, 1] > 0.9


def test_good_a_zoom_from_space_gets_finer_patches_over_smaller_areas():
    G = {"keys": [{"f": 0, "lat": 0, "lng": 20, "alt": 3.0, "tilt": 0}, {"f": 90, "lat": 41, "lng": 50, "alt": 0.01, "tilt": 25}],
         "drift": 0.0, "alt_min": 0.005, "start": 0.75, "end": 1.0, "tex_ppd": globe.TEX_W / 360}
    ps = globe.patch_plan(G, 120, 30, W, H, 21600, True)
    assert [p["level"] for p in ps] == sorted(p["level"] for p in ps) and len(ps) >= 4
    assert all(p["w"] <= globe.PATCH_MAX and p["h"] <= globe.PATCH_MAX for p in ps)
    size = [(p["east"] - p["west"]) * (p["north"] - p["south"]) for p in ps]
    assert size[-1] < size[0] / 10 and len(ps) <= 16 and ps[-1]["have"] == pytest.approx(globe.TILE_PX_PER_DEG, rel=0.3)
    G["patches"] = ps
    worst = min(globe.have_at(G, v, W, H) / min(globe.TILE_PX_PER_DEG, globe.need_px_per_deg(v, H))
                for v in (globe.camera_at(G, f, 30) for f in range(121)))
    assert worst > 0.5, worst                        # as sharp as the source allows, within 2x


def test_good_detail_tiles_are_read_across_tile_borders(tmp_path):
    tiles = tmp_path / "t"
    tiles.mkdir()
    for i, (c, r) in enumerate(globe.TILES):          # each tile one flat grey: the patch shows which tile it read
        Image.new("RGB", (90, 90), (20 + 25 * i,) * 3).save(tiles / f"x.{c}{r}.jpg")
    pp = {"west": -95.0, "east": -85.0, "south": -5.0, "north": 5.0, "w": 100, "h": 100, "src": "tiles"}
    a = np.asarray(Image.open(globe.patch(pp, tmp_path / "unused.jpg", tiles)).convert("L"), dtype=int)
    order = list(globe.TILES)
    exp = {q: 20 + 25 * order.index(t) for q, t in {"nw": ("A", 1), "ne": ("B", 1), "sw": ("A", 2), "se": ("B", 2)}.items()}
    got = {"nw": a[20, 20], "ne": a[20, 80], "sw": a[80, 20], "se": a[80, 80]}
    assert all(abs(got[q] - exp[q]) <= 3 for q in exp), (got, exp)


def test_bad_incomplete_detail_tiles_fail_t02(project, tmp_path):
    d, tl = _globe_proj(project, extra={"detail_tiles": "tiles"})
    (d / "tiles").mkdir()
    Image.new("RGB", (8, 8)).save(d / "tiles" / "x.A1.jpg")
    msgs = [f.message for f in layer1.t02_integrity(tl, d) if f.level == "fail"]
    assert any("A2.jpg (found 0)" in m for m in msgs), msgs


# Chrome ---------------------------------------------------------------------------------------------------------------
def test_good_chrome_draws_the_globe_and_pins_where_the_projection_says(project, tmp_path):
    pins = PINS + [{"label": "West", "lat": 25, "lng": 35}, {"label": "East", "lat": 35, "lng": 45}]
    d, tl = _globe_proj(project, pins=pins)
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "b")
    f = 25
    frames = [f, 10.5, 30, 39.5, 50.25]
    dom = hyperframes.probe_dom(out, [f / 30 for f in frames], W, H, snap_dir=tmp_path / "s")
    shot = np.asarray(Image.open(tmp_path / "s" / "snap_0.png").convert("L"), dtype=float)
    src = np.asarray(Image.open(d / "assets" / "globe.jpg").convert("L"), dtype=float)
    v = globe.camera_at(sc["globe"], f, 30)

    def corr(flip):
        got, want = [], []
        for sy in range(300, 1700, 41):
            for sx in range(150, 930, 41):
                if (h := globe.ray_hit(v, sx, sy, W, H)) is None:
                    continue
                lat, lng = h
                lng = -lng if flip else lng
                got.append(shot[sy, sx])
                want.append(src[min(1023, int((90 - lat) / 180 * 1024)), min(2047, int((lng + 180) / 360 * 2048))])
        return np.corrcoef(got, want)[0, 1]
    good, bad = corr(False), corr(True)
    assert good > 0.8 and bad < 0.3, (good, bad)
    rects = {r["id"]: r for r in dom["times"][0]["rects"]}
    x, y = globe.project(v, 40, 50, W, H)
    assert (rects["anchor:pin:0"]["x"], rects["anchor:pin:0"]["y"]) == pytest.approx((x, y), abs=2)
    assert rects["anchor:pin:1"]["opacity"] == 0 and not dom.get("errors")
    G = sc["globe"]
    for frame, values in enumerate(G["views"]):
        view = globe.camera_at(G, frame, 30)
        assert values == pytest.approx([view[c] for c in ("lat", "lng", "alt", "tilt")], abs=1e-6)
    for frame, item in zip(frames, dom["times"]):
        v = globe.camera_at(G, round(frame / 30, 4) * 30, 30)
        rects = {r["id"]: r for r in item["rects"]}
        for i in (0, 2, 3):
            p = pins[i]
            xy = globe.project(v, p["lat"], p["lng"], W, H)
            assert xy is not None
            assert (rects[f"anchor:pin:{i}"]["x"], rects[f"anchor:pin:{i}"]["y"]) == pytest.approx(xy, abs=1.5)


# gates ----------------------------------------------------------------------------------------------------------------
def test_good_m04_passes_a_high_view_and_bad_fails_a_deep_one_without_detail(project):
    d, tl = _globe_proj(project)
    f = layer2.m04_globe_resolution(tl, d)
    assert f[0].level == "fail", f[0].message                   # 2048 px map at altitude 0.8 and drifting in
    keys = [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 6.0}, {"at_frame": 40, "lat": 0, "lng": 40, "altitude": 5.0}]
    d, tl = _globe_proj(project, keys=keys, pins=[], mapsize=(8192, 4096))
    assert [x.level for x in layer2.m04_globe_resolution(tl, d)] == ["pass"]


def test_good_r56_and_p01_measure_globe_legs(project):
    d, tl = _globe_proj(project)
    r = [x for x in layer2.r56_camera(tl, d)[0] if x.seg == 2]
    assert r and r[0].level == "pass" and "globe" in r[0].message
    mf = style.motion_frames(tl, d)
    s = tl.segments[2]
    seg = mf[s.start_frame + 5:s.start_frame + 35]
    assert all(m is not None and (m[0] > 0 or m[1] > 0) for m in seg)
    keys = [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 2.0}, {"at_frame": 40, "lat": 0, "lng": 0.01, "altitude": 2.0}]
    d, tl = _globe_proj(project, keys=keys, pins=[])
    r = [x for x in layer2.r56_camera(tl, d)[0] if x.seg == 2]
    assert r[0].level == "fail"
