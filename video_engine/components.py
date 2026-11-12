"""Native scene components drawn by the HyperFrames template: thermometers (screen) and parametric flows (map).

Python lays them out (frame / map px, frames -> seconds stay in the template) and fixes every random choice from a
seed; the template animates them from the paused timeline only (no wall clock): a thermometer's fill, colour and
number come from one tweened value, starting at `from` (or min); a flow's dash offset is (phase + speed x (t - t0))
at render time t."""
from __future__ import annotations

import math
import random

TUBE_W = 0.2     # tube width / thermometer height
BULB_D = 0.34    # bulb diameter / thermometer height


def thermo_starts(items: list) -> list[float]:
    """Start value of each thermometer, in list order.

    A thermometer starts at `from` when that is set. Otherwise, with `continue_from`, it starts at the `value`
    of the previous thermometer in time (`at_frame`, then list order). Otherwise it starts at `min`. The first
    thermometer in time has no previous value, so `continue_from` leaves it at `min`. An explicit `from` wins
    over `continue_from`. A start outside that thermometer's min..max is an error. The result depends only on
    the timeline, so two renders of the same scene match."""
    order = sorted(range(len(items)), key=lambda i: (items[i].at_frame, i))
    starts = [0.0] * len(items)
    prev: float | None = None
    for i in order:
        t = items[i]
        if t.from_value is not None:
            start = float(t.from_value)
        elif t.continue_from and prev is not None:
            start = prev
        else:
            start = float(t.min)
        if not t.min <= start <= t.max:
            raise ValueError(f"thermometer: start {start} is outside {t.min}..{t.max}")
        starts[i] = start
        prev = float(t.value)
    return starts


def thermo_data(t, W: int, H: int, start: float | None = None) -> dict:
    """Scene entry of a thermometer: box (x, y = top-left, w, h in frame px), tube and bulb sizes, value range.

    `from` is the fill's start value (default `min`). The template tweens that number to `value`."""
    h = t.height_pct / 100 * H
    w = max(BULB_D, TUBE_W) * h
    cx, cy = t.position[0] * W, t.position[1] * H
    origin = float(t.min if start is None else start)
    return {"x": round(cx - w / 2, 2), "y": round(cy - h / 2, 2), "w": round(w, 2), "h": round(h, 2),
            "tube_w": round(TUBE_W * h, 2), "bulb_d": round(BULB_D * h, 2), "value": t.value, "min": t.min,
            "max": t.max, "from": origin, "unit": t.unit, "decimals": t.decimals, "label": t.label, "note": t.note,
            "at_frame": t.at_frame, "out_frame": t.out_frame, "fill_s": t.fill_s, "low": t.colour_low.lower(),
            "high": t.colour_high.lower()}


def thermo_fraction(t) -> float:
    return (t.value - t.min) / (t.max - t.min)


def thermo_colour(t, frac: float) -> str:
    """Fill colour at a fill fraction: linear RGB mix of colour_low and colour_high (same as the template)."""
    a, b = (tuple(int(c[k:k + 2], 16) for k in (1, 3, 5)) for c in (t.colour_low, t.colour_high))
    return "#" + "".join(f"{math.floor(x + (y - x) * frac + 0.5):02x}" for x, y in zip(a, b))   # Math.round: half up


def flow_data(fl, mw: float, mh: float, seed_base: int) -> dict:
    """Scene entry of a flow: paths in map px and a seeded dash phase (screen px) per path."""
    rng = random.Random(seed_base * 7919 + fl.seed)
    per = fl.dash_px + fl.gap_px
    return {"paths": [[[round(x * mw, 2), round(y * mh, 2)] for x, y in p] for p in fl.paths],
            "phase": [round(rng.random() * per, 3) for _ in fl.paths], "colour": fl.colour.lower(), "alpha": fl.alpha,
            "width_px": fl.width_px, "dash_px": fl.dash_px, "gap_px": fl.gap_px, "speed_px": fl.speed_px,
            "arrows": fl.arrows, "at_frame": fl.at_frame, "reveal_s": fl.reveal_s, "out_frame": fl.out_frame,
            "fade_s": fl.fade_s, "label": fl.label}


def flow_offset(f: dict, i: int, t: float, fps: int) -> float:
    """Dash offset (screen px, before the camera divide) of path i at render time t (template rule)."""
    per = f["dash_px"] + f["gap_px"]
    return -((f["phase"][i] + f["speed_px"] * max(0.0, t - f["at_frame"] / fps)) % per)
