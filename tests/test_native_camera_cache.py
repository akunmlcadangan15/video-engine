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


def test_good_camera_and_encode_segments_share_the_sar_and_join_by_concat_demuxer(project, wd):
    """A Ken Burns still (camera) and footage re-encoded to the frame (encode) write the same square-pixel SAR into
    their SPS, so a timeline mixing them stays concat-demuxer joinable (the draft preview requires it)."""
    from video_engine import streams
    p, tl = project()
    files = [native.render(native.plan(s, tl, p), tl, wd, threads=2, use_cache=False) for s in tl.segments[:2]]
    assert [f[2] for f in files] == ["camera", "encode"]
    assert [streams.params(f[0], "v:0")[0]["sample_aspect_ratio"] for f in files] == ["1:1", "1:1"]
    assert streams.joinable([f[0] for f in files]) == (True, [])


def test_bad_camera_segments_without_setsar_break_the_concat_join(project, wd, monkeypatch):
    """The camera encoder without setsar (before the fix) leaves the SAR unset: the pair differs in SAR and SPS."""
    from video_engine import streams
    p, tl = project()
    monkeypatch.setattr(native, "CAMERA_VF", native.BT709_VF)
    files = [native.render(native.plan(s, tl, p), tl, wd, threads=2, use_cache=False)[0] for s in tl.segments[:2]]
    ok, diffs = streams.joinable(files)
    assert not ok and any("sample_aspect_ratio" in d for d in diffs)


def test_good_keyframe_probe_runs_once_per_settled_source(tmp_path, monkeypatch):
    from video_engine import util
    clip = engine_clip(tmp_path / "engine.mp4", 60)
    monkeypatch.setattr(util, "RACY_NS", 0)
    monkeypatch.setattr(native, "_KEYFRAME", {})
    calls = []
    real = native.run
    monkeypatch.setattr(native, "run", lambda cmd, **k: calls.append(cmd) or real(cmd, **k))
    assert native._keyframe_at(clip, 0.0, 30) and native._keyframe_at(clip, 0.0, 30) and len(calls) == 1
    assert not native._keyframe_at(clip, 10 / 30, 30) and len(calls) == 2      # another time: probed


def test_bad_keyframe_probe_reads_a_rewritten_source_again(tmp_path, monkeypatch):
    from video_engine import util
    clip = engine_clip(tmp_path / "engine.mp4", 60)
    monkeypatch.setattr(util, "RACY_NS", 0)
    monkeypatch.setattr(native, "_KEYFRAME", {})
    assert not native._keyframe_at(clip, 10 / 30, 30)
    engine_clip(tmp_path / "rewritten.mp4", 60, extra=("-g", "10", "-keyint_min", "10", "-sc_threshold", "0"))
    (tmp_path / "rewritten.mp4").replace(clip)
    assert native._keyframe_at(clip, 10 / 30, 30)


def test_good_warp_still_and_stream_fds_match_warp_frame_and_fallback(project, wd, monkeypatch):
    import os

    sw, sh, ow, oh = 16, 12, 8, 6
    fsz = sw * sh * 3
    src0 = bytes((i * 11 % 251 for i in range(fsz)))
    src1 = bytes(((i * 13 + 37) % 251 for i in range(fsz)))
    wins = core.camera_windows((0.0, 0.0, 12.0, 9.0), (4.0, 3.0, 8.0, 6.0), 4)

    r_fd, w_fd = os.pipe()
    try:
        core.warp_still_to_fd(src0, sw, sh, wins, ow, oh, w_fd)
    finally:
        os.close(w_fd)
    with os.fdopen(r_fd, "rb") as f:
        still_got = f.read()
    assert still_got == b"".join(core.warp_frame(src0, sw, sh, w, ow, oh) for w in wins)

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    try:
        with os.fdopen(in_w, "wb") as fin:
            fin.write(src0 + src1)
        written = core.warp_stream_to_fd(in_r, out_w, sw, sh, wins, ow, oh)
    finally:
        os.close(in_r)
        os.close(out_w)
    with os.fdopen(out_r, "rb") as fout:
        stream_got = fout.read()
    assert written == 4
    expected = b"".join(
        core.warp_frame(src0 if i == 0 else src1, sw, sh, w, ow, oh) for i, w in enumerate(wins)
    )
    assert stream_got == expected

    # EOF before first complete frame returns 0 cleanly
    e_r, e_w = os.pipe()
    o2_r, o2_w = os.pipe()
    try:
        os.close(e_w)
        assert core.warp_stream_to_fd(e_r, o2_w, sw, sh, wins, ow, oh) == 0
    finally:
        os.close(e_r)
        os.close(o2_w)
        os.close(o2_r)

    # render_camera produces exact 60 frames with both fd path and Python fallback (still + video)
    wd.mkdir(parents=True, exist_ok=True)
    p, tl = project()
    job = native.plan(tl.segments[0], tl, p)
    out_fast = wd / "fast.mp4"
    out_slow = wd / "slow.mp4"
    native.render_camera(job, tl, out_fast, threads=2)
    monkeypatch.setattr(native, "_fd_or_none", lambda _s: None)
    native.render_camera(job, tl, out_slow, threads=2)
    assert count_frames(out_fast) == 60 and count_frames(out_slow) == 60

    monkeypatch.undo()
    p2, tl2 = project(lambda d: d["segments"][1].update(
        camera_move={"preset": "ken_burns_zoom_in", "intensity": 1.2},
    ))
    vjob = native.plan(tl2.segments[1], tl2, p2)
    out_v = wd / "vid_fast.mp4"
    native.render_camera(vjob, tl2, out_v, threads=2)
    assert count_frames(out_v) == 60
