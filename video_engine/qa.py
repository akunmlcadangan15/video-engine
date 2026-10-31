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

SAMPLE_STEP = 5
MOTION_MIN = 0.5      # mean abs luma diff per sample pair (0-255 scale); below = frozen segment


def sample_frames(video: Path, W: int, H: int, step: int = SAMPLE_STEP,
                  threads: int = 4) -> tuple[list[bytes], int, int]:
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    cmd = [config.FFMPEG, "-v", "error", "-threads", str(threads), "-i", str(video), "-an", "-vf",
           f"select=not(mod(n\\,{step})),scale={ow}:{oh}:flags=area,format=rgb24", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    sz = ow * oh * 3
    return [raw[i:i + sz] for i in range(0, len(raw) - sz + 1, sz)], ow, oh


def pixel_gates(video: Path, tl: Timeline, step: int = SAMPLE_STEP,
                threads: int = 4) -> tuple[list[Finding], dict]:
    W, H = tl.settings.width, tl.settings.height
    frames, ow, oh = sample_frames(video, W, H, step, threads)
    stats = core.frame_stats(frames, ow, oh)
    motion = core.frame_motion(frames, ow, oh)
    out, per_seg = [], {}
    for s in tl.segments:
        idx = [i for i in range(len(frames)) if s.start_frame <= i * step < s.end_frame]
        if not idx:
            continue
        bad = []
        for i in idx:
            fs = r38_check(stats[i], s.seg_index, f"frame {i * step}")
            if fs:
                bad += fs
        out += bad[:2]
        pairs = [motion[i] for i in idx[:-1] if i < len(motion)]
        m = sum(pairs) / len(pairs) if pairs else 0.0
        per_seg[s.seg_index] = round(m, 2)
        if m < MOTION_MIN:
            out.append(Finding("R56px", "fail", f"no visible motion (mean luma diff {m:.2f} < {MOTION_MIN})", s.seg_index))
    if not out:
        out.append(Finding("QA", "pass", f"{len(frames)} sampled frames: no empty third, no black frame, motion in every segment"))
    return out, {"samples": len(frames), "motion": per_seg}
