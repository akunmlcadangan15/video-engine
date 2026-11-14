"""Type stubs for hypoverum_core_rs (native core of the video engine).

Every function raises ValueError on invalid input; none raises PanicException.
Frames are half-open [start, end), integers in 0 <= frame < 2**40. fps is 1..=99.
"""

import os
import pathlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

ALGO_VERSION: str
"""Bumped on any output-changing algorithm edit; part of every render cache key."""

Window = tuple[float, float, float, float]
"""(x, y, w, h) in source pixels; finite, w > 0, h > 0."""

def version() -> str: ...
def set_threads(n: int) -> int:
    """Size the global rayon pool once (1..=256, first call wins). Returns the size in effect."""

def sec_to_frame(seconds: float, fps: int) -> int:
    """round(seconds*fps) clamped >= 0. ValueError: non-finite seconds, fps outside 1..=99, frame >= 2**40."""

def blake3_bytes(data: bytes) -> str: ...
def blake3_files(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    """Hex BLAKE3 per path, same order. FileNotFoundError / OSError on I/O; TypeError on a single path."""

def compile_ass(
    words: Sequence[tuple[str, int, int, bool]],
    width: int,
    height: int,
    fps: int,
    style: Mapping[str, Any] | None = None,
    y_overrides: Sequence[tuple[int, int, float]] | None = None,
) -> tuple[str, list[tuple[int, int, str, float, float, float, float]]]:
    """words = (text, start_frame, end_frame, highlight), sorted by start, 0 <= start < end < 2**40.

    Returns (ass_text, events) with events = (start, end, text, font_size, est_width, x, y).
    ValueError: bad frames/order, fps, size, unknown or invalid style key.
    """

def caption_slots() -> list[tuple[str, float]]: ...
def camera_presets() -> list[str]: ...
def camera_plan(
    preset: str,
    intensity: float,
    src_w: float,
    src_h: float,
    out_w: float,
    out_h: float,
    focus_box: Sequence[float] | None = None,
) -> tuple[Window, Window, float]:
    """(start, end, motion_px). focus_box = (ymin, xmin, ymax, xmax) on the 0..=1000 grid, finite, min < max."""

def camera_windows(start: Window, end: Window, n: int) -> list[Window]: ...
def warp_frame(
    src: bytes, src_w: int, src_h: int, win: Window, out_w: int, out_h: int
) -> bytes:
    """Bilinear crop+scale of RGB24. Sizes 1..=32768, len(src) == src_w*src_h*3."""

def warp_still_to_fd(
    src: bytes, src_w: int, src_h: int, wins: list[Window], out_w: int, out_h: int, fd: int
) -> None:
    """Warp one RGB24 still across `wins` and stream to file descriptor `fd` (GIL released)."""

def warp_stream_to_fd(
    in_fd: int, out_fd: int, src_w: int, src_h: int, wins: list[Window], out_w: int, out_h: int
) -> int:
    """Stream RGB24 frames from `in_fd`, warp over `wins`, write to `out_fd`; returns frames written."""

def frame_stats(
    frames: Iterable[bytes], width: int, height: int
) -> list[tuple[float, float, list[float], list[float]]]:
    """(mean_luma, black_frac, third_std[3], third_detail[3]) per frame; width, height >= 4."""

def frame_motion(frames: Iterable[bytes], width: int, height: int) -> list[float]: ...
def enclosed_holes(mask: bytes, width: int, height: int) -> bytes:
    """Enclosed 4-connected background holes (`mask[i] == 0` pixels unreachable from the border) as 0/1 bytes."""

def keep_components_min_px(mask: bytes, width: int, height: int, min_px: int) -> bytes:
    """Retain 4-connected foreground components (`mask[i] != 0`) with at least `min_px` pixels as 0/1 bytes."""

PauseRow = tuple[int, int, float]  # (start_frame, end_frame) half-open, mean dBFS

def make_proxy(
    src: str | os.PathLike[str],
    out: str | os.PathLike[str],
    height: int = 360,
    fps: int = 5,
    start_s: float = 0.0,
    seconds: float | None = None,
    crf: int = 30,
    threads: int = 2,
    ffmpeg: str = "ffmpeg",
) -> pathlib.Path:
    """ffmpeg proxy spawned from Rust; writes <out>.part then renames. height even 64..=1080, fps 1..=30.
    ValueError: spec. FileNotFoundError: src. OSError: ffmpeg failure (no partial file left)."""

def detect_pauses_pcm(
    pcm: bytes, rate: int, fps: int, threshold_db: float | None = None, min_frames: int = 6
) -> tuple[list[PauseRow], float]:
    """Mono s16le PCM -> (pauses, threshold_db used). threshold None = adaptive (p90 - 24 dB, <= -30)."""

def detect_pauses_file(
    path: str | os.PathLike[str],
    fps: int,
    threshold_db: float | None = None,
    min_frames: int = 6,
    rate: int = 16000,
    ffmpeg: str = "ffmpeg",
) -> tuple[list[PauseRow], float, int]:
    """ffmpeg decode (from Rust) + detect: (pauses, threshold_db, total_frames)."""

def suggest_boundaries(pauses: list[PauseRow], total: int, min_len: int, max_len: int) -> list[int]:
    """Interior cut frames at pause midpoints; longest pause in [prev+min_len, prev+max_len] wins;
    forced cut at prev+max_len when none; a tail shorter than min_len merges back."""
