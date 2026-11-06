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
SOURCE_DARK_MEAN = 25.0       # a source asset with mean luma below this is itself dark (e.g. a night/dark map)
THIRD_NAMES = ("top", "middle", "bottom")


def r38_check(stats: tuple, seg: int | None, what: str, intent: SegmentIntent | None = None,
              source_mean: float | None = None) -> list[Finding]:
    """Judges one frame's stats. `source_mean` (mean luma of the segment's source asset, checked before the
    build judges the snapshot) keeps a genuinely dark source — a night scene or a dark map — from reading as a
    black frame: that is a warn about dark content, never an R38 black-frame failure."""
    mean, black, std, det = stats
    out = []
    dark_source = source_mean is not None and source_mean < SOURCE_DARK_MEAN
    if black > BLACK_FRAC or mean < BLACK_MEAN:
        if dark_source:
            out.append(Finding("R38", "warn",
                               f"{what}: dark snapshot, but the source is dark too (source mean luma "
                               f"{source_mean:.0f}) — dark source, not a black frame", seg))
        else:
            extra = f" (source mean luma {source_mean:.0f})" if source_mean is not None else ""
            out.append(Finding("R38", "fail", f"{what}: black frame (mean luma {mean:.0f}, {black:.0%} black pixels){extra}", seg))
        return out
    for k in range(3):
        if std[k] < EMPTY_STD and det[k] < EMPTY_DETAIL:
            intentional = intent is not None and intent.negative_space
            if intentional:
                level, reason = "info", f"; intentional negative space: {intent.reason}"
            elif dark_source:
                level, reason = "info", f"; dark source (source mean luma {source_mean:.0f})"
            else:
                level, reason = "warn", ""
            out.append(Finding("R38space", level,
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


def _source_mean_lumas(tl: Timeline, project_dir: Path | None) -> dict[int, float]:
    """Mean luma of each segment's source asset, computed BEFORE the snapshot is judged: a native segment's
    `source` image, or a HyperFrames segment's `map_asset`. Missing/unreadable/non-image sources are skipped
    (None -> r38_check keeps its plain threshold behaviour)."""
    from PIL import Image
    out: dict[int, float] = {}
    if project_dir is None:
        return out
    for s in tl.segments:
        path = s.source
        sc = s.gsap_scene_config
        if path is None and sc is not None:
            path = sc.map_asset
        if path is None:
            continue
        p = Path(project_dir) / path
        if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        try:
            with Image.open(p) as im:
                px = list(im.convert("L").resize((64, 64)).tobytes())
            if px:
                out[s.seg_index] = sum(px) / len(px)
        except (OSError, ValueError, AttributeError):
            continue
    return out


def r38_snapshots(tl: Timeline, project_dir: Path, dom_snaps: dict[int, str], save_dir: Path | None = None) -> tuple[list[Finding], dict]:
    W, H = tl.settings.width, tl.settings.height
    ow, oh = W // SNAP_DIV, H // SNAP_DIV
    try:
        snaps = snapshots(tl, project_dir, dom_snaps)
    except (EngineError, OSError) as e:
        return [Finding("R38", "fail", f"snapshot failed: {e}")], {}
    keys = sorted(snaps)
    stats = core.frame_stats([snaps[k] for k in keys], ow, oh)
    sources = _source_mean_lumas(tl, project_dir)   # luma sumber dicek sebelum build menilai snapshot
    out = []
    paths = {}
    intents = {s.seg_index: s.intent for s in tl.segments}
    for k, st in zip(keys, stats):
        out += r38_check(st, k, "snapshot", intents.get(k), source_mean=sources.get(k))
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
