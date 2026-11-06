"""3D camera, part 2: parallax layers (gsap_scene_config.layers): schema, Python mirror math, Chrome render, T02,
credits refs and the segment key."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, layout, parallax, timeline
from video_engine.gates import layer1

W, H = 1080, 1920
PAN = [{"at_frame": 0, "focus": [0.4, 0.5], "zoom": 1.5}, {"at_frame": 50, "focus": [0.6, 0.45], "zoom": 2.0}]


def _marker(path, colour=(255, 0, 0, 255)):
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (24, 24), colour), (0, 0))
    im.save(path)


def _proj(project, layers, keys=PAN):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(pins=[], camera_path=keys, layers=layers)
    d, _ = project(mut)
    _marker(d / "assets" / "cloud.png")
    return d, timeline.load(d)


def _scene(d, tl):
    return hyperframes.scene_data(tl.segments[2], tl, d)


def _map_xy(cam, f, m):
    c = layout.camera_at(cam, f)
    return c["x"] + c["s"] * m[0], c["y"] + c["s"] * m[1]


def test_good_depth_zero_layer_is_fixed_to_the_map(project):
    d, tl = _proj(project, [{"asset": "assets/cloud.png", "depth": 0}])
    sc = _scene(d, tl)
    e, cam = sc["layers"][0], sc["camera"]
    st0 = parallax.state(e, cam, 0, 30, W, H)
    c0 = cam["start"]
    m = ((st0["x"] - c0["x"]) / c0["s"], (st0["y"] - c0["y"]) / c0["s"])     # the map point under the grid origin
    for f in (10, 30, 59):
        st = parallax.state(e, cam, f, 30, W, H)
        assert (st["x"], st["y"]) == pytest.approx(_map_xy(cam, f, m), abs=1e-6)
        assert st["w"] == pytest.approx(e["w"] * layout.camera_at(cam, f)["s"] / c0["s"])


@pytest.mark.parametrize("depth, ratio", [(1.0, 2.0), (-0.5, 0.5)])
def test_good_nearer_layers_move_faster_and_farther_slower(project, depth, ratio):
    keys = [{"at_frame": 0, "focus": [0.4, 0.5], "zoom": 2.0}, {"at_frame": 50, "focus": [0.6, 0.5], "zoom": 2.0}]
    d, tl = _proj(project, [{"asset": "assets/cloud.png", "depth": 0}, {"asset": "assets/cloud.png", "depth": depth}], keys)
    sc = _scene(d, tl)
    cam = sc["camera"]
    mv = [parallax.state(e, cam, 40, 30, W, H)["x"] - parallax.state(e, cam, 0, 30, W, H)["x"] for e in sc["layers"]]
    assert abs(mv[0]) > 50 and mv[1] / mv[0] == pytest.approx(ratio, rel=0.05)    # drift zoom keeps it near, not exact


def test_good_drift_adds_wind(project):
    d, tl = _proj(project, [{"asset": "assets/cloud.png", "depth": 0, "drift": [30, 0]},
                            {"asset": "assets/cloud.png", "depth": 0}])
    sc = _scene(d, tl)
    a, b = (parallax.state(e, sc["camera"], 30, 30, W, H) for e in sc["layers"])
    k = layout.camera_at(sc["camera"], 30)["s"] / sc["camera"]["start"]["s"]
    assert a["x"] - b["x"] == pytest.approx(30 * 1.0 * k)


def test_bad_layer_values_are_rejected():
    for bad in ({"asset": "a.gif"}, {"asset": "a.png", "depth": -1}, {"asset": "a.png", "depth": 3},
                {"asset": "a.png", "opacity": 0}, {"asset": "a.png", "drift": [5000, 0]}):
        with pytest.raises(ValidationError):
            timeline.ParallaxLayer(**bad)


def test_bad_missing_layer_asset_fails_t02_and_is_a_visual_ref(project):
    d, tl = _proj(project, [{"asset": "assets/nope.png"}])
    assert any(f.gate == "T02" and "layer asset not found" in f.message for f in layer1.run_layer1(tl, d, []))
    assert any(r[3] == "layer" and r[2] == "assets/nope.png" for r in layer1.visual_refs(tl))


def test_good_segment_key_follows_the_layer_pixels(project):
    d, tl = _proj(project, [{"asset": "assets/cloud.png", "depth": 0.5}])
    k1 = hyperframes.keys(tl.segments[2], tl, d)[0]
    _marker(d / "assets" / "cloud.png", (0, 255, 0, 255))
    assert hyperframes.keys(tl.segments[2], tl, d)[0] != k1


def test_good_chrome_draws_the_layer_grid_where_state_says(project, tmp_path):
    d, tl = _proj(project, [{"asset": "assets/cloud.png", "depth": 1.0, "tile_px": 300}])
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "b")
    f = 30
    hyperframes.probe_dom(out, [f / 30], W, H, snap_dir=tmp_path / "s")
    px = np.asarray(Image.open(tmp_path / "s" / "snap_0.png").convert("RGB"), dtype=int)
    st = parallax.state(sc["layers"][0], sc["camera"], f, 30, W, H)
    red = (px[..., 0] > 150) & (px[..., 1] < 90) & (px[..., 2] < 90)
    ys, xs = np.nonzero(red[400:1500, 100:980])                  # inside the shade's clear middle
    assert len(xs) > 500
    # every red pixel sits in the first 24/200 of a tile from the predicted grid origin
    fx = ((xs + 100 - st["x"]) % st["w"]) / st["w"]
    fy = ((ys + 400 - st["y"]) % st["h"]) / st["h"]
    ok = ((fx < 0.13) | (fx > 0.99)) & ((fy < 0.13) | (fy > 0.99))
    assert ok.mean() > 0.97, ok.mean()
