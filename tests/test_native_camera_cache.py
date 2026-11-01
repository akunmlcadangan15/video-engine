"""Native path: exact frame counts for camera / encode / stream-copy, camera maths, BLAKE3 cache hits."""
import pytest

import hypoverum_core_rs as core
from video_engine import native
from video_engine.util import count_frames, video_info

from conftest import engine_clip


@pytest.fixture
def wd(tmp_path):
    return tmp_path / "work"


def test_camera_windows_count_and_motion():
    a, b, _motion = core.camera_plan("ken_burns_zoom_in", 1.0, 1600.0, 1200.0, 1080.0, 1920.0, None)
    wins = core.camera_windows(a, b, 45)
    assert len(wins) == 45 and wins[0] == pytest.approx(a) and wins[-1] == pytest.approx(b)
    assert wins[-1][2] < wins[0][2]           # zoom in: window shrinks


def test_camera_window_inside_source():
    for preset in ("ken_burns_zoom_in", "ken_burns_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"):
        a, b, _motion = core.camera_plan(preset, 1.0, 1600.0, 1200.0, 1080.0, 1920.0, None)
        for x, y, w, h in (a, b):
            assert x >= -1e-6 and y >= -1e-6 and x + w <= 1600 + 1e-6 and y + h <= 1200 + 1e-6


def test_good_camera_still_exact_frames(project, wd):
    p, tl = project()
    job = native.plan(tl.segments[0], tl, p)
    out, hit, mode = native.render(job, tl, wd, threads=2, use_cache=False)
    assert mode == "camera" and count_frames(out) == 60
    vi = video_info(out)
    assert (vi["width"], vi["height"]) == (1080, 1920)


def test_good_stream_copy_engine_format(project, wd, tmp_path):
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    engine_clip(tmp_path / "proj" / "assets" / "engine.mp4", 90)        # the engine segment format exactly
    p, tl = project(lambda d: d["segments"][1].update(source_video="assets/engine.mp4"))   # source_in 0 (keyframe)
    job = native.plan(tl.segments[1], tl, p)
    assert job.mode == "copy"
    out, _, mode = native.render(job, tl, wd, use_cache=False)
    assert mode == "copy" and count_frames(out) == 60


def test_good_copy_falls_back_off_keyframe(project, wd):
    p, tl = project(lambda d: d["segments"][1].update(source_in_frame=10))   # not a keyframe
    job = native.plan(tl.segments[1], tl, p)
    out, _, mode = native.render(job, tl, wd, use_cache=False)
    assert job.mode == "encode" and mode == "encode" and count_frames(out) == 60


def test_good_encode_other_format(project, wd):
    def mut(d):
        d["credits_manifest"][1]["path"] = "assets/clip_1280x720_25.mp4"
        d["segments"][1]["source_video"] = "assets/clip_1280x720_25.mp4"
    p, tl = project(mut)
    job = native.plan(tl.segments[1], tl, p)
    out, _, mode = native.render(job, tl, wd, use_cache=False)
    assert mode == "encode" and count_frames(out) == 60


def test_good_cache_hit_and_key_changes(project, wd, tmp_path, monkeypatch):
    from video_engine import config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    job = native.plan(tl.segments[0], tl, p)
    out1, hit1, _ = native.render(job, tl, wd, threads=2)
    out2, hit2, _ = native.render(job, tl, wd, threads=2)
    assert not hit1 and hit2 and out1 == out2
    tl.segments[0].camera_move.intensity = 1.3
    assert native.plan(tl.segments[0], tl, p).key != job.key


def test_good_preview_frame(project, tmp_path):
    from PIL import Image
    p, tl = project()
    out = native.preview_frame(native.plan(tl.segments[0], tl, p), tl, 30, tmp_path / "f.png")
    assert Image.open(out).size == (1080, 1920)


def test_good_copy_fallback_is_cached_under_the_encode_key(project, wd, tmp_path, monkeypatch):
    from video_engine import cache, config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    engine_clip(tmp_path / "proj" / "assets" / "engine.mp4", 90)
    p, tl = project(lambda d: d["segments"][1].update(source_video="assets/engine.mp4"))
    job = native.plan(tl.segments[1], tl, p)
    assert job.mode == "copy" and job.encode_key
    monkeypatch.setattr(native, "_copy_ok", lambda *a: False)
    out, hit, mode = native.render(job, tl, wd, threads=2)
    assert mode == "encode" and not hit
    assert cache.get_file("segments", job.key) is None
    assert cache.get_file("segments", job.encode_key) is not None
    out2, hit2, mode2 = native.render(job, tl, wd, threads=2)
    assert hit2 and mode2 == "encode" and out2 == out
