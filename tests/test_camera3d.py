"""3D camera, part 1: perspective tilt of HyperFrames map cameras (camera key `tilt`, camera3d.py): projection math,
leg planning, Chrome render against the projection, map props under tilt, M02 magnification, M03 cover gate, P01."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import camera3d, hyperframes, layout, maps, props, timeline
from video_engine.gates import layer2, style

W, H = 1080, 1920


def _noise(path, size=(3000, 3000), seed=5):
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 256, (size[1] // 10, size[0] // 10, 3), dtype=np.uint8)).resize(
        (size[0], size[1]), Image.NEAREST).save(path, quality=95)


def _tilt_proj(project, keys, noise=True):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(map_asset="assets/tiltmap.jpg", pins=[], camera_path=keys)
        d["credits_manifest"][2]["path"] = "assets/tiltmap.jpg"
    d, _ = project(mut)
    _noise(d / "assets" / "tiltmap.jpg")
    return d, timeline.load(d)


TILTED = [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 2.0}, {"at_frame": 20, "focus": [0.5, 0.55], "zoom": 2.5, "tilt": 30}]


# math ----------------------------------------------------------------------------------------------------------------
def test_good_projection_round_trips_and_is_identity_at_zero_tilt():
    for t in (0.0, 20.0, 45.0):
        for q in ((0, 0), (W, 300), (540, 960), (100, H)):
            sx, sy, k = camera3d.project(*q, t, W, H)
            assert camera3d.unproject(sx, sy, t, W, H) == pytest.approx(q, abs=1e-6)
    assert camera3d.project(10, 20, 0.0, W, H) == pytest.approx((10, 20, 1.0))
    assert camera3d.project(540, 0, 30, W, H)[2] < 1 < camera3d.project(540, H, 30, W, H)[2]   # top recedes
    assert camera3d.max_mag(30, W, H) > 1.0 and camera3d.max_mag(0, W, H) == pytest.approx(1.0)


def test_bad_screen_point_past_the_horizon_does_not_unproject():
    assert camera3d.unproject(540, 0, 60, W, H) is not None
    assert camera3d.unproject(540, -3000, 60, W, H) is None


def test_bad_tilt_outside_0_to_60_is_rejected():
    for t in (-1, timeline.TILT_MAX + 1):
        with pytest.raises(ValidationError):
            timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], tilt=t)


# plan ----------------------------------------------------------------------------------------------------------------
def test_good_legs_carry_the_key_tilts_and_camera_at_eases_them(project):
    d, tl = _tilt_proj(project, TILTED)
    cam = hyperframes.scene_data(tl.segments[2], tl, d)["camera"]
    assert cam["start"]["t"] == 0 and cam["tilt"]["p"] == camera3d.perspective_px(H)
    assert layout.camera_at(cam, 20)["t"] == pytest.approx(30) and layout.camera_at(cam, 200)["t"] == pytest.approx(30)
    assert 0 < layout.camera_at(cam, 10)["t"] < 30


def test_good_untilted_scene_has_no_tilt_values(project):
    d, tl = _tilt_proj(project, [{**k, "tilt": 0} for k in TILTED])
    cam = hyperframes.scene_data(tl.segments[2], tl, d)["camera"]
    assert "tilt" not in cam and "t" not in cam["start"] and all("t" not in L["to"] for L in cam["legs"])


# Chrome --------------------------------------------------------------------------------------------------------------
def _corr(d, tl, tmp_path, f, use_tilt):
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "b")
    hyperframes.probe_dom(out, [f / sc["fps"]], W, H, snap_dir=tmp_path / "s")
    shot = np.asarray(Image.open(tmp_path / "s" / "snap_0.png").convert("L"), dtype=float)
    c = layout.camera_at(sc["camera"], f)
    src = np.asarray(Image.open(d / "assets" / "tiltmap.jpg").convert("L"), dtype=float)
    k = src.shape[1] / sc["map_size"][0]
    got, want = [], []
    for sy in range(300, 1700, 37):            # rows above and below the pivot, avoiding the shade edges
        for sx in range(200, 880, 37):
            q = camera3d.unproject(sx, sy, c["t"] if use_tilt else 0.0, W, H)
            mx, my = (q[0] - c["x"]) / c["s"] * k, (q[1] - c["y"]) / c["s"] * k
            got.append(shot[sy, sx])
            want.append(src[int(my), int(mx)])
    return np.corrcoef(got, want)[0, 1]


def test_good_chrome_draws_the_tilted_map_where_the_projection_says(project, tmp_path):
    d, tl = _tilt_proj(project, TILTED)
    r = _corr(d, tl, tmp_path, 40, True)
    bad = _corr(d, tl, tmp_path, 40, False)          # the same frame read as a flat camera
    assert r > 0.8 and bad < 0.3, (r, bad)


def test_good_map_prop_box_under_tilt_is_the_projected_flat_box(project):
    keys = TILTED
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(map_asset="assets/tiltmap.jpg", pins=[], camera_path=keys,
                  props=[{"asset": "assets/prop.png", "x": 0.5, "y": 0.45, "width_px": 200}])
        d["credits_manifest"][2]["path"] = "assets/tiltmap.jpg"
    d, _ = project(mut)
    _noise(d / "assets" / "tiltmap.jpg")
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(d / "assets" / "prop.png")
    tl = timeline.load(d)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    e = sc["props"][0]
    st = props.state(e, sc["camera"], 40, W, H)
    flat = {k: v for k, v in st.items() if k != "tilt"}
    b, fb = props.box(e, st), props.box(e, flat)
    assert st["tilt"][0] == pytest.approx(30)
    assert (b[3] - b[1]) < (fb[3] - fb[1])                   # foreshortened
    x0, y0, _ = camera3d.project(fb[0], fb[1], 30, W, H)
    assert (b[0], b[1]) == pytest.approx((min(x0, camera3d.project(fb[0], fb[3], 30, W, H)[0]), y0))


# gates ---------------------------------------------------------------------------------------------------------------
def test_good_m02_counts_the_tilt_magnification(project):
    d, tl = _tilt_proj(project, TILTED)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    top = max(L["to"]["s"] for L in sc["camera"]["legs"] if L["to"]["t"] == 30)       # the tilted views (drift included)
    assert top >= 2.5 * sc["camera"]["start"]["s"] / 2.0 - 1e-6
    assert maps._need_w(sc) == pytest.approx(sc["map_size"][0] * top * camera3d.max_mag(30, W, H), rel=1e-6)


def test_good_m03_passes_a_tilt_with_map_under_every_corner(project):
    d, tl = _tilt_proj(project, TILTED)
    f = layer2.m03_tilt_cover(tl, d)
    assert [x.level for x in f] == ["pass"] and "1 tilted" in f[0].message


def test_bad_m03_fails_a_tilt_that_shows_past_the_map_edge(project):
    d, tl = _tilt_proj(project, [TILTED[0], {"at_frame": 20, "focus": [0.5, 0.2], "zoom": 1.0, "tilt": 45}])
    f = layer2.m03_tilt_cover(tl, d)
    assert f[0].level == "fail" and "top-left" in f[0].message


def test_good_p01_counts_a_tilt_only_move_as_motion(project):
    keys = [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 2.0}, {"at_frame": 50, "focus": [0.5, 0.5], "zoom": 2.0, "tilt": 30}]
    d, tl = _tilt_proj(project, keys)
    mf = style.motion_frames(tl, d)[tl.segments[2].start_frame + 25]
    assert mf[1] > 0.05
