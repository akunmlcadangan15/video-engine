"""[render] final_bit_depth: 10 makes the final composite yuv420p10le / x264 High 10 with the same picture; segment
intermediates stay 8-bit; an ffmpeg whose libx264 lacks 10-bit is refused with a clear message."""
import numpy as np
import pytest
from pydantic import ValidationError

from conftest import engine_clip
from video_engine import audio, config, profiles, toolchain
from video_engine.util import EngineError, probe


def _composite(project, tmp_path, monkeypatch, depth):
    _, tl = project(lambda d: d["settings"].update(width=216, height=384))
    monkeypatch.setattr(profiles.of(tl).render, "final_bit_depth", depth)
    files = [engine_clip(tmp_path / f"s{i}.mp4", 60, (216, 384), src=s)
             for i, s in enumerate(["testsrc2", "smptebars", "rgbtestsrc"])]
    wd = tmp_path / f"wd{depth}"
    wd.mkdir()
    out, _, _ = audio.composite(tl, files, None, wd / "composite.mp4", threads=2, use_cache=False)
    return out, tl


def _frame(path):
    import subprocess
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-vf", "select=eq(n\\,90),format=rgb24",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).astype(float)


def test_good_ten_bit_final_is_high10_with_the_same_picture(project, tmp_path, monkeypatch):
    if "yuv420p10le" not in toolchain.x264_pix_fmts(str(config.FFMPEG)):
        pytest.skip("this ffmpeg's libx264 has no 10-bit")
    out10, tl = _composite(project, tmp_path, monkeypatch, 10)
    v = next(s for s in probe(out10)["streams"] if s["codec_type"] == "video")
    assert v["pix_fmt"] == "yuv420p10le" and v["profile"] == "High 10" and v["color_space"] == "bt709"
    assert profiles.of(tl).render.x264("segment") == list(config.X264_SEGMENT)     # intermediates stay 8-bit
    out8, _ = _composite(project, tmp_path, monkeypatch, 8)
    assert next(s for s in probe(out8)["streams"] if s["codec_type"] == "video")["pix_fmt"] == "yuv420p"
    assert np.abs(_frame(out10) - _frame(out8)).mean() < 1.5


def test_bad_ten_bit_without_a_ten_bit_x264_is_refused(project, tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "x264_pix_fmts", lambda exe: frozenset({"yuv420p"}))
    with pytest.raises(EngineError, match="final_bit_depth = 10.*no yuv420p10le"):
        _composite(project, tmp_path, monkeypatch, 10)


def test_bad_bit_depth_value_rejected():
    with pytest.raises(ValidationError):
        profiles.RenderProfile(final_bit_depth=12)


def test_good_x264_args_per_depth():
    assert "yuv420p10le" in config.x264(bit_depth=10) and "high10" in config.x264(bit_depth=10)
    assert config.x264() == config.X264 and "high10" not in config.X264
