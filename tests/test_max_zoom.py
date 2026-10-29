"""[camera] max_zoom: profile key (default 4, Geoverum 16), camera key validation against it, layout fit and draft
clamp, deep map tiles past the whole-map derivative (plan, pixels, cache, Chrome render) and the M02 map resolution
gate."""
import json

import numpy as np
import pytest
from PIL import Image, ImageFilter
from pydantic import ValidationError

from video_engine import draft, hyperframes, layout, profiles, timeline
from video_engine.gates import layer2
from video_engine.util import EngineError


def _deep_profile(monkeypatch, mz=16.0):
    real = profiles.load

    def load(name=None):
        p = real(name)
        data = p.data.model_copy(update={"camera": p.data.camera.model_copy(update={"max_zoom": mz})})
        return profiles.Profile(p.name, data, p.blake3 + f"-mz{mz}")
    monkeypatch.setattr(profiles, "load", load)


def _noise_map(path, size=(6000, 6000), seed=3):
    rng = np.random.default_rng(seed)
    px = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)       # detail at the source's own pixel scale
    im = Image.fromarray(px).filter(ImageFilter.GaussianBlur(0.8))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=95)
    return path


def _deep_proj(project, zoom=16.0, size=(6000, 6000), focus=(0.31, 0.33)):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(map_asset="assets/deepmap.jpg", pins=[], camera_path=[
            {"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.0},
            {"at_frame": 20, "focus": list(focus), "zoom": zoom}])
        d["credits_manifest"][2]["path"] = "assets/deepmap.jpg"
    d, _ = project(mut)
    _noise_map(d / "assets" / "deepmap.jpg", size)
    return d, timeline.load(d)


# profile and validation ----------------------------------------------------------------------------------------------
def test_good_default_max_zoom_is_4_and_geoverum_16():
    assert profiles.load().camera.max_zoom == timeline.ZOOM_MAX == 4.0
    assert profiles.load("geoverum").camera.max_zoom == 16.0


def test_good_key_up_to_the_profile_max_zoom_loads(project, monkeypatch):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project, zoom=16.0)
    assert tl.segments[2].gsap_scene_config.camera_path[1].zoom == 16.0


def test_bad_key_beyond_the_profile_max_zoom_is_rejected(project):
    with pytest.raises(ValidationError, match="max_zoom 4"):
        _deep_proj(project, zoom=4.5)
    with pytest.raises(ValidationError):
        timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], zoom=timeline.ZOOM_LIMIT + 1)
    with pytest.raises(ValidationError):
        profiles.CameraProfile(max_zoom=0.5)


def test_good_draft_map_path_clamps_to_max_zoom():
    keys = [{"at_s": 0, "x": 0.5, "y": 0.5, "zoom": 1}, {"at_s": 1, "x": 0.3, "y": 0.3, "zoom": 30}]
    assert draft._camera_path(keys, 60, 30)[1]["zoom"] == 4.0
    assert draft._camera_path(keys, 60, 30, max_zoom=16.0)[1]["zoom"] == 16.0


def test_good_layout_fit_follows_max_zoom(project, monkeypatch):
    from test_layout import _planner
    def lone(d):
        d["segments"][2]["gsap_scene_config"]["beats"] = [{"at_frame": 0, "pins": [0]}]
    P = _planner(project, mut=lone)
    assert P.max_zoom == profiles.of(P.tl).camera.max_zoom
    P.max_zoom = 4.0
    _, a = P.fit(P.sc.beats[0])
    P.max_zoom = 16.0
    _, b = P.fit(P.sc.beats[0])
    assert a <= 4.0 + 1e-6 and b > a * 2


# deep tiles ----------------------------------------------------------------------------------------------------------
def test_bad_no_deep_tiles_when_the_derivative_suffices(project):
    d, tl = _deep_proj(project, zoom=1.5)
    assert "deep" not in hyperframes.scene_data(tl.segments[2], tl, d)


def test_good_deep_tiles_cover_only_the_deep_viewports(project, monkeypatch):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    deep = sc["deep"]
    dw, dh = deep[0]["raster"]
    assert dw == 6000 and 0 < len(deep) < (6000 // hyperframes.DEEP_TILE + 1) ** 2 / 2
    mw = sc["map_size"][0]
    c = layout.camera_at(sc["camera"], sc["n_frames"] - 1)
    cx, cy = (540 - c["x"]) / c["s"] * dw / mw, (960 - c["y"]) / c["s"] * dh / sc["map_size"][1]
    assert any(b[0] <= cx < b[2] and b[1] <= cy < b[3] for b in (e["box"] for e in deep))   # the final view is covered
    for e in deep:
        b = e["box"]
        assert b[2] - b[0] <= hyperframes.DEEP_TILE + hyperframes.DEEP_PAD and b[2] <= dw and b[3] <= dh


def test_good_deep_tile_pixels_are_the_source_region_and_cached(project, monkeypatch):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project)
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    files = hyperframes.deep_tiles(d / "assets" / "deepmap.jpg", sc)
    e = sc["deep"][0]
    got = np.asarray(Image.open(files[e["img"].split("/")[1]]).convert("RGB"), dtype=float)
    want = np.asarray(Image.open(d / "assets" / "deepmap.jpg").convert("RGB").crop(tuple(e["box"])), dtype=float)
    off = np.asarray(Image.open(d / "assets" / "deepmap.jpg").convert("RGB").crop(tuple(v + 3 for v in e["box"])), dtype=float)
    assert got.shape == want.shape and np.abs(got - want).mean() < 0.4 * np.abs(got - off).mean()   # JPEG noise only
    monkeypatch.setattr(hyperframes.Image if hasattr(hyperframes, "Image") else Image, "open",
                        lambda *a, **k: pytest.fail("cached tiles must not decode the source"))
    assert hyperframes.deep_tiles(d / "assets" / "deepmap.jpg", sc) == files


def test_good_segment_key_follows_the_deep_tiles(project, monkeypatch):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project)
    k0 = hyperframes.keys(tl.segments[2], tl, d)[0]
    _noise_map(d / "assets" / "deepmap.jpg", seed=4)
    assert hyperframes.keys(tl.segments[2], tl, d)[0] != k0


def _snap_corr(d, tl, tmp_path, name):
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / name)
    f = sc["n_frames"] - 1
    hyperframes.probe_dom(out, [f / sc["fps"]], 1080, 1920, snap_dir=tmp_path / (name + "_snap"))
    shot = np.asarray(Image.open(tmp_path / (name + "_snap") / "snap_0.png").convert("RGB"), dtype=float)
    c = layout.camera_at(sc["camera"], f)
    src = Image.open(d / "assets" / "deepmap.jpg").convert("RGB")
    k = src.width / sc["map_size"][0]
    X0, Y0, X1, Y1 = 340, 760, 740, 1160          # a 400 px square at the frame centre
    box = tuple(v * k for v in ((X0 - c["x"]) / c["s"], (Y0 - c["y"]) / c["s"], (X1 - c["x"]) / c["s"], (Y1 - c["y"]) / c["s"]))
    want = np.asarray(src.resize((400, 400), Image.BICUBIC, box=box).convert("L"), dtype=float)
    got = shot[Y0:Y1, X0:X1] @ [0.299, 0.587, 0.114]
    return np.corrcoef(got.ravel(), want.ravel())[0, 1], sc        # map filters change contrast, not the pattern


def test_good_chrome_shows_the_deep_tiles_closer_to_the_source_than_the_derivative(project, monkeypatch, tmp_path):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project)
    deep_r, sc = _snap_corr(d, tl, tmp_path, "deep")
    assert sc.get("deep")
    monkeypatch.setattr(hyperframes, "deep_plan", lambda *a, **k: None)
    flat_r, sc2 = _snap_corr(d, tl, tmp_path, "flat")
    assert "deep" not in sc2
    assert deep_r > 0.9 and deep_r > flat_r + 0.1, (deep_r, flat_r)


# M02 -----------------------------------------------------------------------------------------------------------------
def test_good_m02_passes_a_map_covering_its_deepest_zoom(project):
    d, tl = _deep_proj(project, zoom=2.0)
    assert [f.level for f in layer2.m02_map_resolution(tl, d)] == ["pass"]


@pytest.mark.parametrize("zoom, level", [(4.0, "warn"), (16.0, "fail")])   # source 6000 px, need 1920 px x zoom
def test_bad_m02_warns_upscaled_and_fails_beyond_2x(project, monkeypatch, zoom, level):
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project, zoom=zoom)
    fs = layer2.m02_map_resolution(tl, d)
    assert [f.level for f in fs] == [level] and "deepmap.jpg" in fs[0].message


def test_good_prune_keeps_the_deep_tiles(project, monkeypatch, tmp_path):
    from video_engine import cache_prune
    _deep_profile(monkeypatch)
    d, tl = _deep_proj(project)
    keep, problems = cache_prune.referenced([d], tmp_path)
    deep = {e["img"].split("/")[1] for e in hyperframes.scene_data(tl.segments[2], tl, d)["deep"]}
    assert not problems and deep and deep <= keep
