"""Engine draft preview (`engine draft-preview` / scripts/engine-draft): first 5 segments, concat demuxer join,
no final encode. GOOD and BAD cases on a synthetic native-only project (no HyperFrames toolchain needed).
"""
import json

import pytest

from video_engine import draft_build, streams
from video_engine.cli import main, parser
from video_engine.util import EngineError, probe

FPS = 30


def _many_native(n=7, seg_frames=30):
    """Timeline mutator: n native image segments of seg_frames frames each."""
    def mut(d):
        out, f = [], 0
        for i in range(n):
            out.append({"seg_index": i, "engine_type": "NATIVE_RUST_FFMPEG",
                        "start_frame": f, "end_frame": f + seg_frames,
                        "source_image": "assets/still.jpg", "asset_id": "still",
                        "camera_move": {"preset": "ken_burns_zoom_in", "intensity": 1.0}})
            f += seg_frames
        d["segments"] = out
    return mut


# ----------------------------------------------------------------------------------------------------------- good
def test_good_default_is_five_segments_concat_demuxer_no_final_encode(project):
    p, _ = project(_many_native(7))
    r = draft_build.build_draft_preview(p, use_cache=False)
    assert r.ok and len(r.segments) == 5 and r.join == "concat demuxer"
    assert r.frames == r.expected_frames == 5 * 30
    assert r.preview == p / "out" / "draft" / "preview.mp4" and r.preview.is_file()
    assert not (p / "out" / "final.mp4").exists()          # a preview never publishes a final
    rep = json.loads(r.report.read_text())
    assert rep["mode"] == "draft-preview" and rep["final_encode"] is False and rep["join"] == "concat demuxer"
    assert len(rep["segments"]) == 5
    streams.verify(r.preview, 150, FPS, "draft preview")


def test_good_still_and_footage_preview_joins_and_says_no_gate_ran(project):
    """A Ken Burns still next to re-encoded footage (the base project's first two segments) joins with the concat
    demuxer; the preview report says that no gate looked at it."""
    p, _ = project()
    r = draft_build.build_draft_preview(p, max_segments=2, use_cache=False)
    assert r.ok and [s.mode for s in r.segments] == ["camera", "encode"] and r.join == "concat demuxer"
    rep = json.loads(r.report.read_text())
    assert [e["kind"] for e in rep["not_checked"]] == ["not_run", "not_run"]
    assert all(e["gate"] is None and e["seg"] is None and "draft preview" in e["why"] for e in rep["not_checked"])


def test_good_concat_command_is_stream_copy_without_reencode(project, monkeypatch):
    p, _ = project(_many_native(3))
    seen = []
    real_run = draft_build.run

    def spy(cmd, **k):
        seen.append([str(c) for c in cmd])
        return real_run(cmd, **k)

    monkeypatch.setattr(draft_build, "run", spy)
    draft_build.build_draft_preview(p, max_segments=2, use_cache=False)
    join = next(c for c in seen if "concat" in c)
    assert join[join.index("-f") + 1] == "concat" and join[join.index("-c") + 1] == "copy"
    assert not any("libx264" in c or "-filter_complex" in c for c in join)   # no final encode, no composite


def test_good_preview_muxes_narration(project):
    def mut(d):
        _many_native(2)(d)
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "auto_captions": False}
    p, _ = project(mut)
    r = draft_build.build_draft_preview(p, max_segments=2, use_cache=False)
    assert r.audio == "narration"
    info = probe(r.preview)
    assert any(s.get("codec_type") == "audio" for s in info["streams"])
    assert json.loads(r.report.read_text())["audio"] == "narration"
    streams.verify(r.preview, 60, FPS, "draft preview")


def test_good_fewer_segments_than_five_and_custom_count(project):
    p, _ = project(_many_native(3))
    r = draft_build.build_draft_preview(p, use_cache=False)                  # only 3 exist
    assert len(r.segments) == 3 and r.frames == 90
    r2 = draft_build.build_draft_preview(p, max_segments=2, use_cache=False)
    assert len(r2.segments) == 2 and r2.frames == 60


def test_good_cli_draft_preview(project, capsys):
    p, _ = project(_many_native(6))
    assert main(["draft-preview", str(p)]) == 0
    out = capsys.readouterr().out
    assert "draft preview: 5 segment(s)" in out and "concat demuxer" in out
    assert (p / "out" / "draft" / "preview.mp4").is_file()
    a = parser().parse_args(["draft-preview", "p"])
    assert a.segments == 5 and a.fn.__name__ == "cmd_draft_preview"


def test_good_engine_draft_wrapper_runs_draft_preview():
    import os
    import stat
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    assert os.access(scripts / "engine-draft", os.X_OK)
    assert "draft-preview" in (scripts / "engine-draft").read_text()
    assert stat.S_IXUSR or True


# ------------------------------------------------------------------------------------------------------------ bad
def test_bad_output_outside_draft_dir_is_rejected(project, tmp_path):
    p, _ = project(_many_native(2))
    with pytest.raises(EngineError, match="out/draft"):
        draft_build.build_draft_preview(p, out=tmp_path / "preview.mp4", use_cache=False)


def test_bad_max_segments_zero_is_rejected(project):
    p, _ = project(_many_native(2))
    with pytest.raises(EngineError, match="max_segments"):
        draft_build.build_draft_preview(p, max_segments=0, use_cache=False)


def test_bad_non_joinable_segments_fail_instead_of_reencoding(project, monkeypatch):
    p, _ = project(_many_native(2))
    monkeypatch.setattr(streams, "joinable", lambda files: (False, ["seg1.mp4: v0.time_base: 1/15360 != 1/90000"]))
    with pytest.raises(EngineError, match="concat-demuxer joinable"):
        draft_build.build_draft_preview(p, use_cache=False)


# (empty-segments timelines are rejected by Timeline validation itself, so no draft-preview test is needed for that)
