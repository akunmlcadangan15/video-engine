"""Post-render pixel gates on sampled frames of the final video (in memory, Rust stats):
R38 (black/decode integrity), R38space (negative-space advice) on each sample; R56px motion per segment (mean abs luma difference
between consecutive samples). Samples: every SAMPLE_STEP-th frame at 1/4 size."""
from __future__ import annotations

import io
import subprocess
from pathlib import Path

import hypoverum_core_rs as core

from . import config
from .gates.core import Finding
from .gates.layer3 import SNAP_DIV, _source_mean_lumas, r38_check
from .timeline import Timeline
from .util import EngineError

SAMPLE_STEP = 5
QA_BATCH_FRAMES = 32   # sampled frames per Rust stats/motion call (bounded memory on a 90 s video)
MOTION_MIN = 0.5      # mean abs luma diff per sample pair (0-255 scale); below = frozen segment


def iter_sample_frames(video: Path, W: int, H: int, step: int = SAMPLE_STEP,
                       threads: int = 4):
    """Yield sampled RGB frames one at a time from one ffmpeg decode (never holds the whole video in memory).
    (ow, oh) is W // SNAP_DIV, H // SNAP_DIV; callers derive it from the first frame's size."""
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    cmd = [config.FFMPEG, "-v", "error", "-threads", str(threads), "-i", str(video), "-an", "-vf",
           f"select=not(mod(n\\,{step})),scale={ow}:{oh}:flags=area,format=rgb24", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-"]
    sz = ow * oh * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout
    assert isinstance(stdout, io.BufferedIOBase)
    try:
        while True:
            buf = bytearray(sz)
            view = memoryview(buf)
            got = 0
            while got < sz:
                n = stdout.readinto(view[got:])
                if not n:
                    break
                got += n
            if got < sz:
                if got:
                    raise EngineError(f"QA frame decode failed: partial RGB frame ({got}/{sz} bytes)")
                break
            yield bytes(buf)
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise EngineError(f"QA frame decode failed: {err[-600:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def sample_frames(video: Path, W: int, H: int, step: int = SAMPLE_STEP,
                  threads: int = 4) -> tuple[list[bytes], int, int]:
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    return list(iter_sample_frames(video, W, H, step, threads)), ow, oh


def pixel_gates(video: Path, tl: Timeline, step: int = SAMPLE_STEP,
                threads: int = 4, project_dir: Path | None = None) -> tuple[list[Finding], dict]:
    W, H = tl.settings.width, tl.settings.height
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    stats, motion, prev = [], [], None
    batch: list[bytes] = []

    def flush():
        nonlocal batch, prev
        if not batch:
            return
        stats.extend(core.frame_stats(batch, ow, oh))
        motion.extend(core.frame_motion(([prev] if prev is not None else []) + batch, ow, oh))
        prev, batch = batch[-1], []
    for frame in iter_sample_frames(video, W, H, step, threads):
        batch.append(frame)
        if len(batch) >= QA_BATCH_FRAMES:
            flush()
    flush()
    sources = _source_mean_lumas(tl, project_dir)
    out, per_seg = [], {}
    for s in tl.segments:
        idx = [i for i in range(len(stats)) if s.start_frame <= i * step < s.end_frame]
        if not idx:
            out.append(Finding("R38", "fail", f"no sampled frames in segment {s.start_frame}-{s.end_frame}; "
                                              "cannot verify decoded frames", s.seg_index))
            continue
        bad, space_runs = [], []
        last_space: dict[str, list] = {}
        for i in idx:
            fs = r38_check(stats[i], s.seg_index, f"frame {i * step}", s.intent,
                           source_mean=sources.get(s.seg_index), landscape=(W > H))
            for f in fs:
                if f.gate != "R38space":
                    bad.append(f)
                    continue
                # Keep one finding per contiguous run and third, with the first sample as evidence.
                detail = f.message.split(": ", 1)[1]
                third = detail.split(" third", 1)[0]
                previous = last_space.get(third)
                if previous is not None and previous[1] == i - 1:
                    previous[1], previous[2] = i, previous[2] + 1
                else:
                    previous = [f, i, 1, i, detail]
                    last_space[third] = previous
                    space_runs.append(previous)
        for f, last, count, first, detail in space_runs:
            f.frames = (first * step, min(s.end_frame, last * step + step))
            f.message = f"frames {f.frames[0]}-{f.frames[1]} ({count} samples; first sample): {detail}"
            bad.append(f)
        out += bad
        keep = set(idx)
        pairs = [motion[i] for i in idx if i < len(motion) and i + 1 in keep]
        if not pairs:
            expected = s.motion_expectation
            message = f"{len(idx)} sampled frame(s) in the segment: too few to measure motion between consecutive samples"
            if expected == "moving":
                assert s.intent is not None
                message += f"; cannot verify expected motion; intent motion=moving: {s.intent.reason}"
            out.append(Finding("R56px", "fail" if expected == "moving" else "info", message,
                               s.seg_index, frames=(s.start_frame, s.end_frame)))
            continue
        m = sum(pairs) / len(pairs)
        per_seg[s.seg_index] = round(m, 2)
        if m < MOTION_MIN:
            expected = s.motion_expectation
            level = "fail" if expected == "moving" else "info" if expected == "static" else "warn"
            evidence = (f"; intent motion={expected}: {s.intent.reason}" if s.intent and s.intent.motion
                        else "; legacy hold read" if s.hold == "read" else "; no explicit motion expectation")
            out.append(Finding("R56px", level, f"no visible motion (mean luma diff {m:.2f} < {MOTION_MIN}){evidence}",
                               s.seg_index, frames=(s.start_frame, s.end_frame)))
    if not out:
        out.append(Finding("QA", "pass", f"{len(stats)} sampled frames: no integrity failures or motion warnings"))
    return out, {"samples": len(stats), "motion": per_seg}
