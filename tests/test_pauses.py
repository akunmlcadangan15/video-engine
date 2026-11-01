"""Rust pause detection, boundary suggestion and the Rust-driven proxy (good + bad cases)."""
import subprocess

import hypoverum_core_rs as core
import pytest

from video_engine import pauses
from video_engine.cli import main
from video_engine.util import EngineError, count_frames, video_info


def _narr(path, pattern):
    """pattern: list of (kind, seconds) with kind 'tone' or 'sil'."""
    parts = []
    for i, (k, d) in enumerate(pattern):
        src = f"sine=f=300:d={d}" if k == "tone" else f"anullsrc=r=16000:cl=mono:d={d}"
        parts += ["-f", "lavfi", "-i", src]
    n = len(pattern)
    flt = "".join(f"[{i}:a]aresample=16000,aformat=channel_layouts=mono[a{i}];" for i in range(n))
    flt += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[o]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *parts, "-filter_complex", flt, "-map", "[o]", str(path)], check=True)
    return path


def test_good_pauses_found_in_frames(tmp_path):
    p = _narr(tmp_path / "n.wav", [("tone", 2), ("sil", 0.5), ("tone", 2), ("sil", 0.1), ("tone", 1)])
    r = pauses.analyze(p, 30, max_seg_s=3.5)
    assert r.total_frames in (167, 168)  # ceil(5.6 s * 30); the lavfi sine may add a few samples
    assert [(x.start_frame, x.end_frame) for x in r.pauses] == [(60, 75)]  # 0.1 s gap < 0.2 s minimum
    assert all(isinstance(v, int) for x in r.pauses for v in (x.start_frame, x.end_frame))
    assert r.boundaries == [67] and r.segments == [(0, 67), (67, r.total_frames)]


def test_good_boundaries_respect_lengths():
    rows = [(100, 104, -60.0), (140, 160, -60.0), (200, 202, -60.0)]
    assert core.suggest_boundaries(rows, 300, 60, 200) == [150]
    assert core.suggest_boundaries([], 500, 60, 200) == [200, 400]


def test_bad_boundary_and_pcm_params():
    with pytest.raises(ValueError):
        core.suggest_boundaries([], 10, 5, 4)
    with pytest.raises(ValueError):
        core.detect_pauses_pcm(b"\x00", 16000, 30)  # odd byte count
    with pytest.raises(ValueError):
        core.detect_pauses_pcm(b"\x00\x00", 16000, 0)
    with pytest.raises(ValueError):
        core.detect_pauses_pcm(b"\x00\x00", 16000, 30, threshold_db=float("nan"))


def test_good_pcm_silence_only_is_one_pause():
    rows, thr = core.detect_pauses_pcm(b"\x00\x00" * 16000, 16000, 25, None, 5)
    assert rows == [(0, 25, -120.0)] and thr == -119.0


def test_bad_missing_or_undecodable_audio(tmp_path):
    with pytest.raises(EngineError, match="not found"):
        pauses.analyze(tmp_path / "missing.wav", 30)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio")
    with pytest.raises(EngineError, match="ffmpeg decode failed"):
        pauses.analyze(junk, 30)


def test_good_cli_pauses_json(project, capsys):
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav"}))
    assert main(["pauses", str(p), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"segments"' in out and '"threshold_db"' in out


def test_bad_cli_pauses_without_voiceover(project, capsys):
    p, _ = project()
    assert main(["pauses", str(p)]) == 1
    assert "voiceover_path is empty" in capsys.readouterr().err


def test_good_rust_proxy_360p_5fps(tmp_path, assets):
    out = core.make_proxy(assets / "clip_1080x1920_30.mp4", tmp_path / "p.mp4", seconds=2.0)
    vi = video_info(out)
    assert vi["height"] == 360 and count_frames(out) == 10
    assert not (tmp_path / "p.mp4.part").exists()


def test_bad_rust_proxy(tmp_path, assets):
    with pytest.raises(FileNotFoundError):
        core.make_proxy(tmp_path / "nope.mp4", tmp_path / "p.mp4")
    with pytest.raises(ValueError):
        core.make_proxy(assets / "clip_1080x1920_30.mp4", tmp_path / "p.mp4", height=361)
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"junk")
    with pytest.raises(OSError, match="ffmpeg proxy failed"):
        core.make_proxy(bad, tmp_path / "q.mp4")
    assert not (tmp_path / "q.mp4").exists() and not (tmp_path / "q.mp4.part").exists()
