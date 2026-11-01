"""Optional per-scene visual components (P12 grid, P13 bubbles, P14 halo + stars, P15 flag characters, P16 cutaway +
insets, P17 timezones + hatch, P18 geoid, P19 particles).

Every component is off unless the scene sets its field, so a scene without these fields produces the same scene JSON
as before. Values the scene leaves out come from profile [fx], colours are normalised to lowercase, assets go through
video_engine/assets.py so ensure_credits adds their credit automatically, and all randomness is seeded from the project
id and segment index (or the scene's own seed).
"""
from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path

from PIL import Image

from . import assets
from .timeline import Segment, Timeline, sec_to_frame
from .util import EngineError

JS = Path(__file__).with_name("hf")
FX2_JS = JS / "fx2.js"


def _pick(value, default):
    return default if value is None else value


def _seed(tl, seg, explicit: int | None = None) -> int:
    """Explicit seed, else a stable seed from the project id and segment index."""
    if explicit is not None:
        return int(explicit) & 0x7FFFFFFF
    raw = f"{tl.project_id}:{seg.seg_index}".encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big") & 0x7FFFFFFF


def _lcg(seed: int):
    """32-bit LCG shared with hf/fx2.js; every draw is an exact integer operation."""
    a = seed & 0xFFFFFFFF

    def draw() -> float:
        nonlocal a
        a = (1664525 * a + 1013904223) & 0xFFFFFFFF
        return a / 4294967296.0

    return draw


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


def flag_characters(seg: Segment, tl: Timeline, project_dir: Path, pins: list[dict]) -> list[dict]:
    """Scene block of a flag character: SVG href, screen centre, size, look direction and seeded wave/blink values."""
    from . import profiles
    prof = profiles.of(tl)
    cfg = prof.fx.flag_character
    W, H, fps = tl.settings.width, tl.settings.height, tl.settings.fps
    base = _seed(tl, seg)
    out = []
    for i, c in enumerate(seg.gsap_scene_config.flag_characters or []):
        asset = assets.resolve(assets.flag_for(c.flag.code, c.flag.variant), project=project_dir, kind="flag")
        h = c.height_pct / 100.0 * H
        w = h * (4.0 / 3.0 if c.flag.variant == "4x3" else 1.0)
        cx, cy = c.xy[0] * W, c.xy[1] * H
        look, look_pin = [0.0, 0.0], None
        if c.eyes == "look_left":
            look = [-1.0, 0.0]
        elif c.eyes == "look_right":
            look = [1.0, 0.0]
        elif c.eyes.startswith("look_at:"):
            look_pin = int(c.eyes.split(":", 1)[1])   # pins move with the camera: fx2.js aims the eyes every frame
            p = pins[look_pin]
            dx, dy = p["px"] - cx, p["py"] - cy
            d = math.hypot(dx, dy) or 1.0
            look = [dx / d, dy / d]
        out.append({"id": f"flagchar:{i}", "href": f"assets/{asset.sha256[:16]}.svg",
                    "x": round(cx, 2), "y": round(cy, 2), "w": round(w, 2), "h": round(h, 2),
                    "at_frame": c.at_frame, "out_frame": c.out_frame if c.out_frame is not None else seg.n_frames,
                    "wave": c.wave, "look": [round(look[0], 5), round(look[1], 5)],
                    **({"look_pin": look_pin} if look_pin is not None else {}),
                    "seed": (base + i * 7919) & 0x7FFFFFFF,
                    "wave_amp": round(cfg.wave_amplitude * h, 3), "wave_hz": cfg.wave_hz, "slices": cfg.slices,
                    "eye_px": round(cfg.eye_pct / 100.0 * h, 3),
                    "blink_period_f": round(cfg.blink_period_s * fps, 6), "blink_frames": cfg.blink_frames,
                    "shadow_px": round(cfg.shadow_px * min(W / 1080.0, H / 1920.0), 3)})
    return out


def cutaway_block(seg: Segment, tl: Timeline, prof) -> dict:
    """Scene block of a stacked cross-section: absolute box, per-layer bands and beside-labels."""
    cfg = prof.fx.cutaway
    W, H, fps = tl.settings.width, tl.settings.height, tl.settings.fps
    c = seg.gsap_scene_config.cutaway
    k = min(W / 1080.0, H / 1920.0)
    width = c.width_pct / 100.0 * W
    height = width * cfg.height_ratio
    total = sum(layer.thickness for layer in c.layers)
    cx, cy = c.xy[0] * W, c.xy[1] * H
    x0, y0 = cx - width / 2.0, cy - height / 2.0
    gap, label_px = 24.0 * k, cfg.label_px * k
    label_w = max(len(layer.label) for layer in c.layers) * label_px * 0.62 + 20.0 * k
    right = x0 + width + gap + label_w <= W - 24.0 * k
    side = "right" if right else "left"
    x = x0 if right else x0 - gap - label_w
    layers, y = [], 0.0
    for layer in c.layers:
        h = height * layer.thickness / total
        colour = layer.colour.lower()
        layers.append({"label": layer.label, "colour": colour, "y": round(y, 3), "h": round(h, 3), "ink": "#ffffff",
                       "ly": round(y + h / 2.0, 3)})
        y += h
    return {"x": round(x, 2), "y": round(y0, 2), "w": round(width + gap + label_w, 2), "h": round(height, 2),
            "diagram": [round(x0, 2), round(y0, 2), round(width, 2), round(height, 2)],
            "side": side, "gap": round(gap, 3), "label_px": round(label_px, 3), "line_px": round(cfg.line_px * k, 3),
            "layers": layers, "at_frame": c.at_frame,
            "reveal_f": max(1, sec_to_frame(c.reveal_s if c.reveal_s is not None else cfg.reveal_s, fps))}


def inset_name(asset) -> str:
    return f"inset_{asset.sha256[:16]}{asset.path.suffix.lower()}"


def inset_blocks(seg: Segment, tl: Timeline, project_dir: Path, pins: list[dict]) -> list[dict]:
    """Scene block of framed photo insets with a leader line to their pin or screen point."""
    from . import profiles
    prof = profiles.of(tl)
    cfg = prof.fx.cutaway
    W, H, fps = tl.settings.width, tl.settings.height, tl.settings.fps
    k = min(W / 1080.0, H / 1920.0)
    margin, gap = 48.0 * k, cfg.inset_gap_px * k
    out = []
    for i, ins in enumerate(seg.gsap_scene_config.insets or []):
        try:
            asset = assets.resolve(ins.asset, project=project_dir, kind="image")
        except EngineError as exc:
            raise EngineError(f"insets[{i}]: {exc}") from exc
        with Image.open(asset.path) as im:
            iw, ih = im.size
        w = ins.size_pct / 100.0 * W
        h = w * ih / iw
        if h > 0.5 * H:
            h = 0.5 * H
            w = h * iw / ih
        if ins.anchor.pin is not None:
            p = pins[ins.anchor.pin]
            ax, ay = p["px"], p["py"]
        else:
            ax, ay = ins.anchor.xy[0] * W, ins.anchor.xy[1] * H
        side = -1.0 if ax > W / 2.0 else 1.0
        cx = ax + side * (w / 2.0 + gap)
        cy = ay - h / 2.0 - gap
        cx = min(max(cx, margin + w / 2.0), W - margin - w / 2.0)
        cy = min(max(cy, margin + h / 2.0), H - margin - h / 2.0)
        lx = min(max(ax, cx - w / 2.0), cx + w / 2.0)
        ly = min(max(ay, cy - h / 2.0), cy + h / 2.0)
        if lx == ax and ly == ay:   # anchor inside the frame: use the nearest edge
            pts = [(cx - w / 2.0, ay), (cx + w / 2.0, ay), (ax, cy - h / 2.0), (ax, cy + h / 2.0)]
            lx, ly = min(pts, key=lambda q: math.hypot(q[0] - ax, q[1] - ay))
        out.append({"id": f"inset:{i}", "img": f"assets/{inset_name(asset)}",
                    "x": round(cx, 2), "y": round(cy, 2), "w": round(w, 2), "h": round(h, 2),
                    "ax": round(ax, 2), "ay": round(ay, 2), "lx": round(lx, 2), "ly": round(ly, 2),
                    "at_frame": ins.at_frame, "out_frame": ins.out_frame if ins.out_frame is not None else seg.n_frames,
                    "border_px": round(cfg.inset_border_px * k, 3), "fade_f": max(1, round(0.2 * fps)),
                    **({"pin": ins.anchor.pin, "gap": round(gap, 3), "margin": round(margin, 3)}
                       if ins.anchor.pin is not None else {})})
    return out


def sources(seg: Segment, project_dir: Path) -> dict[str, Path]:
    """Build-dir name -> source image of every inset of a segment."""
    out = {}
    for ins in seg.gsap_scene_config.insets or []:
        asset = assets.resolve(ins.asset, project=project_dir, kind="image")
        out[inset_name(asset)] = asset.path
    return out


def _harmonics(seed: int, count: int) -> list[dict]:
    """Seeded smooth waves over the sphere: sum(a * sin(n * angle(v, v_i) + phase))."""
    rnd = _lcg(seed)
    out = []
    for k in range(count):
        z = 2.0 * rnd() - 1.0
        a = 2.0 * math.pi * rnd()
        r = math.sqrt(max(0.0, 1.0 - z * z))
        out.append({"v": [round(r * math.cos(a), 6), round(z, 6), round(r * math.sin(a), 6)],
                    "n": round(1.5 + 4.5 * rnd(), 6), "p": round(2.0 * math.pi * rnd(), 6),
                    "a": 1.0 / (k + 1) ** 0.6})
    total = sum(h["a"] for h in out) or 1.0
    for h in out:
        h["a"] = round(h["a"] / total, 6)
    return out


def geoid_height(harmonics: list[dict], lat: float, lng: float) -> float:
    """Normalised displacement (-1..1) at (lat, lng); the same formula hf/globe.js applies per vertex."""
    a, b = math.radians(lat), math.radians(lng)
    d = (math.cos(a) * math.cos(b), math.sin(a), -math.cos(a) * math.sin(b))
    s = 0.0
    for h in harmonics:
        dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(d, h["v"]))))
        s += h["a"] * math.sin(h["n"] * math.acos(dot) + h["p"])
    return max(-1.0, min(1.0, s))


def geoid_block(seg: Segment, tl: Timeline, prof) -> dict:
    """Scene block of the exaggerated Earth: seeded harmonics, ramp, rotation and fade."""
    cfg = prof.fx.geoid
    g = seg.gsap_scene_config.geoid
    seed = _seed(tl, seg)
    return {"exaggeration": g.exaggeration if g.exaggeration is not None else cfg.exaggeration,
            "colour_ramp": [c.lower() for c in (g.colour_ramp or cfg.colour_ramp)],
            "rotate_deg_s": g.rotate_deg_s if g.rotate_deg_s is not None else cfg.rotate_deg_s,
            "at_frame": g.at_frame, "seed": seed, "harmonics": _harmonics(seed, cfg.harmonics),
            "fade_f": max(0, round(cfg.fade_s * tl.settings.fps))}


def _emitter(p, i: int, tl: Timeline, seg: Segment, prof) -> dict:
    cfg = prof.fx.particles
    W, H, fps = tl.settings.width, tl.settings.height, tl.settings.fps
    seed = (_seed(tl, seg, p.seed) + i * 2654435761) & 0x7FFFFFFF
    duration = p.duration_s if p.duration_s is not None else cfg.duration_s
    count = min(cfg.max_count, int((70 if p.kind == "fireworks" else 54) * p.scale))
    colours = p.colours or (cfg.fireworks_colours if p.kind == "fireworks" else cfg.fire_colours)
    return {"id": f"particles:{i}", "kind": p.kind, "x": round(p.xy[0] * W, 2), "y": round(p.xy[1] * H, 2),
            "f_in": p.at_frame, "f_out": p.at_frame + max(1, sec_to_frame(duration, fps)),
            "seed": seed, "scale": p.scale, "count": max(8, count), "gravity": cfg.gravity,
            "colours": [c.lower() for c in colours]}


def particle_blocks(seg: Segment, tl: Timeline, prof) -> list[dict]:
    return [_emitter(p, i, tl, seg, prof) for i, p in enumerate(seg.gsap_scene_config.particles or [])]


def _fireworks(e: dict, f: float, fps: int, H: int) -> list[dict]:
    dur = (e["f_out"] - e["f_in"]) / fps
    t = (f - e["f_in"]) / fps
    if t < 0 or t > dur:
        return []
    rnd = _lcg(e["seed"])
    g = e["gravity"] * H * e["scale"]
    out = []
    for _ in range(e["count"]):
        ang, sp = rnd() * 2.0 * math.pi, H * (0.16 + 0.34 * rnd()) * e["scale"]
        col, size, life = int(rnd() * len(e["colours"])), H * (0.0025 + 0.004 * rnd()) * e["scale"], 0.55 + 0.45 * rnd()
        x = e["x"] + math.cos(ang) * sp * t
        y = e["y"] + math.sin(ang) * sp * t + 0.5 * g * t * t
        out.append({"x": x, "y": y, "r": size * max(0.0, 1.0 - 0.6 * t / dur),
                    "a": max(0.0, 1.0 - t / dur) * life, "c": col})
    return out


def _fire(e: dict, f: float, fps: int, H: int) -> list[dict]:
    dur = (e["f_out"] - e["f_in"]) / fps
    t = (f - e["f_in"]) / fps
    if t < 0 or t > dur:
        return []
    rnd = _lcg(e["seed"])
    out = []
    for _ in range(e["count"]):
        base, rise = (rnd() - 0.5) * 0.16 * H * e["scale"], H * (0.16 + 0.28 * rnd()) * e["scale"]
        sway, freq, phase = H * (0.01 + 0.035 * rnd()) * e["scale"], 1.5 + 3.5 * rnd(), rnd() * 2.0 * math.pi
        period, off = 0.7 + 1.1 * rnd(), rnd()
        u = (t + off * period) % period
        age = u / period
        x = e["x"] + base + math.sin(age * 2.0 * math.pi * freq + phase) * sway * (0.3 + 0.7 * age)
        y = e["y"] - rise * u
        out.append({"x": x, "y": y, "r": H * 0.012 * e["scale"] * (1.0 - 0.65 * age),
                    "a": (1.0 - age) ** 0.7 * min(1.0, t * 3.0), "c": min(len(e["colours"]) - 1, int(age * len(e["colours"])))})
    return out


def particle_state(e: dict, f: float, fps: int, H: int) -> list[dict]:
    """Python mirror of hf/fx2.js for one emitter at frame f (determinism tests)."""
    vals = _fireworks(e, f, fps, H) if e["kind"] == "fireworks" else _fire(e, f, fps, H)
    return [{**v, "x": round(v["x"], 3), "y": round(v["y"], 3), "r": round(v["r"], 3), "a": round(v["a"], 3)}
            for v in vals]


def scene_block(seg, tl, project_dir: Path, scene: dict, sc, prof, pins: list[dict] | None = None) -> dict:
    """Scene fields for all active visual effects (P12-P19). Empty when the scene sets none."""
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
    if sc.flag_characters:
        out["flag_characters"] = flag_characters(seg, tl, project_dir, pins or [])
    if sc.cutaway:
        out["cutaway"] = cutaway_block(seg, tl, prof)
    if sc.insets:
        out["insets"] = inset_blocks(seg, tl, project_dir, pins or [])
    if sc.particles:
        out["particles"] = particle_blocks(seg, tl, prof)
    return out
