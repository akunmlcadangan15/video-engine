"""`engine sheet`: contact sheet of the final render, one tile per segment at its middle frame, labelled
`seg N  mm:ss.f`. Tiles are 1/5 of the output size; 7 columns by default."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import config
from .timeline import Timeline
from .util import EngineError, probe

TILE_DIV = 5


def _seek(frame: int, fps: int) -> str:
    return f"{frame / fps:.4f}"


def frame_rgb(video: Path, frame: int, fps: int, w: int, h: int) -> bytes:
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", _seek(frame, fps), "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if r.returncode or len(r.stdout) != w * h * 3:
        raise EngineError(f"cannot read frame {frame} of {video}: {r.stderr.decode(errors='replace')[-200:]}")
    return r.stdout


def frames_rgb(video: Path, frames: list[int], fps: int, w: int, h: int) -> list[bytes]:
    """frame_rgb of every frame in `frames`, from ONE decode of `video` (it stops after the last tile). frame_rgb's
    input seek keeps the first frame whose timestamp is at or after the seek time; each branch here applies the same
    rule with a trim filter at the same time string, then the same scale to RGB24. The seek also subtracts the file's
    start time, rounded on its own, so a file that does not start at 0 takes frame_rgb per tile."""
    if not frames:
        return []
    if float(probe(video)["format"].get("start_time") or "nan") != 0.0:
        return [frame_rgb(video, f, fps, w, h) for f in frames]
    n = len(frames)
    graph = (f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n)) + ";"
             + ";".join(f"[s{i}]trim=start={_seek(f, fps)},trim=end_frame=1,scale={w}:{h}[o{i}]"
                        for i, f in enumerate(frames)))
    with tempfile.TemporaryDirectory(prefix="sheet-") as d:
        outs = [Path(d) / f"{i}.rgb" for i in range(n)]
        args = [a for i, o in enumerate(outs)
                for a in ("-map", f"[o{i}]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(o))]
        r = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(video), "-filter_complex", graph, *args],
                           capture_output=True)
        tiles = [o.read_bytes() if o.is_file() else b"" for o in outs]
    for f, t in zip(frames, tiles):
        if r.returncode or len(t) != w * h * 3:
            raise EngineError(f"cannot read frame {f} of {video}: {r.stderr.decode(errors='replace')[-200:]}")
    return tiles


def make(tl: Timeline, video: Path, out: Path, cols: int = 7) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    if cols < 1:
        raise EngineError("--cols must be at least 1")
    if not video.is_file():
        raise EngineError(f"{video} not found; build first")
    S = tl.settings
    w, h = S.width // TILE_DIV, S.height // TILE_DIV
    segs = tl.segments
    rows = -(-len(segs) // cols)
    pad, lab = 6, 26
    sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + lab + pad) + pad), (16, 18, 22))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(config.FONTS_DIR / "Inter-SemiBold.otf"), 18)
    tiles = frames_rgb(video, [(s.start_frame + s.end_frame) // 2 for s in segs], S.fps, w, h)
    for i, s in enumerate(segs):
        f = (s.start_frame + s.end_frame) // 2
        tile = Image.frombytes("RGB", (w, h), tiles[i])
        x, y = pad + (i % cols) * (w + pad), pad + (i // cols) * (h + lab + pad)
        sheet.paste(tile, (x, y + lab))
        t = f / S.fps
        kind = "HF" if s.gsap_scene_config else "N"
        draw.text((x + 2, y + 3), f"seg {s.seg_index} {kind}  {int(t // 60):02d}:{t % 60:04.1f}", fill=(235, 235, 235), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def make_from_tiles(tl: Timeline, tiles: dict[int, Path], out: Path, cols: int = 7) -> Path:
    """Contact sheet assembled from already-rendered preview PNGs (one per segment), without requiring final.mp4."""
    from PIL import Image, ImageDraw, ImageFont
    if cols < 1:
        raise EngineError("--cols must be at least 1")
    S = tl.settings
    w, h = S.width // TILE_DIV, S.height // TILE_DIV
    segs = tl.segments
    rows = -(-len(segs) // cols)
    pad, lab = 6, 26
    sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + lab + pad) + pad), (16, 18, 22))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(config.FONTS_DIR / "Inter-SemiBold.otf"), 18)
    for i, s in enumerate(segs):
        p = tiles.get(s.seg_index)
        if p and Path(p).is_file():
            with Image.open(p) as im:
                tile = im.convert("RGB").resize((w, h), Image.LANCZOS)
        else:
            tile = Image.new("RGB", (w, h), (32, 36, 44))
        x, y = pad + (i % cols) * (w + pad), pad + (i // cols) * (h + lab + pad)
        sheet.paste(tile, (x, y + lab))
        f = (s.start_frame + s.end_frame) // 2
        t = f / S.fps
        kind = "HF" if s.gsap_scene_config else "N"
        draw.text((x + 2, y + 3), f"seg {s.seg_index} {kind}  {int(t // 60):02d}:{t % 60:04.1f}", fill=(235, 235, 235), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
