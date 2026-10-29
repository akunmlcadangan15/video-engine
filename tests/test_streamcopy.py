"""Stream-copy and concat eligibility (streams.py): every copy or concat-demuxer path joins packets only when every
stream parameter matches exactly, time base included; otherwise it re-encodes or retimes, and the result has the
exact frame count and duration."""
import subprocess

import numpy as np
import pytest

from video_engine import audio, config, native, streams
from video_engine.util import EngineError, count_frames

from conftest import engine_clip

SMALL = (320, 240)


def ff(*a):
    subprocess.run([config.FFMPEG, "-v", "error", "-y", *map(str, a)], check=True)


def remux(src, out, *opts):
    ff("-i", src, "-map", "0", "-c", "copy", *opts, out)
    return out


@pytest.fixture
def clip(tmp_path):
    """clip(name, n=30, size=SMALL, fps=30, src=testsrc2, extra=()) -> engine-format clip in tmp_path."""
    def make(name, n=30, size=SMALL, fps=30, src="testsrc2", extra=()):
        return engine_clip(tmp_path / name, n, size, fps, src, extra)
    return make


# Each case: a source differing from the engine format in one parameter group -> (path, expected differing keys)
def _tb(clip, tmp):
    return remux(clip("a.mp4"), tmp / "tb.mp4", "-video_track_timescale", "90000"), {"time_base"}


def _fps(clip, tmp):
    return clip("fps.mp4", fps=25), {"r_frame_rate", "avg_frame_rate", "time_base"}


def _pix_fmt(clip, tmp):
    return clip("pix.mp4", extra=("-pix_fmt", "yuv444p")), {"pix_fmt"}


def _sar(clip, tmp):
    return remux(clip("a.mp4"), tmp / "sar.mp4", "-bsf:v", "h264_metadata=sample_aspect_ratio=4/3"), \
        {"sample_aspect_ratio"}


def _codec_params(clip, tmp):
    return clip("prm.mp4", extra=("-preset", "ultrafast")), {"extradata_hash"}


def _colour(clip, tmp):
    return remux(clip("a.mp4"), tmp / "col.mp4", "-bsf:v", "h264_metadata=colour_primaries=9:transfer_characteristics=14"
                 ":matrix_coefficients=9"), {"color_primaries", "color_transfer", "color_space"}


def _field_order(clip, tmp):
    return clip("fo.mp4", extra=("-flags", "+ilme+ildct", "-top", "1")), {"field_order"}


def _size(clip, tmp):
    return clip("size.mp4", size=(336, 240)), {"width"}


MISMATCH = {"time-base": _tb, "fps": _fps, "pix_fmt": _pix_fmt, "sar": _sar, "codec-params": _codec_params,
            "colour": _colour, "field-order": _field_order, "size": _size}


def _keys(diffs):
    return {d.split(".", 1)[1].split(":", 1)[0] for d in diffs}


# ---------------------------------------------------------------------------------------------------------- good
def test_good_engine_clip_is_the_engine_format_and_copy_eligible(clip):
    p = clip("e.mp4")
    assert streams.mismatch(streams.params(p, "v:0"), streams.engine_format(*SMALL, 30)) == []
    assert native.copy_eligible(p, *SMALL, 30)


@pytest.mark.parametrize("case", list(MISMATCH))
def test_good_each_parameter_mismatch_forbids_copy(case, clip, tmp_path):
    src, want = MISMATCH[case](clip, tmp_path)
    diffs = streams.mismatch(streams.params(src, "v:0"), streams.engine_format(*SMALL, 30))
    assert want <= _keys(diffs), diffs
    assert not native.copy_eligible(src, *SMALL, 30)
    assert not streams.joinable([clip("e.mp4"), src])[0]


def test_good_time_base_alone_differs_and_forbids_copy(clip, tmp_path):
    src, _ = _tb(clip, tmp_path)
    diffs = streams.mismatch(streams.params(src, "v:0"), streams.engine_format(*SMALL, 30))
    assert _keys(diffs) == {"time_base"}, diffs


def _seg_project(project, tmp_path, src_name):
    return project(lambda d: d["segments"][1].update(source_video=f"assets/{src_name}"))


def test_good_time_base_mismatch_re_encodes_with_exact_frames_and_duration(project, tmp_path, monkeypatch):
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    e = engine_clip(tmp_path / "e.mp4", 90)
    remux(e, tmp_path / "proj" / "assets" / "tb.mp4", "-video_track_timescale", "90000")
    p, tl = _seg_project(project, tmp_path, "tb.mp4")
    cmds = []
    real = native.run
    monkeypatch.setattr(native, "run", lambda cmd, **k: (cmds.append([str(c) for c in cmd]), real(cmd, **k))[1])
    job = native.plan(tl.segments[1], tl, p)
    out, _, mode = native.render(job, tl, tmp_path / "w", use_cache=False)
    assert job.mode == mode == "encode" and not any("copy" in c for c in cmds)
    streams.verify(out, 60, 30, "segment")
    assert streams.params(out, "v:0")[0]["time_base"] == "1/15360" and count_frames(out) == 60


def test_good_matching_source_stream_copies(project, tmp_path, monkeypatch):
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    engine_clip(tmp_path / "proj" / "assets" / "e.mp4", 90)
    p, tl = _seg_project(project, tmp_path, "e.mp4")
    cmds = []
    real = native.run
    monkeypatch.setattr(native, "run", lambda cmd, **k: (cmds.append([str(c) for c in cmd]), real(cmd, **k))[1])
    job = native.plan(tl.segments[1], tl, p)
    out, _, mode = native.render(job, tl, tmp_path / "w", use_cache=False)
    assert job.mode == mode == "copy"
    ffmpeg = [c for c in cmds if c[0] == str(config.FFMPEG)]
    assert len(ffmpeg) == 1 and ffmpeg[0][ffmpeg[0].index("-c") + 1] == "copy"
    streams.verify(out, 60, 30, "segment")


def _colour_clip(tmp_path, name, rgb, timescale):
    c = engine_clip(tmp_path / f"{name}.raw.mp4", 6, src=f"color=c=0x{rgb}")
    return remux(c, tmp_path / f"{name}.mp4", "-video_track_timescale", str(timescale))


def _mux_two(project, tmp_path, monkeypatch, a, b):
    d, tl = project(lambda doc: doc.update(segments=[
        dict(doc["segments"][1], seg_index=0, start_frame=0, end_frame=6),
        dict(doc["segments"][1], seg_index=1, start_frame=6, end_frame=12)]))
    seen = []
    real = audio.run
    monkeypatch.setattr(audio, "run", lambda cmd, **k: (seen.append([str(c) for c in cmd]), real(cmd, **k))[1])
    out = tmp_path / "final.mp4"
    audio.final_mux(tl, d, [a, b], None, out, threads=2)
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", out, "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt",
                          "rgb24", "-"], capture_output=True, check=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(-1, 8, 8, 3)[:, 4, 4].astype(int)
    return out, next(c for c in seen if "-an" in c), ["red" if p[0] > 150 and p[2] < 100 else "blue" if p[2] > 150 and p[0] < 100 else "?"
                           for p in px]


def test_good_mixed_time_base_clips_keep_all_twelve_frames_at_the_boundary(project, tmp_path, monkeypatch):
    a = _colour_clip(tmp_path, "red", "ff0000", 15360)
    b = _colour_clip(tmp_path, "blue", "0000ff", 90000)
    out, cmd, colours = _mux_two(project, tmp_path, monkeypatch, a, b)
    assert colours == ["red"] * 6 + ["blue"] * 6
    assert "concat" not in cmd[cmd.index("-i") - 2:cmd.index("-i")] and "concat=n=2" in cmd[cmd.index("-filter_complex") + 1]
    streams.verify(out, 12, 30, "final")


def test_good_matching_segments_use_the_concat_demuxer(project, tmp_path, monkeypatch):
    a = _colour_clip(tmp_path, "red", "ff0000", 15360)
    b = _colour_clip(tmp_path, "blue", "0000ff", 15360)
    out, cmd, colours = _mux_two(project, tmp_path, monkeypatch, a, b)
    assert colours == ["red"] * 6 + ["blue"] * 6
    assert cmd[cmd.index("-f") + 1] == "concat" and "concat=n=" not in cmd[cmd.index("-filter_complex") + 1]
    assert not (tmp_path / "final.concat.txt").exists()


def _audio(tmp_path, name, rate, layout="stereo"):
    out = tmp_path / f"{name}.m4a"
    ff("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}", "-t", "0.5", "-ac", 2 if layout == "stereo" else 1,
       "-c:a", "aac", "-b:a", "96k", out)
    return out


def test_good_audio_parameters_decide_joinability(tmp_path):
    a, b = _audio(tmp_path, "a", 48000), _audio(tmp_path, "b", 48000)
    assert streams.joinable([a, b]) == (True, [])
    ok, diffs = streams.joinable([a, _audio(tmp_path, "c", 44100)])
    assert not ok and {"sample_rate", "time_base"} <= _keys(d.split(": ", 1)[1] for d in diffs)
    ok, diffs = streams.joinable([a, _audio(tmp_path, "m", 48000, "mono")])
    assert not ok and {"channels", "channel_layout"} <= _keys(d.split(": ", 1)[1] for d in diffs)


def test_good_gain_pass_copies_video_and_keeps_its_parameters(tmp_path, monkeypatch):
    v = engine_clip(tmp_path / "v.mp4", 30, SMALL)
    src = tmp_path / "src.mp4"
    ff("-i", v, "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
       "-c:a", "aac", "-shortest", src)
    seen = []
    real = audio.run
    monkeypatch.setattr(audio, "run", lambda cmd, **k: (seen.append([str(c) for c in cmd]), real(cmd, **k))[1])
    dst = audio.apply_gain(src, tmp_path / "dst.mp4", -1.0, -1.5)
    assert seen[0][seen[0].index("-c:v") + 1] == "copy"
    assert streams.mismatch(streams.params(dst, "v:0"), streams.params(src, "v:0")) == []
    streams.verify(dst, 30, 30, "gain")


def test_good_hyperframes_output_with_exact_timing_passes_through(project, tmp_path):
    from video_engine import hyperframes
    _, tl = project()
    assert hyperframes._is_engine_format(engine_clip(tmp_path / "hf.mp4", 60), tl, 60)


# ----------------------------------------------------------------------------------------------------------- bad
def test_bad_verify_rejects_a_wrong_frame_count_start_or_duration(clip, tmp_path):
    p = clip("e.mp4", n=12)
    streams.verify(p, 12, 30, "ok")
    with pytest.raises(EngineError, match="11 frames|12 frames from"):
        streams.verify(p, 11, 30, "count")
    late = remux(p, tmp_path / "late.mp4", "-output_ts_offset", "1")
    with pytest.raises(EngineError, match="from 1.0"):
        streams.verify(late, 12, 30, "start")
    with pytest.raises(EngineError, match="lasting 0.4"):
        streams.verify(p, 12, 25, "duration")


def test_bad_stream_counts_or_kinds_never_match(tmp_path, clip):
    v = clip("v.mp4")
    a = _audio(tmp_path, "a", 48000)
    assert streams.mismatch(streams.params(v), streams.params(a))[0].startswith("streams:")
    assert not streams.joinable([v, a])[0]


def test_bad_time_base_mismatched_source_is_never_planned_as_copy(project, tmp_path):
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    remux(engine_clip(tmp_path / "e.mp4", 90), tmp_path / "proj" / "assets" / "tb.mp4", "-video_track_timescale", "90000")
    p, tl = _seg_project(project, tmp_path, "tb.mp4")
    assert native.plan(tl.segments[1], tl, p).mode == "encode"


def test_bad_copy_that_changes_a_parameter_falls_back_to_encode(project, tmp_path, monkeypatch):
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    engine_clip(tmp_path / "proj" / "assets" / "e.mp4", 90)
    p, tl = _seg_project(project, tmp_path, "e.mp4")
    job = native.plan(tl.segments[1], tl, p)
    assert job.mode == "copy"

    def bad_copy(job, tl, out):     # a copy whose time base drifted
        ff("-i", job.src, "-map", "0:v:0", "-c", "copy", "-frames:v", job.seg.n_frames, "-video_track_timescale", 90000,
           out)
    monkeypatch.setattr(native, "render_copy", bad_copy)
    out, _, mode = native.render(job, tl, tmp_path / "w", use_cache=False)
    assert mode == "encode" and streams.params(out, "v:0")[0]["time_base"] == "1/15360"
    streams.verify(out, 60, 30, "segment")


def test_bad_gain_pass_that_changes_the_video_stream_fails_loudly(tmp_path, monkeypatch):
    v = engine_clip(tmp_path / "v.mp4", 30, SMALL)
    src = tmp_path / "src.mp4"
    ff("-i", v, "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
       "-c:a", "aac", "-shortest", src)
    real = audio.run

    def drift(cmd, **k):
        real(cmd, **k)
        dst = cmd[-1]
        tmp = tmp_path / "t.mp4"
        remux(dst, tmp, "-video_track_timescale", "90000")
        tmp.replace(dst)
    monkeypatch.setattr(audio, "run", drift)
    with pytest.raises(EngineError, match="time_base"):
        audio.apply_gain(src, tmp_path / "dst.mp4", -1.0, -1.5)


def test_bad_hyperframes_output_with_shifted_start_is_normalised(project, tmp_path):
    from video_engine import hyperframes
    _, tl = project()
    late = remux(engine_clip(tmp_path / "hf.mp4", 60), tmp_path / "late.mp4", "-output_ts_offset", "0.5")
    assert not hyperframes._is_engine_format(late, tl, 60)
    assert not hyperframes._is_engine_format(engine_clip(tmp_path / "hf25.mp4", 60, fps=25), tl, 60)
