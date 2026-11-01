"""Narration pause detection and pause-driven segment boundaries. Decode, energy analysis and
boundary choice run in the Rust core (`detect_pauses_file`, `suggest_boundaries`); this module
converts seconds to frames and shapes the result."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import hypoverum_core_rs as core

from . import config
from .util import EngineError

MIN_PAUSE_S = 0.20
MIN_SEG_S = 2.0
MAX_SEG_S = 6.0


@dataclass(frozen=True)
class Pause:
    start_frame: int
    end_frame: int
    mean_db: float


@dataclass(frozen=True)
class PauseReport:
    fps: int
    total_frames: int
    threshold_db: float
    pauses: list[Pause]
    boundaries: list[int]          # interior cut frames
    segments: list[tuple[int, int]]  # half-open [start, end) covering 0..total_frames

    def to_dict(self) -> dict:
        d = asdict(self)
        d["segments"] = [list(s) for s in self.segments]
        return d


def analyze(path: Path, fps: int, *, threshold_db: float | None = None, min_pause_s: float = MIN_PAUSE_S,
            min_seg_s: float = MIN_SEG_S, max_seg_s: float = MAX_SEG_S) -> PauseReport:
    """Pauses (>= min_pause_s, below threshold_db or adaptive) and suggested segments within
    [min_seg_s, max_seg_s] where pauses allow."""
    try:
        min_pause = max(1, core.sec_to_frame(min_pause_s, fps))
        min_len = max(1, core.sec_to_frame(min_seg_s, fps))
        max_len = max(min_len, core.sec_to_frame(max_seg_s, fps))
        rows, thr, total = core.detect_pauses_file(path, fps, threshold_db, min_pause, 16000, config.FFMPEG)
        if total < 1:
            raise EngineError(f"{path}: no audio decoded")
        cuts = core.suggest_boundaries(rows, total, min_len, max_len)
    except (ValueError, OSError) as e:
        raise EngineError(f"pauses: {e}") from e
    edges = [0, *cuts, total]
    return PauseReport(fps, total, round(thr, 2), [Pause(a, b, round(db, 2)) for a, b, db in rows], cuts,
                       list(zip(edges, edges[1:])))


def speech_start_frame(path: Path, fps: int) -> int:
    """First frame of speech: end of a detected pause that starts at frame 0 (adaptive threshold), else 0."""
    try:
        rows, _thr, total = core.detect_pauses_file(path, fps, None, 1, 16000, config.FFMPEG)
    except (ValueError, OSError) as e:
        raise EngineError(f"pauses: {e}") from e
    if total < 1:
        raise EngineError(f"{path}: no audio decoded")
    return int(rows[0][1]) if rows and rows[0][0] == 0 else 0
