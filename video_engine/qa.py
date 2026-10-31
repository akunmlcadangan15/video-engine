"""Post-render pixel gates on sampled frames of the final video (in memory, Rust stats):
R38 (no empty third, no black frame) on each sample; R56px visible motion per segment (mean abs luma difference
between consecutive samples). Samples: every SAMPLE_STEP-th frame at 1/4 size."""
from __future__ import annotations

import subprocess
from pathlib import Path

import hypoverum_core_rs as core

from . import config
from .gates.core import Finding
from .gates.layer3 import SNAP_DIV, r38_check
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
    try:
        while True:
            buf = bytearray(sz)
            view = memoryview(buf)
            got = 0
            while got < sz:
                n = proc.stdout.readinto(view[got:])
                if not n:
                    break
                got += n
            if got < sz:
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
                threads: int = 4) -> tuple[list[Finding], dict]:
    W, H = tl.settings.width, tl.settings.height
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    stats, motion, batch, prev = [], [], [], None

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
    out, per_seg = [], {}
    for s in tl.segments:
        idx = [i for i in range(len(stats)) if s.start_frame <= i * step < s.end_frame]
        if not idx:
            out.append(Finding("R56px", "fail", f"no sampled frames in segment {s.start_frame}-{s.end_frame}; "
                                                "cannot check for motion", s.seg_index))
            continue
        bad = []
        for i in idx:
            fs = r38_check(stats[i], s.seg_index, f"frame {i * step}")
            if fs:
                bad += fs
        out += bad
        keep = set(idx)
        pairs = [motion[i] for i in idx if i < len(motion) and i + 1 in keep]
        if not pairs:
            out.append(Finding("R56px", "info", f"{len(idx)} sampled frame(s) in the segment: too few to measure "
                                                "motion between consecutive samples", s.seg_index))
            continue
        m = sum(pairs) / len(pairs)
        per_seg[s.seg_index] = round(m, 2)
        if m < MOTION_MIN:
            out.append(Finding("R56px", "fail", f"no visible motion (mean luma diff {m:.2f} < {MOTION_MIN})", s.seg_index))
    if not out:
        out.append(Finding("QA", "pass", f"{len(stats)} sampled frames: no empty third, no black frame, motion in every segment"))
    return out, {"samples": len(stats), "motion": per_seg}
