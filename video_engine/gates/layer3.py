"""Layer 3 visual snapshot gates: R38 (black/decode integrity), R38space (negative-space advice) on one snapshot per segment, computed in Rust.
Snapshots: native segments from the camera math at 60% of the segment (one source frame, warped at 1/4 size);
HyperFrames segments from the R63 DOM probe screenshot. Optional AI QA (`--ai-qa`, any AI provider) runs on the same snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hypoverum_core_rs as core

from .. import native
from ..timeline import NATIVE, SegmentIntent, Timeline
from ..util import EngineError
from .core import Finding

SNAP_DIV = 4                  # snapshot size = output size / SNAP_DIV
EMPTY_STD = 6.0               # a third is empty when luma std < EMPTY_STD and edge detail < EMPTY_DETAIL
EMPTY_DETAIL = 1.5
BLACK_FRAC = 0.85             # black frame: > BLACK_FRAC of pixels with luma < 20, or mean luma < BLACK_MEAN
BLACK_MEAN = 12.0
THIRD_NAMES = ("top", "middle", "bottom")


def r38_check(stats: tuple, seg: int | None, what: str, intent: SegmentIntent | None = None) -> list[Finding]:
    mean, black, std, det = stats
    out = []
    if black > BLACK_FRAC or mean < BLACK_MEAN:
        out.append(Finding("R38", "fail", f"{what}: black frame (mean luma {mean:.0f}, {black:.0%} black pixels)", seg))
        return out
    for k in range(3):
        if std[k] < EMPTY_STD and det[k] < EMPTY_DETAIL:
            intentional = intent is not None and intent.negative_space
            reason = f"; intentional negative space: {intent.reason}" if intentional else ""
            out.append(Finding("R38space", "info" if intentional else "warn",
                               f"{what}: {THIRD_NAMES[k]} third empty (luma std {std[k]:.1f}, detail {det[k]:.2f}){reason}", seg))
    return out


def snapshots(tl: Timeline, project_dir: Path, dom_snaps: dict[int, str]) -> dict[int, bytes]:
    from PIL import Image
    W, H = tl.settings.width, tl.settings.height
    ow, oh = W // SNAP_DIV, H // SNAP_DIV

    def one(s):
        if s.engine_type == NATIVE:
            if not (project_dir / s.source).is_file():
                return s.seg_index, None
            job = native.plan(s, tl, project_dir)
            return s.seg_index, native.snapshot_rgb(job, tl, round(s.n_frames * 0.6), ow, oh)
        p = dom_snaps.get(s.seg_index)
        if not p:
            return s.seg_index, None
        with Image.open(p) as im:
            return s.seg_index, im.convert("RGB").resize((ow, oh), Image.BILINEAR).tobytes()

    with ThreadPoolExecutor(max_workers=4) as ex:
        return {i: b for i, b in ex.map(one, tl.segments) if b}


def r38_snapshots(tl: Timeline, project_dir: Path, dom_snaps: dict[int, str], save_dir: Path | None = None) -> tuple[list[Finding], dict]:
    W, H = tl.settings.width, tl.settings.height
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    try:
        snaps = snapshots(tl, project_dir, dom_snaps)
    except (EngineError, OSError) as e:
        return [Finding("R38", "fail", f"snapshot failed: {e}")], {}
    keys = sorted(snaps)
    stats = core.frame_stats([snaps[k] for k in keys], ow, oh)
    out = []
    paths = {}
    intents = {s.seg_index: s.intent for s in tl.segments}
    for k, st in zip(keys, stats):
        out += r38_check(st, k, "snapshot", intents.get(k))
        if save_dir:
            from PIL import Image
            save_dir.mkdir(parents=True, exist_ok=True)
            p = save_dir / f"seg{k:03d}.png"
            Image.frombytes("RGB", (ow, oh), snaps[k]).save(p)
            paths[k] = str(p)
    missing = [s.seg_index for s in tl.segments if s.seg_index not in snaps]
    for m in missing:
        out.append(Finding("R38", "skip", "no snapshot", m))
    return out or [Finding("R38", "pass", f"{len(keys)} snapshots: no empty third, no black frame")], paths
