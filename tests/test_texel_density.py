"""Map sharpness at deep zoom: the deep raster reaches the source width (top-rows-only JPEG decode, half-width
fallback past the decode budget), M02 grades the raster the page really draws, and a tilted camera keeps the camera
scale in Chrome's raster (no layer rasterized at scale 1 and upscaled)."""
import numpy as np
import pytest
from PIL import Image

from video_engine import hyperframes, maps, timeline
from video_engine.gates import layer2

W, H = 1080, 1920


def _scene(s=20.0, y=0.0):
    return {"map_size": [1000.0, 1000.0], "width": 216, "height": 384, "n_frames": 1,
            "camera": {"start": {"x": 0, "y": y, "s": s}, "end": {"x": 0, "y": y, "s": s}, "legs": []}}


def test_good_top_rows_decode_is_pixel_exact(tmp_path):
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 256, (300, 400, 3), dtype=np.uint8)).save(tmp_path / "m.jpg", quality=90)
    full = np.asarray(Image.open(tmp_path / "m.jpg"), dtype=int)
    im = Image.open(tmp_path / "m.jpg")
    maps._load_top_rows(im, 117)
    assert im.size == (400, 117) and np.array_equal(np.asarray(im, dtype=int), full[:117])
    from PIL import ImageFile
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False                 # the flag is restored


def test_bad_rows_past_the_decode_budget_fall_back_to_half_width(monkeypatch):
    sc = _scene(y=-19000.0)                                          # the view sits near the bottom of the map
    assert maps.deep_plan(sc, (20000, 20000))["w"] == 20000
    monkeypatch.setattr(maps, "DEEP_DECODE_MAX_PX", 20000 * 1000)
    assert maps.deep_plan(sc, (20000, 20000))["w"] == 10000   # JPEG draft 1/2 decode instead


def test_good_drawn_width_is_the_deep_raster_or_the_derivative():
    assert maps.drawn_width(_scene(), (20000, 20000)) == 20000
    assert maps.drawn_width(_scene(s=1.0), (20000, 20000)) == 1000   # the derivative suffices


def _deep_proj(project, zoom=4.0, keys=None, side=3000):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(map_asset="assets/dm.jpg", pins=[], camera_path=keys or [
            {"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.0}, {"at_frame": 20, "focus": [0.4, 0.4], "zoom": zoom}])
        d["credits_manifest"][2]["path"] = "assets/dm.jpg"
    d, _ = project(mut)
    rng = np.random.default_rng(5)
    Image.fromarray(rng.integers(0, 256, (300, 300, 3), dtype=np.uint8)).resize((side, side), Image.NEAREST).save(
        d / "assets" / "dm.jpg", quality=95)
    return d, timeline.load(d)


def test_bad_m02_flags_a_raster_capped_below_the_zoom(project, monkeypatch):
    d, tl = _deep_proj(project, side=10000)                           # needs about 7800 px: the source covers it
    ok = layer2.m02_map_resolution(tl, d)
    assert [f.level for f in ok] == ["pass"]
    monkeypatch.setattr(maps, "DEEP_MAX_SIDE", 4500)          # the old kind of cap: source 10000, drawn 4500
    hyperframes.clear_scene_cache()
    fs = layer2.m02_map_resolution(tl, d)
    assert fs[0].level in ("warn", "fail") and "raster" in fs[0].message and "texels per screen px" in fs[0].message
    assert [f.level for f in ok] != [f.level for f in fs]


def _sharpness(d, tl, tmp_path, name):
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / name)
    hyperframes.probe_dom(out, [18 / sc["fps"]], W, H, snap_dir=tmp_path / (name + "_s"))
    a = np.asarray(Image.open(tmp_path / (name + "_s") / "snap_0.png").convert("L"), dtype=float)[860:1060, 440:640]
    return float(np.abs(4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]).mean())


def test_good_tilted_camera_is_as_sharp_as_the_flat_one_in_chrome(project, tmp_path):
    keys = lambda t: [{"at_frame": 0, "focus": [0.45, 0.45], "zoom": 1.5, "tilt": t},
                      {"at_frame": 20, "focus": [0.45, 0.46], "zoom": 4.0, "tilt": t + 4 if t else 0}]
    d, tl = _deep_proj(project, keys=keys(0), side=10000)
    flat = _sharpness(d, tl, tmp_path, "flat")
    d2, tl2 = _deep_proj(project, keys=keys(30), side=10000)
    tilt = _sharpness(d2, tl2, tmp_path, "tilt")
    assert tilt > 0.75 * flat, (tilt, flat)   # sanity: tilt keeps detail (the scale-1 blur itself needs the full demo scene)


def test_good_tilted_template_keeps_the_camera_layer_flat(project, tmp_path):
    keys = [{"at_frame": 0, "focus": [0.45, 0.45], "zoom": 3.6, "tilt": 30},
            {"at_frame": 20, "focus": [0.45, 0.46], "zoom": 4.0, "tilt": 30}]
    d, tl = _deep_proj(project, keys=keys)
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "b")
    html = (out / "index.html").read_text()
    assert "force3D:false" in html and "data-flat-raster" in html


@pytest.mark.parametrize("zoom", [2.0])
def test_good_untilted_scene_keeps_its_layers(project, tmp_path, zoom):
    d, tl = _deep_proj(project, zoom=zoom)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    assert "tilt" not in sc["camera"]
