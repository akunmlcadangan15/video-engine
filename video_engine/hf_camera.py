"""2D and 2.5D HyperFrames camera planning, fit-in-frame binary search, flow splines, pulses, and per-frame evaluation."""
from __future__ import annotations

import json
import math

from . import camera3d, profiles, projection
from .units import units
from .util import EngineError

HF_PRESETS = {
    "static": (0, 0), "zoom_in": (0, 0), "zoom_out": (0, 0),
    "pan_north": (0, -1), "pan_south": (0, 1), "pan_east": (1, 0), "pan_west": (-1, 0),
    "pan_northeast": (1, -1), "pan_northwest": (-1, -1), "pan_southeast": (1, 1), "pan_southwest": (-1, 1),
}

FIT_PAD_REF = 24    # px at the reference canvas, inside the R63 safe area, that a fitted pin must clear
_FIT_SLACK = 0.6    # px; _state rounds x/y to 0.001, so the hold check allows half a pixel of that rounding


def _state(c: tuple[float, float], s: float, W: int, H: int, mw: float, mh: float) -> dict:
    """Container transform {x, y, s} that centres map point c at scale s, clamped so the map covers the frame."""
    x = W / 2 - s * c[0]
    y = H / 2 - s * c[1]
    x = min(0.0, max(W - s * mw, x))
    y = min(0.0, max(H - s * mh, y))
    return {"x": round(x, 3), "y": round(y, 3), "s": round(s, 5)}


def motion_px(a: dict, b: dict, W: int, H: int) -> float:
    """Visible motion between two transforms in output px: max(centre shift, zoom change at the corner)."""
    ca = ((W / 2 - a["x"]) / a["s"], (H / 2 - a["y"]) / a["s"])
    cb = ((W / 2 - b["x"]) / b["s"], (H / 2 - b["y"]) / b["s"])
    shift = math.hypot(ca[0] - cb[0], ca[1] - cb[1]) * (a["s"] + b["s"]) / 2
    dz = abs(math.log(b["s"] / a["s"])) * math.hypot(W, H) / 2
    return max(shift, dz)


def camera_states(sc, W: int, H: int, mw: float, mh: float) -> tuple[dict, dict, float]:
    """Preset camera: start/end transforms {x, y, s} of the map container (container size mw x mh covers the frame
    at s=1) and the visible motion in output px (same metric as native: max(centre shift, zoom change at the corner))."""
    if sc.camera_preset not in HF_PRESETS:
        raise EngineError(f"unknown HyperFrames camera_preset '{sc.camera_preset}' (known: {', '.join(HF_PRESETS)})")
    I = sc.camera_intensity
    fx, fy = (sc.focus or [0.5, 0.5])
    focus = (fx * mw, fy * mh)
    if sc.camera_preset == "zoom_in":
        s0, s1 = 1.0, 1.0 + 0.18 * I
        c0, c1 = (mw / 2, mh / 2), focus
    elif sc.camera_preset == "zoom_out":
        s0, s1 = 1.0 + 0.18 * I, 1.0
        c0, c1 = focus, (mw / 2, mh / 2)
    elif sc.camera_preset == "static":
        s0 = s1 = 1.05
        c0 = c1 = focus
    else:
        dx, dy = HF_PRESETS[sc.camera_preset]
        nrm = math.hypot(dx, dy)
        s0, s1 = 1.0 + 0.10 * I, 1.0 + 0.16 * I
        travel = 0.10 * I * mw  # map px
        ux, uy = dx / nrm, dy / nrm
        c0 = (focus[0] - ux * travel / 2, focus[1] - uy * travel / 2)
        c1 = (focus[0] + ux * travel / 2, focus[1] + uy * travel / 2)
    a, b = _state(c0, s0, W, H, mw, mh), _state(c1, s1, W, H, mw, mh)
    return a, b, motion_px(a, b, W, H)


def _pin_xy(p, sc, mw: float, mh: float) -> tuple[float, float]:
    if p.x is not None and p.y is not None:
        return p.x * mw, p.y * mh
    if not sc.map_bounds:
        raise EngineError(f"pin '{p.label}' uses lat/lng but gsap_scene_config.map_bounds is missing")
    x, y = projection.to_map(p.lat, p.lng, sc.map_bounds, projection.of(sc))
    return x * mw, y * mh


def pin_frame(p, i: int, n: int, fps: int) -> int:
    """Appear frame of pin i (relative to the segment start): at_frame, else 0.4 s + 0.5 s per pin."""
    return p.at_frame if p.at_frame is not None else min(n - 1, round(fps * (0.4 + 0.5 * i)))


def card_frame(c, n: int, fps: int) -> int:
    """Appear frame of a card (relative to the segment start): at_frame, else 0.2 s."""
    return c.at_frame if c.at_frame is not None else min(n - 1, round(fps * 0.2))


def fit_points(sc, refs, mw: float, mh: float) -> list[tuple[float, float]]:
    """Map-pixel positions of the pins named by a camera key's `fit` (labels or indexes)."""
    out = []
    for ref in refs:
        if isinstance(ref, int) and not isinstance(ref, bool):
            if not 0 <= ref < len(sc.pins):
                raise EngineError(f"camera fit index {ref} is outside the scene's {len(sc.pins)} pins")
            pin = sc.pins[ref]
        else:
            hits = [p for p in sc.pins if p.label == ref]
            if len(hits) != 1:
                raise EngineError(f"camera fit {ref!r} matches {len(hits)} pins; use one label or a pin index")
            pin = hits[0]
        out.append(_pin_xy(pin, sc, mw, mh))
    return out


def _fit_box(W: int, H: int) -> tuple[float, float, float, float]:
    u = units((W, H))
    sx, top, bottom = u.safe()
    pad = FIT_PAD_REF * u.k
    return sx + pad, top + pad, W - sx - pad, bottom - pad


def _pins_inside(cam: dict, f0: int, f1: int, points, box) -> bool:
    from .layout import camera_at

    x0, y0, x1, y1 = box
    for f in range(f0, f1 + 1):
        st = camera_at(cam, f)
        for px, py in points:
            x, y = st["x"] + st["s"] * px, st["y"] + st["s"] * py
            if not (x0 - _FIT_SLACK <= x <= x1 + _FIT_SLACK and y0 - _FIT_SLACK <= y <= y1 + _FIT_SLACK):
                return False
    return True


def _scaled_pulse(pulse: dict | None, t: float) -> dict | None:
    if not pulse or t <= 1e-4:
        return None
    zoom = pulse["zoom"]
    if isinstance(zoom, list):
        sizes = [round(float(v) * t, 4) for v in zoom]
        if not sizes or max(sizes) <= 1e-4:
            return None
        return {**pulse, "zoom": sizes}
    size = round(float(zoom) * t, 4)
    if size <= 1e-4:
        return None
    return {**pulse, "zoom": size}


def fit_hold(view, points, pulse, bound, f0: int, f1: int, drift: float, W: int, H: int, mw: float, mh: float,
             fps: int, anchor, base_zoom):
    """(pulse, drift bound) for the wait on `view` over [f0, f1) so every fitted pin stays in the safe area.

    The opening view is kept. Push-in and pulses are scaled down together, deterministically, until the whole
    hold fits, or to a still drift when nothing smaller would. A hold that already fits is returned unchanged."""
    if f1 <= f0 or not points:
        return pulse, bound
    box = _fit_box(W, H)
    centre, scale = view
    start = _state(centre, scale, W, H, mw, mh)

    def ok(pk, b) -> bool:
        z = pk and (pk["zoom"] if isinstance(pk["zoom"], list) else [pk["zoom"]])
        use = pk if z and max(z) > 0 else None
        legs = drift_legs(view, f0, f1, drift, use, W, H, mw, mh, fps, base_zoom=base_zoom, anchor=anchor, bound=b)
        return _pins_inside({"start": start, "legs": legs}, f0, f1, points, box)

    if ok(pulse, bound):
        return pulse, bound
    # How far an unbounded push of this wait actually goes, so the search has a finite top.
    if bound is None:
        free = drift_legs(view, f0, f1, drift, None, W, H, mw, mh, fps, anchor=anchor, bound=None)
        peak = max((L["to"]["s"] for L in free), default=view[1])
        limit = math.log(peak / view[1]) if peak > view[1] else 0.0
    else:
        limit = float(bound)

    def at(t: float):
        if t >= 1.0:
            return pulse, bound
        pk = _scaled_pulse(pulse, t)
        b = 0.0 if limit <= 0 else math.floor(limit * t * 10000) / 10000
        return pk, b

    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        pk, b = at(mid)
        if ok(pk, b):
            lo = mid
        else:
            hi = mid
    pk, b = at(lo)
    while lo > 0 and not ok(pk, b):
        lo = max(0.0, round(lo - 0.0001, 4))
        pk, b = at(lo)
    return pk, b


def camera_plan(sc, W: int, H: int, mw: float, mh: float, n: int, fps: int, theme: dict) -> dict:
    """Camera of one HyperFrames segment: {start, end, legs, leg_motion}. A leg {f0, f1, to, ease[, bez, lr, blur]}
    tweens the container from the previous transform to `to` over frames [f0, f1) under the leg ease (leg_ease).
    Preset: one glide leg over the segment. camera_path: per key a glide leg over the whole key-to-key time, or for
    move "whip" a drift (zoom at a constant ln rate theme camera_drift_per_s about the drift anchor) that waits until
    theme whip_s before the key, then a fast in-out leg with motion blur `blur` [sx, sy] (peak Gaussian deviation along
    the screen motion; absent when theme whip_blur_px is 0). Move "settle" = the same drift and in-out leg over the
    key's move_s, without blur. Move "reveal" = one leg over the whole key-to-key time under the cubic-bezier theme
    `reveal_ease` (fast early, long settle onto the key). leg_motion = the visible motion of each key's move (checked by R56). After the last key
    a drift runs to the segment end so the camera never stops. With theme `camera_pulse` (profile [camera]) every
    drift is split by `drift_legs` into drifts and pulses; a key's `pulse` sets the pulse sizes of the wait on its view
    (0 = drift only) and its `drift_max` bounds the drift of that wait. A key's `fit` (or `keep_in_frame`) lists pins
    that must stay inside the safe area, with a margin, for the whole wait: the push-in and the pulses of that wait
    are scaled down until they do. A key's `hold` adds a still leg on its view
    after it. Glide legs carry endpoint speeds `v` (glide_speeds)."""
    if not sc.camera_path:
        a, b, _ = camera_states(sc, W, H, mw, mh)
        return {"start": a, "end": b, "legs": [{"f0": 0, "f1": n, "to": b, "ease": "glide"}], "leg_motion": []}
    keys = sc.camera_path
    views = [((k.focus[0] * mw, k.focus[1] * mh), k.zoom) for k in keys]
    drift = theme["camera_drift_per_s"]

    pulse = theme.get("camera_pulse")
    anchor = tuple(pulse["anchor"]) if pulse else (0.5, 0.5)

    def drifts(i, f0, f1):     # the wait on key i's view; key `pulse` overrides the pulse sizes of this wait
        pk = pulse and keys[i].pulse is not None and {**pulse, "zoom": keys[i].pulse} or pulse
        z = pk and (pk["zoom"] if isinstance(pk["zoom"], list) else [pk["zoom"]])
        use = pk if z and max(z) > 0 else None
        bound = keys[i].drift_max
        if keys[i].fit:
            pts = fit_points(sc, keys[i].fit, mw, mh)
            use, bound = fit_hold(views[i], pts, use, bound, f0, f1, drift, W, H, mw, mh, fps, anchor,
                                  pulse and pulse["zoom"])
            z = use and (use["zoom"] if isinstance(use["zoom"], list) else [use["zoom"]])
            use = use if z and max(z) > 0 else None
        return drift_legs(views[i], f0, f1, drift, use, W, H, mw, mh, fps,
                          base_zoom=pulse and pulse["zoom"], anchor=anchor, bound=bound)

    start = _state(*views[0], W, H, mw, mh)
    legs, motion, prev = [], [], start
    if keys[0].hold:
        legs.append({"f0": 0, "f1": keys[0].hold, "to": start, "ease": "drift"})
    i = 0
    while i + 1 < len(keys):
        k, (c, s) = keys[i + 1], views[i + 1]
        to = _state(c, s, W, H, mw, mh)
        f0 = legs[-1]["f1"] if legs else 0
        if k.move == "flow":   # a run of flow keys: one spline from key i through the last consecutive flow key
            j = i + 1
            while j + 1 < len(keys) and keys[j + 1].move == "flow":
                j += 1
            legs += flow_legs(keys[i:j + 1], views[i:j + 1], f0, W, H, mw, mh, fps, theme.get("camera_bank"))
            for left, right in zip(views[i:j], views[i + 1:j + 1]):
                motion.append(round(motion_px(_state(*left, W, H, mw, mh), _state(*right, W, H, mw, mh), W, H), 2))
            prev = legs[-1]["to"]
            if keys[j].hold:
                legs.append({"f0": keys[j].at_frame, "f1": min(n, keys[j].at_frame + keys[j].hold),
                             "to": prev, "ease": "drift"})
            i = j
            continue
        if k.move in ("whip", "settle"):
            fw = max(f0, k.at_frame - max(1, round((k.move_s or theme["whip_s"]) * fps)))
            if fw > f0:
                legs += drifts(i, f0, fw)
                prev = legs[-1]["to"]
            blur = theme["whip_blur_px"] if k.move == "whip" else 0
            legs.append({"f0": fw, "f1": k.at_frame, "to": to, "ease": "whip",
                         **({"blur": whip_blur(prev, to, W, H, blur)} if blur > 0 else {})})
        elif k.move == "reveal":
            legs.append({"f0": f0, "f1": k.at_frame, "to": to, "ease": "reveal",
                         "bez": list(theme.get("reveal_ease") or profiles.REVEAL_EASE)})
        else:
            legs.append({"f0": f0, "f1": k.at_frame, "to": to, "ease": "glide"})
        motion.append(round(motion_px(prev, to, W, H), 2))
        prev = to
        if k.hold:
            legs.append({"f0": k.at_frame, "f1": min(n, k.at_frame + k.hold), "to": to, "ease": "drift"})
        i += 1
    last = min(n, keys[-1].at_frame + keys[-1].hold)
    if last < n:
        legs += drifts(len(keys) - 1, last, n)
    # tilt `t` and roll `r` (deg) of every transform: a leg ending at a key frame moves to its key's value (flow legs
    # carry their own spline and banking values); the template turns #tilt / #roll and scales #roll to cover
    for ch, attr in (("t", "tilt"), ("r", "roll")):
        if any(getattr(k, attr) for k in keys) or any(ch in L["to"] for L in legs):
            def val(f, attr=attr):
                return next(getattr(k, attr) for k in reversed(keys) if k.at_frame <= f)
            start = {**start, ch: getattr(keys[0], attr)}
            legs = [L if ch in L["to"] else {**L, "to": {**L["to"], ch: val(L["f1"])}} for L in legs]
    glide_speeds(legs, start, W, H, n, theme["camera_start_speed"], theme["camera_end_speed"])
    return {"start": start, "end": legs[-1]["to"], "legs": legs, "leg_motion": motion}


def _monotone(ts: list[float], vs: list[float]):
    """C1 monotone cubic Hermite (Fritsch-Carlson) through (ts, vs) with zero slope at both ends: never overshoots a
    key, stops only where the channel turns (or holds) and keeps its speed through every other key."""
    n = len(ts)
    d = [(vs[i + 1] - vs[i]) / (ts[i + 1] - ts[i]) for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * (ts[i + 1] - ts[i]) + (ts[i] - ts[i - 1]), (ts[i + 1] - ts[i]) + 2 * (ts[i] - ts[i - 1])
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def at(t: float) -> float:
        if t <= ts[0]:
            return vs[0]
        if t >= ts[-1]:
            return vs[-1]
        i = max(j for j in range(n - 1) if ts[j] <= t)
        h = ts[i + 1] - ts[i]
        u = (t - ts[i]) / h
        h00 = 2 * u ** 3 - 3 * u ** 2 + 1
        h10 = u ** 3 - 2 * u ** 2 + u
        h01 = -2 * u ** 3 + 3 * u ** 2
        h11 = u ** 3 - u ** 2
        return h00 * vs[i] + h10 * h * m[i] + h01 * vs[i + 1] + h11 * h * m[i + 1]
    return at


def _smooth(xs: list[float], w: int) -> list[float]:
    """Two passes of a centred moving average of half-width w (ends clamped)."""
    for _ in range(2 if w > 0 else 0):
        n = len(xs)
        xs = [sum(xs[max(0, i - w):min(n, i + w + 1)]) / (min(n, i + w + 1) - max(0, i - w)) for i in range(n)]
    return xs


def flow_legs(keys: list, views: list, f0: int, W: int, H: int, mw: float, mh: float, fps: int,
              bank: dict | None) -> list[dict]:
    """One-frame linear legs of a flow run: keys[0] (reached at frame f0, after its hold) through keys[-1]. Focus x, y,
    ln zoom, tilt and roll each follow a monotone cubic in time through the keys (a key's hold adds a still key), so the
    camera eases in at the first key, keeps moving through every inner key and eases out at the last one. With `bank`
    (profile [camera] bank_*) the camera leans into its motion: roll -bank.roll x sideways screen speed and tilt
    +bank.tilt x screen speed (frame widths / s; the zoom part counts half the frame diagonal), bounded and smoothed. A frame's tilt is lowered
    to the largest that still shows map in every corner (camera3d.corners_off_map, gate M03)."""
    ts, rows = [float(f0)], [(*views[0][0], math.log(views[0][1]), keys[0].tilt, keys[0].roll)]
    for k, ((cx, cy), s) in zip(keys[1:], views[1:]):
        row = (cx, cy, math.log(s), k.tilt, k.roll)
        if k.at_frame > ts[-1]:
            ts.append(float(k.at_frame))
            rows.append(row)
        if k.hold and k is not keys[-1]:
            ts.append(float(k.at_frame + k.hold))
            rows.append(row)
    ch = [_monotone(ts, [r[c] for r in rows]) for c in range(5)]
    a, b = int(ts[0]), int(ts[-1])
    v = [[c(f) for c in ch] for f in range(a, b + 1)]
    if bank:
        R = math.hypot(W, H) / 2
        sx, sp = [0.0], [0.0]
        for p, q in zip(v, v[1:]):
            s = math.exp((p[2] + q[2]) / 2)
            vx, vy = (q[0] - p[0]) * s * fps / W, (q[1] - p[1]) * s * fps / W
            sx.append(vx)
            sp.append(math.hypot(vx, vy) + abs(q[2] - p[2]) * R * fps / W)
        w = round(bank["smooth_s"] * fps / 2)
        sx, sp = _smooth(sx, w), _smooth(sp, w)
        def lim(x, m):
            return max(-m, min(m, x))
        for sample, x, g in zip(v, sx, sp):
            sample[3] = min(60.0, sample[3] + min(bank["max_tilt"], bank["tilt"] * g))
            sample[4] = lim(sample[4] - lim(bank["roll"] * x, bank["max_roll"]), 45.0)
    out = []
    for f in range(a, b):
        q = v[f + 1 - a]
        to = _state((q[0], q[1]), math.exp(q[2]), W, H, mw, mh)
        t = q[3]
        if t > 0 and camera3d.corners_off_map({**to, "t": t}, W, H, mw, mh):   # tilt no further than the map covers
            lo, hi = 0.0, t
            for _ in range(14):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if camera3d.corners_off_map({**to, "t": mid}, W, H, mw, mh) else (mid, hi)
            t = lo
        out.append({"f0": f, "f1": f + 1, "to": {**to, "t": round(t, 4), "r": round(q[4], 4)}, "ease": "lin"})
    return out


def glide_speeds(legs: list[dict], start: dict, W: int, H: int, n: int, first: float, last: float) -> None:
    """Set `v` [a, b] (ease slope at progress 0 and 1, leg_ease) on every glide leg of a camera path so that the screen
    speed (ln zoom x half the frame diagonal, centre shift in px) is continuous at its ends: next to a glide leg the
    slower leg's mean, next to another leg that leg's speed there, 0 where the motion turns by 90 deg or more;
    `first` / `last` x the leg's mean at the segment start / end (a leg from the last frame on is never shown)."""
    R = math.hypot(W, H) / 2
    froms = [start] + [L["to"] for L in legs[:-1]]

    def state(i, p):
        L, a = legs[i], froms[i]
        e = p if L["ease"] == "glide" else leg_ease(L, a["s"])(p)
        return {k: a[k] + (L["to"][k] - a[k]) * e for k in "xys"}

    def step(a, b):   # screen motion from transform a to b
        m = (a["s"] + b["s"]) / 2
        return (R * math.log(b["s"] / a["s"]), ((b["x"] - W / 2) / b["s"] - (a["x"] - W / 2) / a["s"]) * -m,
                ((b["y"] - H / 2) / b["s"] - (a["y"] - H / 2) / a["s"]) * -m)

    def vel(i, end):  # per unit of tween fraction on a glide leg, per frame on any other leg
        d = 1e-4
        u = step(state(i, 1 - d if end else 0.0), state(i, 1.0 if end else d))
        k = d if legs[i]["ease"] == "glide" else d * (legs[i]["f1"] - legs[i]["f0"])
        return [c / k for c in u]

    def mean(i):
        return sum(math.hypot(*step(state(i, j / 16), state(i, (j + 1) / 16))) for j in range(16)) / (
            legs[i]["f1"] - legs[i]["f0"])

    for i, L in enumerate(legs):
        if L["ease"] != "glide":
            continue
        own, m, v = (vel(i, 0), vel(i, 1)), mean(i), []
        for end, j in ((0, i - 1), (1, i + 1)):
            if not 0 <= j < len(legs) or legs[j]["f0"] >= n - 1:
                speed = (last if end else first) * m
            else:
                other = vel(j, 1 - end)
                ahead = sum(a * b for a, b in zip(other, own[end])) > 0
                speed = ahead * (min(m, mean(j)) if legs[j]["ease"] == "glide" else math.hypot(*other))
            g = math.hypot(*own[end])
            v.append(round(min(3.0, speed * (L["f1"] - L["f0"]) / g) if g > 1e-9 else 1.0, 4))
        L["v"] = v


def drift_base(skeleton: list[dict], rate: float, bound: float | None) -> list[dict]:
    """Base ln zoom offsets of a wait: every leg of `skeleton` ({f0, f1, ...}, contiguous) gets g0, g1 = the drift ln
    offset from the wait's view at its first and last frame. The drift moves at `rate` ln per frame; without `bound`
    it pushes in for the whole wait. With `bound` it turns at 0 and `bound`: a drift leg that reaches a turn is split
    on the first frame at or past it and goes on the other way; a pulse leg keeps its direction (it may pass the turn
    by at most its length x rate) and the next drift leg turns."""
    out, g, sg = [], 0.0, 1.0
    for L in skeleton:
        if bound is None or rate <= 0 or L["ease"] == "pulse":
            g1 = g + sg * rate * (L["f1"] - L["f0"])
            out.append({**L, "g0": g, "g1": g1})
            g = g1
            continue
        a = L["f0"]
        while a < L["f1"]:
            if (sg > 0 and g >= bound - 1e-12) or (sg < 0 and g <= 1e-12):
                sg = -sg
            lim = bound if sg > 0 else 0.0
            k = max(1, math.ceil((lim - g) / (sg * rate) - 1e-9))
            b = min(L["f1"], a + k)
            g1 = g + sg * rate * (b - a)
            out.append({**L, "f0": a, "f1": b, "g0": g, "g1": g1})
            g, a = g1, b
    return out


def drift_legs(view, f0: int, f1: int, drift: float, pulse: dict | None, W: int, H: int, mw: float, mh: float,
               fps: int, base_zoom: float | None = None, anchor: tuple[float, float] | None = None,
               bound: float | None = None) -> list[dict]:
    """Legs of the camera waiting on `view` ((map centre, zoom)) over frames [f0, f1): the base drift zooms at a
    constant `drift` ln per second about the screen point `anchor` (frame fractions; default the pulse anchor, else
    the frame centre), pushing in, or with `bound` turning at 0 and `bound` ln (drift_base). Without `pulse` (or when
    [f0, f1) is shorter than gap + pulse + gap): drift legs only. With `pulse` {zoom, s, gap_s, pan, ease, anchor,
    dir}: k = (frames - gap) // (pulse + gap) pulses, evenly spaced with drift legs between them (each >= gap). Pulses
    alternate away from the base drift (zoom by dir x `zoom` ln scale about `anchor`, frame centre shifted by `pan`
    frame widths, sides alternating) and back to it; ease cubic-bezier `ease` (leg ease "pulse" with `bez`) on top of
    the base drift (`lr` = the leg's base ln change, leg_ease). An odd count ends the wait off the base. `zoom` is one
    size or a list with one size per away pulse (the last one repeats); `pan` scales with size / `base_zoom` (default
    `zoom`); a pulse of size 0 and its way back are drift legs. Legs off the base (an away pulse, the way back and the
    drift between) carry `away` = the away pulse index."""
    c0, s0 = view
    zs: list
    if not pulse:
        zs = []
    elif isinstance(pulse["zoom"], list):
        zs = pulse["zoom"]
    else:
        zs = [pulse["zoom"]]
    an = anchor or (tuple(pulse["anchor"]) if pulse else (0.5, 0.5))
    ax, ay = an[0] * W - W / 2, an[1] * H - H / 2   # anchor offset from the frame centre
    rate = drift / fps

    def at(g, z, side):
        sb = s0 * math.exp(g)
        c = (c0[0] + ax / s0 - ax / sb, c0[1] + ay / s0 - ay / sb)
        s = sb
        if z > 0:
            so = sb * math.exp(pulse["dir"] * z)
            pan = pulse["pan"] * z / (base_zoom or zs[0])
            c = (c[0] + ax / sb - ax / so + side * pan * W / so, c[1] + ay / sb - ay / so)
            s = so
        return _state(c, s, W, H, mw, mh)

    D = f1 - f0
    sk = []                                                   # skeleton: {f0, f1, ease, z, side[, away, bez]}
    if pulse:
        P, G = max(1, round(pulse["s"] * fps)), max(1, round(pulse["gap_s"] * fps))
        k = max(0, (D - G) // (P + G))
    if not pulse or k == 0:
        sk = [{"f0": f0, "f1": f1, "ease": "drift", "z": 0, "side": 0}]
    else:
        free = D - k * P
        gaps = [free * (i + 1) // (k + 1) - free * i // (k + 1) for i in range(k + 1)]
        u, z, side, cur, m = f0, 0, 1, 0, -1
        def tag(leg):
            return {**leg, "away": m} if z > 0 else leg   # legs off the base carry their away pulse index
        for i in range(k):
            sk.append(tag({"f0": u, "f1": u + gaps[i], "ease": "drift", "z": z, "side": cur}))
            u += gaps[i]
            if i % 2 == 0:   # away from the base
                m = i // 2
                z, cur, side = zs[min(m, len(zs) - 1)], side, -side
                leg = tag({"f0": u, "f1": u + P, "ease": "pulse", "bez": list(pulse["ease"]), "z": z, "side": cur})
            else:            # back to the base
                leg = tag({"f0": u, "f1": u + P, "ease": "pulse", "bez": list(pulse["ease"]), "z": 0, "side": 0})
                z = 0
            if "away" not in leg:
                leg = {"f0": u, "f1": u + P, "ease": "drift", "z": 0, "side": 0}
            sk.append(leg)
            u += P
        sk.append(tag({"f0": u, "f1": f1, "ease": "drift", "z": z, "side": cur}))
    legs = []
    for L in drift_base(sk, rate, bound):
        leg = {"f0": L["f0"], "f1": L["f1"], "to": at(L["g1"], L["z"], L["side"]), "ease": L["ease"]}
        if "bez" in L:
            leg["bez"] = L["bez"]
            leg["lr"] = round(L["g1"] - L["g0"], 6)
        if "away" in L:
            leg["away"] = L["away"]
        legs.append(leg)
    return legs


def leg_ease(leg: dict, s0: float):
    """Ease of a camera leg (progress 0..1 -> tween fraction of x, y and s; hf template window.__legs is the same
    math). Glide legs with endpoint speeds `v` [a, b] follow the unit-interval cubic with slopes a, b at 0, 1 (equal
    slopes 0 reproduce an ease-in-out). Drift legs zoom at a constant ln rate (s(t) = s0 rho^t). Pulse legs move their
    base drift's ln change `lr` linearly in t and the cubic-bezier share by `bez` (pulse legs: the base drift's ln change) moves its ln zoom at a
    constant rate over its base share `lr` and shifts by its pulse share under cubic-bezier `bez`."""
    from .layout import EASES, cubic_bezier
    if "v" in leg:
        a, b = leg["v"]
        return lambda t: ((a + b - 2) * t + 3 - 2 * a - b) * t ** 2 + a * t
    rho = leg["to"]["s"] / s0
    if "bez" not in leg and leg["ease"] == "drift":
        return (lambda t: t) if abs(rho - 1) < 1e-9 else (lambda t: (rho ** t - 1) / (rho - 1))
    if "bez" in leg:
        x1, y1, x2, y2 = leg["bez"]

        def B(t: float, x1: float = x1, y1: float = y1, x2: float = x2, y2: float = y2) -> float:
            return cubic_bezier(x1, y1, x2, y2, t)
    else:
        B = EASES[leg["ease"]]
    if abs(rho - 1) < 1e-9 or "lr" not in leg:
        return B
    ln = math.log(rho)
    r = math.copysign(min(abs(leg["lr"]), abs(ln)), ln)
    return lambda t: (math.exp(r * t + (ln - r) * B(t)) - 1) / (rho - 1)


def whip_blur(a: dict, b: dict, W: int, H: int, peak: float) -> list[float]:
    """Peak blur [sx, sy] of a whip from transform a to b: `peak` split along the on-screen travel of the frame-centre
    map point; each axis keeps at least a quarter of `peak` (zoom smears every direction)."""
    mx, my = (W / 2 - a["x"]) / a["s"], (H / 2 - a["y"]) / a["s"]
    dx, dy = b["x"] + mx * b["s"] - W / 2, b["y"] + my * b["s"] - H / 2
    L = math.hypot(dx, dy) or 1.0
    return [round(peak * max(abs(dx) / L, 0.25), 2), round(peak * max(abs(dy) / L, 0.25), 2)]


def camera_frames(cam: dict, n: int) -> list[dict]:
    """Evaluate the 2D/2.5D camera transform at every integer frame 0..n with Python's leg math (`leg_ease`),
    eliminating JS tween rounding drift when driven by `SCENE.camera_frames`."""
    legs = cam.get("legs") or []
    prev = cam["start"]
    keys = tuple(k for k in ("x", "y", "s", "t", "r") if k in prev)

    def fmt(st: dict) -> dict:
        return {k: round(st[k], 6 if k == "s" else 4) for k in keys}

    frames: list[dict] = []
    idx = 0
    n_legs = len(legs)
    for f in range(n + 1):
        while idx < n_legs and f >= legs[idx]["f1"]:
            prev = legs[idx]["to"]
            idx += 1
        if idx >= n_legs or f <= legs[idx]["f0"]:
            frames.append(fmt(prev))
        else:
            L = legs[idx]
            p = (f - L["f0"]) / (L["f1"] - L["f0"])
            e = leg_ease(L, prev["s"])(p)
            frames.append(fmt({k: prev[k] + (L["to"][k] - prev[k]) * e for k in keys}))
    return frames


def camera_frames_sig(cam: dict, fps: int, n: int) -> str:
    """Canonical integer-micro signature of camera + fps + n_frames matching the template's `cfSig` check."""
    def r(v) -> int:
        return math.floor(float(v or 0) * 1e6 + 0.5)

    def pt(p: dict) -> list:
        return [r(p.get("x")), r(p.get("y")), r(p.get("s")), r(p.get("t")), r(p["r"]) if "r" in p else ""]

    legs = [
        [L["f0"], L["f1"], L.get("ease") or "", [r(x) for x in L["v"]] if L.get("v") else "",
         [r(x) for x in L["bez"]] if L.get("bez") else "", r(L["lr"]) if "lr" in L else "", pt(L["to"])]
        for L in cam.get("legs") or []
    ]
    tilt_p = r(cam["tilt"]["p"]) if cam.get("tilt") else ""
    return json.dumps([fps, n, tilt_p, pt(cam["start"]), legs], separators=(",", ":"))
