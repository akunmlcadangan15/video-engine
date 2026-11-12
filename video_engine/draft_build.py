"""Engine draft preview: render the first N segments and join them with the concat demuxer, no final encode.

`engine draft` drafts a timeline with an AI provider; this module is the other draft: a fast technical preview
for a real project. It renders at most `max_segments` (default 5) segments with the normal segment renderers
(`build.render_segments`, cache included), then joins the segment files with ffmpeg's concat demuxer in
stream-copy mode (`-c copy`). There is deliberately no composite pass (caption burn / watermark / transitions)
and no final x264 encode: the preview is never deliverable and never touches `out/final.mp4`.

After the picture is joined, the preview muxes audio when the timeline has any. A profile `[sound]` mix
(narration, music and SFX, including manual cues) is used when it can be planned from files already on disk.
Otherwise the narration is muxed, with `bgm_path` when the simple mix has one. A timeline with neither stays
silent. The audio is AAC at the profile bitrate, cut to the preview length, and the video packets are copied.

The concat demuxer joins packets only; it requires every segment file to share exactly the same stream
parameters (`streams.joinable`). When they do not, this fails with an EngineError naming the differing
parameters instead of silently re-encoding through the concat filter (that fallback is the full build's
composite step, not a draft preview).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, build, config, resources, streams, timeline
from .gates.severity import unchecked
from .util import EngineError, probe, run

DEFAULT_MAX_SEGMENTS = 5
PREVIEW_NAME = "preview.mp4"
PREVIEW_REPORT = "preview_report.json"
# preview_report.json `not_checked` (docs/BUILD.md): a preview runs no gate at all.
NOT_CHECKED = [unchecked(None, "not_run", "no pre-flight gate runs on a draft preview (run `engine preflight` or a "
                                           "build)"),
               unchecked(None, "not_run", "no post-render gate runs on a draft preview: no pixel QA, no loudness "
                                          "(the preview has no audio), no silence or audio source check")]


@dataclass
class DraftPreviewResult:
    preview: Path | None
    segments: list = field(default_factory=list)   # build.SegResult list, first max_segments only
    frames: int = 0
    expected_frames: int = 0
    join: str = "concat demuxer"
    audio: str = "none"             # none | narration | mix
    report: Path | None = None
    ok: bool = True


def concat_demuxer_cmd(files: list[Path], list_path: Path, out: Path) -> list[str]:
    """ffmpeg argv for the preview join: concat demuxer + stream copy, never a re-encode."""
    list_path.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in files))
    return [config.FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", str(out)]


def _preview_mix(tl, project_dir: Path):
    """Sound mix for the preview, or None when it cannot be planned from files already on disk."""
    from . import mixer, profiles, sfx
    from . import manifest
    if profiles.of(tl).sound is None and not tl.audio_chain.sfx:
        return None
    lock = None
    if profiles.of(tl).sound is not None:
        try:
            lock = manifest.locked_sounds(manifest.load(project_dir / "out"), tl, sfx.kinds_used(tl), project_dir)
        except EngineError:
            lock = None
    try:
        return mixer.plan(tl, project_dir, list(tl.captions), lock)
    except EngineError:
        if lock is not None:
            try:
                return mixer.plan(tl, project_dir, list(tl.captions), None)
            except EngineError:
                return None
        return None


def _mux_preview_audio(tl, project_dir: Path, video: Path, workdir: Path) -> str:
    """Attach narration or the sound mix to `video`. Returns `none`, `narration` or `mix`."""
    ac = tl.audio_chain
    mix = _preview_mix(tl, project_dir)
    mode = "mix" if mix is not None else None
    if mix is None and (ac.voiceover_path or ac.bgm_path):
        vo = project_dir / ac.voiceover_path if ac.voiceover_path else None
        bg = project_dir / ac.bgm_path if ac.bgm_path else None
        if (vo is None or vo.is_file()) and (bg is None or bg.is_file()) and (vo or bg):
            mode = "narration"
    if mode is None:
        return "none"
    if mode == "mix" and not (ac.voiceover_path or (mix and (mix.music or mix.cues)) or ac.bgm_path):
        return "none"
    workdir.mkdir(parents=True, exist_ok=True)
    m4a = workdir / "preview-audio.m4a"
    run(audio.audio_cmd(tl, project_dir, m4a, None, mix if mode == "mix" else None))
    muxed = video.with_name(video.stem + ".audio.mp4")
    run([config.FFMPEG, "-v", "error", "-y", "-i", video, "-i", m4a, "-map", "0:v:0", "-map", "1:a:0",
         "-c", "copy", "-movflags", "+faststart", muxed])
    muxed.replace(video)
    m4a.unlink(missing_ok=True)
    if not any(st.get("codec_type") == "audio" for st in probe(video)["streams"]):
        raise EngineError("draft preview: audio mux produced no audio stream")
    return mode


def build_draft_preview(project_dir: Path, *, max_segments: int = DEFAULT_MAX_SEGMENTS,
                        out: Path | None = None, use_cache: bool = True,
                        keep_workdir: bool = False) -> DraftPreviewResult:
    """Render the first `max_segments` segments of `project_dir` and concat-demuxer them into a preview.

    Output defaults to `<project>/out/draft/preview.mp4` with `preview_report.json` beside it. A custom
    `out` must still live under `<project>/out/draft/` so a preview can never masquerade as a final.
    Raises EngineError when there is nothing to preview, when the segments are not concat-joinable, or
    when the joined frame count differs from the sum of the previewed segments.
    """
    if max_segments < 1:
        raise EngineError(f"max_segments must be >= 1, got {max_segments}")
    project_dir = Path(project_dir).resolve()
    tl = timeline.load(project_dir)
    if not tl.segments:
        raise EngineError("draft preview: timeline has no segments")
    preview_tl = tl.model_copy(deep=True)
    preview_tl.segments = list(tl.segments[:max_segments])
    expected = sum(s.n_frames for s in preview_tl.segments)

    out = Path(out) if out is not None else project_dir / "out" / build.DRAFT_DIR / PREVIEW_NAME
    out = out.resolve()
    draft_dir = (project_dir / "out" / build.DRAFT_DIR).resolve()
    if not out.is_relative_to(draft_dir):
        raise EngineError("draft preview output must be inside the project out/draft directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    report_path = out.with_name(PREVIEW_REPORT)

    workdir = build.project_workdir(tl)
    try:
        segs = build.render_segments(preview_tl, project_dir, workdir, use_cache=use_cache,
                                     budget=resources.budget(tl))
        files = [Path(r.path) for r in segs]
        joined, diffs = streams.joinable(files)
        if not joined:
            raise EngineError("draft preview: segments are not concat-demuxer joinable "
                              f"(stream parameters differ: {'; '.join(diffs[:5])}); "
                              "a full build would re-encode them in the composite step")
        list_path = out.with_name(out.stem + ".concat.txt")
        cmd = concat_demuxer_cmd(files, list_path, out)
        try:
            run(cmd)
        finally:
            list_path.unlink(missing_ok=True)
        streams.verify(out, expected, tl.settings.fps, "draft preview")
        heard = _mux_preview_audio(preview_tl, project_dir, out, workdir)
        streams.verify(out, expected, tl.settings.fps, "draft preview")
        frames = expected
        report = {
            "ok": True, "mode": "draft-preview", "final_encode": False, "join": "concat demuxer",
            "audio": heard, "preview": str(out), "frames": frames, "expected_frames": expected,
            "segments": [r.__dict__ for r in segs],
            "project": str(project_dir), "project_id": tl.project_id, "not_checked": NOT_CHECKED,
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        return DraftPreviewResult(out, segs, frames, expected, "concat demuxer", heard, report_path, True)
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
