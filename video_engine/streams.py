"""Stream parameters and the one rule for every stream-copy and concat-demuxer path of the engine.

Paths that join or copy compressed packets: `native.render_copy` (source -> segment, `-c copy`), the final mux
concat demuxer (`audio.final_mux`, segments -> one stream), `audio.apply_gain` (final video stream, `-c:v copy`).
Each one goes through `mismatch`: packets of two streams are interchangeable only when every parameter in
`VIDEO_KEYS` / `AUDIO_KEYS` is identical, time base and codec extradata (H.264 SPS/PPS) included.

- `params(path, select)`: the parameter dicts of a file's streams (ffprobe, extradata as an MD5 hash).
- `mismatch(a, b)`: list of differing parameters ("v0.time_base: 1/15360 != 1/90000"); empty = copy-compatible.
- `joinable(paths)`: (True, []) when every file has the parameters of the first, else (False, differences).
- `engine_format(W, H, fps)`: parameters of the engine's own segment encoder (raw RGB frames -> config.X264_SEGMENT), the
  target a native stream copy must match exactly. Encoded once per process and size.
- `verify(path, n, fps, what)`: raises EngineError unless the file's first video stream has exactly `n` frames,
  starts at 0 and lasts n / fps (both within half a frame).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from . import config
from .util import EngineError, run

VIDEO_KEYS = ("codec_name", "codec_tag_string", "profile", "level", "width", "height", "pix_fmt",
              "sample_aspect_ratio", "r_frame_rate", "avg_frame_rate", "time_base", "field_order", "color_range",
              "color_space", "color_transfer", "color_primaries", "chroma_location", "bits_per_raw_sample",
              "has_b_frames", "refs", "extradata_hash", "rotation")
AUDIO_KEYS = ("codec_name", "codec_tag_string", "profile", "sample_rate", "channels", "channel_layout", "sample_fmt",
              "bits_per_raw_sample", "time_base", "extradata_hash")
KEYS = {"video": VIDEO_KEYS, "audio": AUDIO_KEYS}


@lru_cache(maxsize=512)
def _params(path: str, stamp: tuple, select: str | None) -> tuple:
    cmd = [config.FFPROBE, "-v", "error", "-show_data_hash", "MD5", "-show_streams", "-print_format", "json"]
    if select:
        cmd += ["-select_streams", select]
    out = []
    for s in json.loads(run([*cmd, path])).get("streams", []):
        kind = s.get("codec_type")
        rot = next((sd["rotation"] for sd in s.get("side_data_list", []) or [] if "rotation" in sd), 0)
        d = {"codec_type": kind, **{k: s.get(k) for k in KEYS.get(kind, ())}}
        if kind == "video":
            d["rotation"] = rot
        out.append(tuple(sorted(d.items(), key=lambda kv: kv[0])))
    return tuple(out)


def params(path: str | Path, select: str | None = None) -> list[dict]:
    """Parameter dict of every stream of `path` (ffprobe stream specifier `select`, e.g. "v:0"), in stream order."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as e:
        raise EngineError(f"cannot inspect {p}: {e}") from None
    return [dict(s) for s in _params(str(p), (st.st_size, st.st_mtime_ns, st.st_ino), select)]


def mismatch(a: list[dict], b: list[dict]) -> list[str]:
    """Every parameter in which stream list `a` differs from `b`; empty when they are copy-compatible."""
    if [s["codec_type"] for s in a] != [s["codec_type"] for s in b]:
        return [f"streams: {[s['codec_type'] for s in a]} != {[s['codec_type'] for s in b]}"]
    out = []
    for i, (x, y) in enumerate(zip(a, b)):
        out += [f"{x['codec_type'][0]}{i}.{k}: {x.get(k)} != {y.get(k)}" for k in KEYS.get(x["codec_type"], ())
                if x.get(k) != y.get(k)]
    return out


def joinable(paths: list[Path]) -> tuple[bool, list[str]]:
    """(True, []) when every file has exactly the stream parameters of the first; else (False, differences)."""
    if not paths:
        return True, []
    ref = params(paths[0])
    diffs = [f"{Path(p).name}: {d}" for p in paths[1:] for d in mismatch(params(p), ref)]
    return not diffs, diffs


@lru_cache(maxsize=16)
def _engine_format(W: int, H: int, fps: int, x264: tuple, ffmpeg: str) -> tuple:
    with tempfile.TemporaryDirectory(prefix="engine-format-") as d:
        out = Path(d) / "ref.mp4"
        frames = bytes(W * H * 3) * 2
        p = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(fps), "-i", "-", *x264, "-frames:v", "2", "-an", "-movflags", "+faststart",
                            str(out)], input=frames, capture_output=True)
        if p.returncode != 0:
            raise EngineError(f"engine format probe failed: {p.stderr.decode(errors='replace')[-400:]}")
        return tuple(tuple(sorted(s.items())) for s in params(out, "v:0"))


def engine_format(W: int, H: int, fps: int, x264: list[str] | None = None) -> list[dict]:
    """Video stream parameters of the engine's segment encoder (`x264`, default config.X264_SEGMENT) at W x H, fps
    (see module docstring)."""
    return [dict(s) for s in _engine_format(W, H, fps, tuple(x264 or config.X264_SEGMENT), str(config.FFMPEG))]


@lru_cache(maxsize=512)
def _video_timing(path: str, stamp: tuple) -> tuple[int, float, float]:
    """(packet frame count, start_time, duration) in one ffprobe call, memoised by path content stamp."""
    try:
        s = json.loads(run([config.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                            "stream=nb_read_packets,start_time,duration", "-print_format", "json", path]))["streams"][0]
        return int(s.get("nb_read_packets") or 0), float(s.get("start_time") or 0.0), float(s.get("duration") or -1.0)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
        raise EngineError(f"ffprobe timing of {path} is unusable: {e}") from None


def verify(path: str | Path, n: int, fps: int, what: str) -> None:
    """Raise EngineError unless the first video stream of `path` has n frames, starts at 0 and lasts n / fps. One
    ffprobe per artifact: the timing is memoised by (size, mtime, inode) and reused by every later verify."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as e:
        raise EngineError(f"{what}: cannot stat {p}: {e}") from None
    got, start, dur = _video_timing(str(p), (st.st_size, st.st_mtime_ns, st.st_ino))
    half = 0.5 / fps
    if got != n or abs(start) > half or abs(dur - n / fps) > half:
        raise EngineError(f"{what}: {got} frames from {start:.6f} s lasting {dur:.6f} s, expected {n} frames from 0 "
                          f"lasting {n / fps:.6f} s")
