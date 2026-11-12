"""Parallax layers (gsap_scene_config.layers): repeating full-frame images over the map, each at its own depth.

`scene_layers` builds the scene block hf/parallax.js renders; `state` is its Python mirror. With camera (x, y, s) =
layout.camera_at(scene camera, f), frame-centre map point c(f) = ((W/2 - x)/s, (H/2 - y)/s), frame centre C, depth d,
tile width w0 at frame 0 and drift v (px/s at frame 0 scale), frame f at t = f / fps shows the tile grid with
    k(f)   = (s(f) / s(0)) ^ (1 + d)
    origin = C - s(0) k(f) (1 + d) (c(f) - c(0)) + k(f) v t       tile width = w0 k(f)
(d = 0: the grid is fixed to the map). Layers draw over the map and the map props, under the shade and the texts; in
a tilted scene they sit in the tilted layer.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .timeline import Segment, Timeline
from .util import EngineError, blake3_files

JS = Path(__file__).parent / "hf" / "parallax.js"


def asset_name(src: Path) -> str:
    return f"layer_{blake3_files([src])[0][:32]}{src.suffix.lower()}"


def sources(seg: Segment, project_dir: Path) -> dict[str, Path]:
    sc = seg.gsap_scene_config
    assert sc is not None
    return {asset_name(project_dir / L.asset): project_dir / L.asset for L in sc.layers or []}


def scene_layers(seg: Segment, tl: Timeline, project_dir: Path) -> list[dict]:
    sc = seg.gsap_scene_config
    assert sc is not None
    out = []
    for i, L in enumerate(sc.layers or []):
        src = project_dir / L.asset
        if not src.is_file():
            raise EngineError(f"layer '{L.asset}' not found")
        with Image.open(src) as im:
            w, h = im.size
        tw = L.tile_px or float(w)
        out.append({"id": f"layer:{i}", "img": "assets/" + asset_name(src), "d": L.depth, "w": round(tw, 3),
                    "h": round(tw * h / w, 3), "op": L.opacity, "blend": L.blend, "v": list(L.drift)})
    return out


def state(e: dict, cam: dict, f: float, fps: int, W: int, H: int) -> dict:
    """Tile grid of layer e at frame f: origin (x, y) and tile size (w, h) in screen px (flat picture)."""
    from . import layout
    c, r = layout.camera_at(cam, f), cam["start"]
    k = (c["s"] / r["s"]) ** (1 + e["d"])
    cx, cy = (W / 2 - c["x"]) / c["s"], (H / 2 - c["y"]) / c["s"]
    rx, ry = (W / 2 - r["x"]) / r["s"], (H / 2 - r["y"]) / r["s"]
    t = f / fps
    return {"x": W / 2 - r["s"] * k * (1 + e["d"]) * (cx - rx) + k * e["v"][0] * t,
            "y": H / 2 - r["s"] * k * (1 + e["d"]) * (cy - ry) + k * e["v"][1] * t,
            "w": e["w"] * k, "h": e["h"] * k}
