"""Optional per-scene visual components (Stage 4a: P12 grid, P14 halo + stars, P17 timezones + hatch, P13 bubbles).

Every component is off unless the scene sets its field, so a scene without these fields produces the same scene JSON
as before. Values the scene leaves out come from profile [fx], and colours are normalised to lowercase here.
"""
from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path

from .util import EngineError

JS = Path(__file__).with_name("hf")


def _pick(value, default):
    return default if value is None else value


def _seed(tl, seg, explicit: int | None) -> int:
    """Explicit seed, else a stable seed from the project id and segment index."""
    if explicit is not None:
        return int(explicit)
    raw = f"{tl.project_id}:{seg.seg_index}".encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big") & 0x7FFFFFFF


def _bounds(sc) -> tuple[float, float, float, float]:
    """(south, west, north, east): the scene's map_bounds, else the whole world for graticule/zone lines."""
    b = sc.map_bounds
    return tuple(b) if b else (-90.0, -180.0, 90.0, 180.0)


def grid_data(cfg, scene: dict, sc) -> dict:
    g = sc.backdrop.grid
    h, w = scene["height"], scene["width"]
    cell = _pick(g.cell_pct, cfg.cell_pct) / 100.0 * h
    out = {"colour": _pick(g.colour, cfg.colour).lower(), "bg": _pick(g.bg, cfg.bg).lower(),
           "cell_px": round(cell, 3), "line_px": round(_pick(g.line_px, cfg.line_px) * min(w, h) / 1080.0, 3),
           "alpha": _pick(g.alpha, cfg.alpha), "graticule": bool(g.graticule)}
    if g.graticule:
        south, west, north, east = _bounds(sc)
        mw, mh = scene["map_size"]
        step = cfg.graticule_spacing_deg
        out["graticule_colour"], out["graticule_alpha"] = cfg.graticule_colour.lower(), cfg.graticule_alpha
        lines = []
        lng = math.ceil(west / step) * step
        while lng <= east + 1e-9:
            x = round((lng - west) / (east - west) * mw, 2)
            lines.append([[x, 0.0], [x, round(mh, 2)]])
            lng += step
        lat = math.ceil(south / step) * step
        while lat <= north + 1e-9:
            y = round((north - lat) / (north - south) * mh, 2)
            lines.append([[0.0, y], [round(mw, 2), y]])
            lat += step
        out["lines"] = lines
    return out


def stars_data(cfg, scene: dict, tl, seg) -> dict:
    st = scene["_stars"]
    h, w = scene["height"], scene["width"]
    seed = _seed(tl, seg, _pick(st.seed, cfg.seed))
    count = _pick(st.count, cfg.count)
    rng = random.Random(seed)
    scale = min(w, h) / 1080.0
    stars = []
    for _ in range(count):
        stars.append({"x": round(rng.random(), 6), "y": round(rng.random(), 6),
                      "r": round(rng.uniform(1.0, 2.0) * scale, 3),
                      "b": round(rng.uniform(0.35, 1.0), 3),
                      "p": round(rng.uniform(0.0, 2.0 * math.pi), 6),
                      "s": round(rng.uniform(2.5, 5.5), 3)})
    return {"count": count, "seed": seed, "twinkle": _pick(st.twinkle, cfg.twinkle),
            "parallax": _pick(st.parallax, cfg.parallax), "stars": stars}


def halo_data(cfg, halo) -> dict:
    return {"colour": _pick(halo.colour, cfg.colour).lower(), "width_pct": _pick(halo.width_pct, cfg.width_pct),
            "alpha": _pick(halo.alpha, cfg.alpha)}


def hatch_data(cfg, hatch) -> dict:
    return {"angle": _pick(hatch.angle, cfg.angle), "spacing_px": _pick(hatch.spacing_px, cfg.spacing_px),
            "line_px": _pick(hatch.line_px, cfg.line_px), "colour": _pick(hatch.colour, cfg.colour).lower(),
            "alpha": _pick(hatch.alpha, cfg.alpha)}


def timezones_data(cfg, scene: dict, sc) -> dict:
    _south, west, _north, east = _bounds(sc)
    mw, mh = scene["map_size"]
    bands = []
    for b in sc.timezones.bands:
        x0 = round((b.from_lng - west) / (east - west) * mw, 2)
        x1 = round((b.to_lng - west) / (east - west) * mw, 2)
        bands.append({"x0": x0, "x1": x1, "label": b.label,
                      "colour": _pick(b.colour, cfg.colour).lower(),
                      "lx": round((x0 + x1) / 2.0, 2), "ly": round(mh * 0.5, 2)})
    return {"bands": bands, "alpha": _pick(sc.timezones.alpha, cfg.alpha),
            "at_frame": _pick(sc.timezones.at_frame, cfg.at_frame)}


def bubbles_data(cfg, sc, seg) -> list[dict]:
    out = []
    for i, b in enumerate(sc.bubbles or []):
        a = b.anchor
        anchor = None
        if a.pin is not None:
            anchor = {"kind": "pin", "index": a.pin}
        elif a.region is not None:
            anchor = {"kind": "region", "index": a.region}
        elif a.xy is not None:
            anchor = {"kind": "xy", "x": a.xy[0], "y": a.xy[1]}
        elif a.overlay is not None:
            box = seg.overlays[a.overlay].box
            anchor = {"kind": "xy", "px": box[0] + box[2] / 2.0, "py": box[1] + box[3] / 2.0}
        if anchor is None:
            raise EngineError(f"bubbles[{i}]: anchor has no target")
        out.append({"text": b.text, "anchor": anchor, "side": b.side, "at_frame": b.at_frame,
                    "out_frame": b.out_frame, "style": _pick(b.style, cfg.style),
                    "font_px": cfg.font_px, "pop_frames": cfg.pop_frames,
                    "max_width_pct": cfg.max_width_pct, "max_bottom_pct": cfg.max_bottom_pct,
                    "gap_px": cfg.gap_px})
    return out


def scene_block(seg, tl, project_dir: Path, scene: dict, sc, prof) -> dict:
    """Scene fields for the components that are on. Empty when the scene sets none (the previous scene JSON)."""
    out: dict = {}
    if sc.backdrop:
        bg = {}
        if sc.backdrop.grid:
            bg["grid"] = grid_data(prof.fx.grid, scene, sc)
        if sc.backdrop.stars:
            bg["stars"] = stars_data(prof.fx.stars, {**scene, "_stars": sc.backdrop.stars}, tl, seg)
        if bg:
            out["backdrop"] = bg
    if sc.timezones and not sc.globe:
        out["timezones"] = timezones_data(prof.fx.timezone, scene, sc)
    if sc.bubbles:
        out["bubbles"] = bubbles_data(prof.fx.bubble, sc, seg)
    return out
