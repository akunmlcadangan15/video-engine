"""`engine sheet`: contact sheet of the final render, one tile per segment at its middle frame, labelled
`seg N  mm:ss.f`. Tiles are 1/5 of the output size; 7 columns by default."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config
from .timeline import Timeline
from .util import EngineError

TILE_DIV = 5


def frame_rgb(video: Path, frame: int, fps: int, w: int, h: int) -> bytes:
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{frame / fps:.4f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if r.returncode or len(r.stdout) != w * h * 3:
        raise EngineError(f"cannot read frame {frame} of {video}: {r.stderr.decode(errors='replace')[-200:]}")
    return r.stdout


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
    for i, s in enumerate(segs):
        f = (s.start_frame + s.end_frame) // 2
        tile = Image.frombytes("RGB", (w, h), frame_rgb(video, f, S.fps, w, h))
        x, y = pad + (i % cols) * (w + pad), pad + (i // cols) * (h + lab + pad)
        sheet.paste(tile, (x, y + lab))
        t = f / S.fps
        kind = "HF" if s.gsap_scene_config else "N"
        draw.text((x + 2, y + 3), f"seg {s.seg_index} {kind}  {int(t // 60):02d}:{t % 60:04.1f}", fill=(235, 235, 235), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
