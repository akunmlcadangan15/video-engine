"""HyperFrames path: map/pin/card motion-graphics segments only (no captions in HTML). One HTML project per segment in
/dev/shm, rendered with the vendored HyperFrames CLI; the same HTML is probed by the DOM gate (R63) on a dynamic port."""
from __future__ import annotations

import contextlib
import copy
import functools
import http.server
import json
import math
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import hypoverum_core_rs as core

from . import assets, cache, camera3d, camera_full3d, config, entities, fx, globe, hfversion, legibility, parallax, printstyle, profiles, projection, props, streams, toolchain
from . import components
from .maps import (MAP_JPEG_QUALITY, _map_copy, _need_w, _q, deep_entries, deep_plan, deep_tiles, map_image_size,
                   map_key, morph_data, texture_dim, textures)
from .timeline import HYPERFRAMES, Segment, Timeline
from .util import EngineError, blake3_files, blake3_json, code_hash, run
from .units import units

CODE = (Path(__file__), Path(components.__file__), Path(fx.__file__), Path(globe.__file__),
        Path(parallax.__file__), Path(props.__file__), Path(camera3d.__file__), Path(camera_full3d.__file__),
        Path(entities.__file__), Path(printstyle.__file__), Path(projection.__file__))   # writes the build dir (html, scene.js); legibility.py is keyed in the scene (legibility.code_hash)
HF_VERSION = "hf-2"   # hf-2: capture/encode profile HF_QUALITY + HF_CRF
HF_FONTS = ("Inter-Bold.otf", "Inter-SemiBold.otf")   # copied into every HF build dir; hashed into the cache key
# Theme fonts (vendor/fonts/README.md): role -> (CSS family, file, weight range). Copied and hashed only when the scene
# theme uses the role, so a scene that uses none builds exactly as with HF_FONTS alone.
THEME_FONTS = {"script": ("EngScript", "KaushanScript-Regular.ttf", "400"),
               "title": ("EngTitle", "ArchivoBlack-Regular.ttf", "400"),
               "serif": ("EngSerif", "PlayfairDisplay[wght].ttf", "400 900"),
               "garamond": ("EngGaramond", "EBGaramond[wght].ttf", "400 800"),
               "garamond_italic": ("EngGaramondI", "EBGaramond-Italic[wght].ttf", "400 800"),
               "cinzel": ("EngCinzel", "Cinzel[wght].ttf", "400 900"),
               "hand": ("EngHand", "Kalam-Bold.ttf", "700")}


def tint(hex_colour: str, t: float) -> str:
    """`hex_colour` mixed `t` (0-1) of the way toward white, "#rrggbb" lower case."""
    rgb = [int(hex_colour[k:k + 2], 16) for k in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * t):02x}" for c in rgb)


def font_roles(scene: dict) -> list[str]:
    """Font roles a scene uses: the theme's label and title fonts, the serif when it shows a year; with a profile
    [print] the roles of its surface instead of the area-label title font; the title font for map texts."""
    th = scene.get("theme", {})
    used = {th.get("label_font"), th.get("title_font")}
    if any(f["kind"] == "year" for f in scene.get("figures", [])):
        used.add("serif")
    if pr := scene.get("print"):
        used |= {r[0] for r in pr["roles"].values()}
    elif scene.get("layout", {}).get("areas"):
        used.add("title")
    if scene.get("map_texts"):
        used.add("title")
    return [r for r in THEME_FONTS if r in used]


def font_files(scene: dict) -> list[str]:
    return [*HF_FONTS, *(THEME_FONTS[r][1] for r in font_roles(scene))]


def font_faces(scene: dict) -> str:
    """@font-face rules of the scene's theme fonts (empty when it uses none)."""
    return "".join(f"@font-face{{font-family:'{fam}';src:url('assets/{quote(f)}') format('truetype');font-weight:{w}}}"
                   for fam, f, w in (THEME_FONTS[r] for r in font_roles(scene)))


HF_PRESETS = {
    "static": (0, 0), "zoom_in": (0, 0), "zoom_out": (0, 0),
    "pan_north": (0, -1), "pan_south": (0, 1), "pan_east": (1, 0), "pan_west": (-1, 0),
    "pan_northeast": (1, -1), "pan_northwest": (-1, -1), "pan_southeast": (1, 1), "pan_southwest": (-1, 1),
}
SCENE_CACHE_MAX = 64     # memo entries of scene_data() (one build calls it several times per segment)
INK_CACHE_MAX = 64       # memo entries of the adaptive ink choices
# HyperFrames capture/encode profile for the intermediate segment file (re-encoded once more by the build).
# "standard" = q100 screenshot + x264 medium; CRF 14 keeps the intermediate close to lossless.
HF_QUALITY = "standard"  # defaults; profile [render] hf_quality / hf_crf override them (encode_settings)
HF_CRF = 14
CARD_TOP = {"top": 0.12, "bottom": 0.77}
TITLE_TOP = 0.11                       # big title top edge (fraction of the frame height)
CARD_TOP_LANDSCAPE = {"top": 0.10, "bottom": 0.70}   # landscape (units.py): cards fit the z1 / z2 bands
TITLE_TOP_LANDSCAPE = 0.10
FIGURE_POP_S = {"elastic": 0.9, "back": 0.45}   # entry of a non-counter figure by theme figure_pop (template twin)
TITLE_GAP_EM = 0.5                      # theme title_anchor "region": gap between the title and its region (title em)
TEMPLATE_SCALED = {"label_box_radius_px"}   # the template scales these itself (S.width / reference width)


def landscape_theme(theme: dict, k: float) -> dict:
    """Theme px lengths (keys ending in `_px`, px at the 1920x1080 reference canvas) in output px for a landscape
    frame (units.k; 1 at 1920x1080). Portrait themes are passed unchanged (the template takes them as output px)."""
    if k == 1.0:
        return theme
    return {key: (v * k if key.endswith("_px") and key not in TEMPLATE_SCALED and isinstance(v, (int, float))
                  and not isinstance(v, bool) else v)
            for key, v in theme.items()}


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
    (0 = drift only) and its `drift_max` bounds the drift of that wait. A key's `hold` adds a still leg on its view
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
        return drift_legs(views[i], f0, f1, drift, pk if z and max(z) > 0 else None, W, H, mw, mh, fps,
                          base_zoom=pulse and pulse["zoom"], anchor=anchor, bound=keys[i].drift_max)

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
            for a, b in zip(views[i:j], views[i + 1:j + 1]):
                motion.append(round(motion_px(_state(*a, W, H, mw, mh), _state(*b, W, H, mw, mh), W, H), 2))
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
            val = lambda f, attr=attr: next(getattr(k, attr) for k in reversed(keys) if k.at_frame <= f)
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
        h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
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
        lim = lambda x, m: max(-m, min(m, x))
        for row, x, g in zip(v, sx, sp):
            row[3] = min(60.0, row[3] + min(bank["max_tilt"], bank["tilt"] * g))
            row[4] = lim(row[4] - lim(bank["roll"] * x, bank["max_roll"]), 45.0)
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
    zs = pulse and (pulse["zoom"] if isinstance(pulse["zoom"], list) else [pulse["zoom"]])
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
        tag = lambda leg: {**leg, "away": m} if z > 0 else leg   # legs off the base carry their away pulse index
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


def leg_ease(L: dict, s_from: float):
    """Ease of a camera leg (progress 0..1 -> tween fraction of x, y and s; hf template window.__legs is the same
    math). A leg with `v` [a, b]: the cubic with slopes a and b at 0 and 1 (glide_speeds). Glide is
    (1 - cos(pi t)) / 2 (zero slope at both ends, same as layout.EASES), whip power3.inOut, a pulse leg's cubic-bezier `bez`.
    drift: constant ln-rate zoom (rho^t - 1) / (rho - 1), rho = to.s / from.s (linear when rho = 1). A leg with `lr`
    (pulse legs: the base drift's ln change) moves its ln zoom at a
    constant rate r plus the B share of the rest, (exp(r t + (ln rho - r) B(t)) - 1) / (rho - 1), with
    r = sign(ln rho) min(|lr|, |ln rho|): the zoom keeps moving at both ends and never reverses inside the leg."""
    from .layout import EASES, cubic_bezier
    rho = L["to"]["s"] / s_from
    if "v" in L:
        a, b = L["v"]
        return lambda t: ((a + b - 2) * t + 3 - 2 * a - b) * t * t + a * t
    if "bez" in L:
        b = L["bez"]
        shape = lambda t: cubic_bezier(b[0], b[1], b[2], b[3], t)
    elif L["ease"] == "drift":
        return (lambda t: t) if abs(rho - 1) < 1e-9 else (lambda t: (rho ** t - 1) / (rho - 1))
    else:
        shape = EASES[L["ease"]]
    if abs(rho - 1) < 1e-9 or "lr" not in L:
        return shape
    ln = math.log(rho)
    r = math.copysign(min(abs(L["lr"]), abs(ln)), ln)
    return lambda t: (math.exp(r * t + (ln - r) * shape(t)) - 1) / (rho - 1)


def whip_blur(a: dict, b: dict, W: int, H: int, peak: float) -> list[float]:
    """Peak blur [sx, sy] of a whip from transform a to b: `peak` split along the on-screen travel of the frame-centre
    map point; each axis keeps at least a quarter of `peak` (zoom smears every direction)."""
    mx, my = (W / 2 - a["x"]) / a["s"], (H / 2 - a["y"]) / a["s"]
    dx, dy = b["x"] + mx * b["s"] - W / 2, b["y"] + my * b["s"] - H / 2
    L = math.hypot(dx, dy) or 1.0
    return [round(peak * max(abs(dx) / L, 0.25), 2), round(peak * max(abs(dy) / L, 0.25), 2)]


def _pin_xy(p, sc, mw, mh) -> tuple[float, float]:
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


def _bounded_put(memo: dict, key: str, value, limit: int) -> None:
    """Insert key -> value and evict the oldest entry when the memo is over `limit` (in-process memo, never grows
    without bound in a long-lived engine process)."""
    memo.pop(key, None)
    memo[key] = value
    while len(memo) > limit:
        memo.pop(next(iter(memo)))


def _scene_key(seg: Segment, tl: Timeline, project_dir: Path) -> str:
    """Inputs of scene_data: the scene config, the timeline outside the segments, the profile and the map file.
    A beats scene lets apply_segment derive camera_path and element at_frame values, so those derived fields are
    excluded; every other scene keeps them, and the project directory is part of the key because relative assets
    (morph, cards, props) live there even when two projects share one symlinked map."""
    from . import layout
    scfg = seg.gsap_scene_config
    if layout.is_beats(seg, tl):
        sc = scfg.model_dump(mode="json", exclude={"camera_path", "title_at_frame"})
        for k in ("pins", "regions", "routes", "figures", "cards"):
            for e in sc.get(k) or []:
                e.pop("at_frame", None)
    else:
        sc = scfg.model_dump(mode="json")
    for item in [*(sc.get("pins") or []), *(sc.get("regions") or [])]:
        if item.get("flag") is None:
            item.pop("flag", None)
        for change in item.get("changes", []):
            if change.get("flag") is None:
                change.pop("flag", None)
    p = project_dir / scfg.map_asset
    try:
        st = p.stat()
        src = [str(p.resolve()), st.st_mtime_ns, st.st_size]
    except OSError:
        src = [str(p.resolve()), None, None]
    deps = [scfg.map_asset]
    if scfg.morph:
        deps.append(scfg.morph.asset)
    deps += [L.asset for L in (scfg.layers or [])]
    deps += [pth.asset for _, pth, _, _ in props.flat(seg)]
    for c in scfg.cards:
        if c.asset_ref and (cr := tl.credit_for(ref=c.asset_ref)) and cr.path:
            deps.append(cr.path)
    deps += [str(assets.resolve(i.asset, project=project_dir, kind="image").path) for i in scfg.insets or []]
    flags = [p.flag for p in scfg.pins]
    flags += [f for r in scfg.regions or [] for f in [r.flag, *(c.flag for c in r.changes)]]
    flags += [c.flag for c in scfg.flag_characters or []]
    deps += [str(assets.resolve(assets.flag_for(f.code, f.variant), project=project_dir, kind="flag").path)
             for f in flags if f]
    files = sorted({str((Path(project_dir) / d).resolve()) for d in deps})
    asset_hashes = [blake3_files([Path(f)])[0] if Path(f).is_file() else None for f in files]
    hooks = [id(fn) for fn in (deep_plan, morph_data, map_image_size, probe_dom, props.scene_props,
                               parallax.scene_layers, layout.apply_segment, legibility.all_times,
                               legibility.collect, legibility.elements, legibility.choose, legibility.scene_inks)]
    prof = profiles.of(tl)
    ov_dump = [o.model_dump(mode="json") for o in seg.overlays] if seg.overlays else None
    return blake3_json({"scene": sc, **({"overlays": ov_dump} if ov_dump else {}), "engine": seg.engine_type, "n": seg.n_frames,
                        "project": str(Path(project_dir).resolve()), "assets": [files, asset_hashes], "hooks": hooks,
                        "tl": tl.model_dump(mode="json", exclude={"segments", "captions", "captions_meta"}),
                        "profile": [prof.name, prof.blake3, prof.data.model_dump(mode="json", exclude={"fx"})],
                        "legibility": legibility.code_hash(), "map": src})


def clear_scene_cache() -> None:
    """Drop the in-process scene memo (tests; a caller that mutates a timeline outside apply_segment)."""
    _SCENES.clear()
    _INKS.clear()


def _flag_data(flag, prof, project_dir: Path, region_aspect: float | None = None) -> dict:
    asset = assets.resolve(assets.flag_for(flag.code, flag.variant), project=project_dir, kind="flag")
    out = {**prof.data.flags.model_dump(), **flag.model_dump(exclude_none=True, exclude={"code", "variant"}),
           "href": f"assets/{asset.sha256[:16]}.svg", "aspect": _svg_aspect(asset.path)}
    loss = out.pop("auto_max_loss")
    if out["fit"] == "auto":
        out["fit"] = "cover" if region_aspect is None else flag_fit(region_aspect, out["aspect"], loss)
    return out


def flag_fit(region_aspect: float, flag_aspect: float, max_loss: float) -> str:
    """Automatic flag fit of one region: `cover` keeps the region filled and crops the flag; the share of the flag
    cut away is 1 - min(r/a, a/r) for region bbox aspect r and flag aspect a. Past `max_loss` (a tall or very wide
    region: Myanmar or Chile against a 4:3 flag) the crop would leave a strip nobody recognises, so the flag is
    `contain`ed whole inside the region bbox instead."""
    if not (region_aspect > 0 and flag_aspect > 0):
        return "cover"
    return "contain" if 1 - min(region_aspect / flag_aspect, flag_aspect / region_aspect) > max_loss else "cover"


def region_aspect(polygon: list[list[float]], mw: float, mh: float, globe_scene: bool) -> float:
    """Width/height of a region's bbox as drawn: map px on a flat map; on the globe the lng span shrinks by
    cos(mean lat) (the orthographic view near the region's centre), the polygon being equirectangular 0..1."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    if dy <= 0:
        return 0.0
    if not globe_scene:
        return dx * mw / (dy * mh)
    lat = math.radians(90 - (max(ys) + min(ys)) / 2 * 180)
    return dx * 360 * math.cos(lat) / (dy * 180)


def _svg_aspect(path) -> float:
    """width/height of an SVG's viewBox (4:3 fallback). The template fits flags itself: Chrome treats an
    `<image>`-referenced SVG as `meet` whatever its preserveAspectRatio says, so `slice` alone never covers."""
    m = re.search(r'viewBox\s*=\s*"\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', Path(path).read_text()[:2000])
    return round(float(m[1]) / float(m[2]), 4) if m and float(m[2]) > 0 else round(4 / 3, 4)


def scene_data(seg: Segment, tl: Timeline, project_dir: Path) -> dict:
    key = _scene_key(seg, tl, project_dir)
    if (hit := _SCENES.get(key)) is not None:
        return copy.deepcopy(hit)
    from . import layout
    lay = layout.apply_segment(seg, tl, project_dir)   # beats scenes: derived camera_path and frames first
    S = tl.settings
    sc = seg.gsap_scene_config
    n = seg.n_frames
    prof = profiles.of(tl)
    theme = prof.render_values("hf")
    U = units(tl)
    if U.landscape:
        theme = landscape_theme(theme, U.k)
    card_top, title_top = (CARD_TOP_LANDSCAPE, TITLE_TOP_LANDSCAPE) if U.landscape else (CARD_TOP, TITLE_TOP)
    G = C3 = None
    if sc.globe:   # the 2D camera stays at identity over a frame-sized #cam; hf/globe.js draws the globe and moves pins
        G = globe.scene_block(seg, tl, project_dir, theme)
        if sc.globe.halo:
            G["halo"] = fx.halo_data(prof.fx.halo, sc.globe.halo)
        if sc.geoid:
            G["geoid"] = fx.geoid_block(seg, tl, prof)
        mw, mh = float(S.width), float(S.height)
        still = {"x": 0.0, "y": 0.0, "s": 1.0}
        cam = {"start": still, "end": still, "legs": [{"f0": 0, "f1": n, "to": still, "ease": "glide"}], "leg_motion": []}
        km = globe.key_motion(G, S.width, S.height)
        motion = min(km) if km else 0.0
    elif sc.camera3d:   # full 3D camera: #cam stays at identity; hf/scene3d.js draws the map plane and moves pins
        iw, ih = map_image_size(project_dir / sc.map_asset)
        cover = max(S.width / iw, S.height / ih)
        mw, mh = iw * cover, ih * cover
        states = camera_full3d.frames(sc.camera3d, n, mw, mh)
        still = {"x": 0.0, "y": 0.0, "s": 1.0}
        cam = {"start": still, "end": still, "legs": [{"f0": 0, "f1": n, "to": still, "ease": "glide"}], "leg_motion": []}
        met = camera_full3d.metrics(states, S.width, S.height, S.fps)
        motion = met["motion_px"]
        C3 = {"frames": [st.as_list() for st in states], "near_frac": camera_full3d.NEAR_FRAC,
              "need_w": round(camera_full3d.need_w(states, S.width, S.height, mw), 2),
              "metrics": {k: round(v, 3) for k, v in met.items()}}
    else:
        iw, ih = map_image_size(project_dir / sc.map_asset)
        cover = max(S.width / iw, S.height / ih)
        mw, mh = iw * cover, ih * cover
        cam = camera_plan(sc, S.width, S.height, mw, mh, n, S.fps, theme)
        motion = min(cam["leg_motion"]) if cam["leg_motion"] else motion_px(cam["start"], cam["end"], S.width, S.height)
    pins = []
    for i, p in enumerate(sc.pins):
        f = pin_frame(p, i, n, S.fps)
        if G:
            xy = globe.project(globe.camera_at(G, f, S.fps), p.lat, p.lng, S.width, S.height) or (-1000.0, -1000.0)
            px, py = xy
        elif C3:
            x0, y0 = _pin_xy(p, sc, mw, mh)
            st = states[min(f, n - 1)]
            px, py, _ = camera_full3d.project(st, (x0, -y0, 0.0), S.width, S.height)
            px, py = (-1000.0, -1000.0) if px is None else (px, py)
        else:
            px, py = _pin_xy(p, sc, mw, mh)
        pins.append({"label": p.label, "px": round(px, 2), "py": round(py, 2),
                     "at_frame": f, **({"label_side": p.label_side} if p.label_side else {}),
                     **({"box": p.box} if p.box is not None else {}),
                     **({"lat": p.lat, "lng": p.lng} if G else {}),
                     **({"mx": round(x0, 3), "my": round(y0, 3)} if C3 else {}),
                     **({"flag": _flag_data(p.flag, prof, project_dir)} if p.flag else {})})
    cards = []
    for i, c in enumerate(sc.cards):
        img = None
        if c.asset_ref:
            cr = tl.credit_for(ref=c.asset_ref)
            if cr is None or not cr.path:
                raise EngineError(f"card '{c.title}': asset_ref '{c.asset_ref}' has no credits_manifest entry with a path")
            img = cr.path
        cards.append({"title": c.title, "subtitle": c.subtitle, "img": img, "top": round(card_top[c.position] * S.height),
                      "at_frame": card_frame(c, n, S.fps)})
    ecol = entities.scene_colours(seg, tl, project_dir)   # vivid on a dark map (theme vivid_chroma_scale)
    if theme["label_style"] == "box":
        for c, cd in zip(sc.cards, cards):
            cd["box"] = entities.box_colour(ecol[c.entity], theme) if c.entity else theme["label_box_colour"]
    out = {"fps": S.fps, "n_frames": n, "width": S.width, "height": S.height, "map": sc.map_asset,
           "map_size": [round(mw, 2), round(mh, 2)], "camera": {"start": cam["start"], "end": cam["end"], "legs": cam["legs"],
                                                               **({"tilt": {"p": camera3d.perspective_px(S.height)}}
                                                                  if "t" in cam["start"] else {})},
           "motion_px": round(motion, 2), "pins": pins, "cards": cards, "theme": theme}
    if C3:
        out["cam3d"] = C3
    if U.landscape:   # JS reads S.units (k, ref, safe) instead of the portrait W/1080, H/1920; portrait scenes keep their key
        out["units"] = U.scene()
    if (mq := prof.render.map_jpeg_quality) and mq != MAP_JPEG_QUALITY:
        out["map_q"] = mq
    if sc.regions:
        out["regions"] = [region_data(r, ecol, theme, mw, mh, G, S, lay is not None, prof.fx) for r in sc.regions]
        for r, data in zip(sc.regions, out["regions"]):
            ra = region_aspect([p for ring in r.all_rings() for p in ring], mw, mh, G is not None)
            if r.flag:
                data["flag"] = _flag_data(r.flag, prof, project_dir, ra)
            for change, target in zip(r.changes, data["changes"]):
                if change.flag:
                    target["flag"] = _flag_data(change.flag, prof, project_dir, ra)
    if sc.borders:
        out["borders"] = [{"points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in b.points], "at_frame": b.at_frame,
                           "draw_s": theme["border_draw_s"] if b.draw_s is None else b.draw_s, "colour": b.colour.lower(),
                           "dashed": b.dashed, "closed": b.closed,
                           **({"w": b.width_px} if b.width_px is not None else {}),
                           **({"glow": b.glow_px} if b.glow_px is not None else {}),
                           **({"grow": b.grow} if b.grow else {}),
                           **({"nocase": True} if not b.casing else {})} for b in sc.borders]
    if sc.map_texts:
        out["map_texts"] = [{"text": m.text, "points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in m.points],
                             "at_frame": m.at_frame, "out_frame": m.out_frame, "reveal_s": m.reveal_s, "px": m.px,
                             "colour": m.colour.lower(), "depth": m.depth_px} for m in sc.map_texts]
    col = ecol
    if sc.title:
        out["title"] = {"text": sc.title, "entity": sc.title_entity,
                        "colour": tint(col.get(sc.title_entity, "#ffffff"), theme["title_tint"]),
                        "at_frame": sc.title_at_frame or 0, "top": round(title_top * S.height)}
        if prof.theme.title_anchor == "region" and lay is None and not G and not C3:
            if (at := title_place(sc, cam, mw, mh, tl, theme["title_px"])) is not None:
                out["title"]["left"], out["title"]["top"] = at
    if sc.figures:
        out["figures"] = [{"kind": f.kind, "text": figure_text(f, f.value, theme), "value": f.value, "from": f.count_from,
                           "decimals": f.decimals, "prefix": f.prefix, "suffix": f.suffix, "motion": f.motion,
                           "count_s": f.count_s or theme["counter_s"], "at_frame": f.at_frame,
                           "x": round(f.position[0] * (mw if f.anchor == "map" else S.width), 2),
                           "y": round(f.position[1] * (mh if f.anchor == "map" else S.height), 2),
                           **({"px": f.px} if f.px else {}), **({"map": True} if f.anchor == "map" else {}),
                           "entity": f.entity, "colour": col.get(f.entity) or _accent(f, prof.theme) or "#ffffff",
                           **({"accent": _accent(f, prof.theme)} if not f.entity and _accent(f, prof.theme) else {})}
                          for f in sc.figures]
    if sc.thermometers:
        starts = components.thermo_starts(sc.thermometers)
        out["thermometers"] = [components.thermo_data(t, S.width, S.height, start)
                               for t, start in zip(sc.thermometers, starts)]
    if sc.flows:
        out["flows"] = [components.flow_data(f, mw, mh, fx._seed(tl, seg)) for f in sc.flows]
        out["flow_legend"] = sc.flow_legend
    boxed = theme["label_style"] == "box" and (lay is None or prof.print is None)
    if boxed and out.get("title"):
        t = out["title"]
        t["box"] = entities.box_colour(col[t["entity"]], theme) if t["entity"] else theme["label_box_colour"]
    for fm, f in zip(sc.figures or [], out.get("figures", [])):
        on = fm.box if fm.box is not None else boxed
        if not on:
            continue
        if fm.box is None and f["kind"] == "year" and theme["year_box_colour"]:
            f.update(box=theme["year_box_colour"], ink=theme["year_box_ink"], px=theme["year_box_px"],
                     texture=theme["year_box_texture"], dim=texture_dim(theme["year_box_texture"]))
        else:
            f["box"] = theme["label_box_colour"]
    if prof.legibility:
        out["legibility"] = {**prof.legibility.model_dump(), "v": legibility.code_hash()}
        for t in [out.get("title"), *out.get("figures", [])]:
            if t:
                t["entity_colour"] = col.get(t["entity"]) if t.get("entity") else None
        for r in out.get("regions", []):
            for x in (r, *r["changes"]):
                x["casing"] = legibility.line_casing(r["edge_colour"] if r["style"] == "block" else x["colour"],
                                                     out["legibility"])
        for b in out.get("borders", []):
            b["casing"] = legibility.line_casing(b["colour"], out["legibility"])
    if prof.print:
        surf = printstyle.surface(project_dir / sc.map_asset, prof.print)
        out["print"] = printstyle.scene_block(prof.print, surf)
        for r in out.get("regions", []):
            for x in (r, *r["changes"]):
                x["edge_ink"] = printstyle.edge_ink(x["colour"], prof.print, surf)
    if sc.routes:
        out["routes"] = [{"points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in r.points], "at_frame": r.at_frame,
                          "draw_s": r.draw_s or theme["route_draw_s"]} for r in sc.routes]
    if lay is not None:
        out["layout"] = layout.scene_layout(lay)
        for r, pts in zip(out.get("routes", []), lay.routes):
            r["points"] = [[round(x, 2), round(y, 2)] for x, y in pts]
    if sc.layers:
        out["layers"] = parallax.scene_layers(seg, tl, project_dir)
    if props.has_props(seg):
        out["props"] = props.scene_props(seg, tl, project_dir, out["camera"], mw, mh)
    if G:
        out["globe"] = G
    elif (dp := deep_plan(out, (iw, ih))) is not None:
        out["deep"] = deep_entries(project_dir / sc.map_asset, dp, _q(out))
    if sc.morph:
        out["morph"] = morph_data(sc.morph, out, project_dir, (iw, ih))
    out.update(fx.scene_block(seg, tl, project_dir, out, sc, prof, pins))
    if seg.overlays:
        from . import overlays as _ov
        drawn = _ov.drawn(seg)
        if drawn:
            out["overlays"] = [
                {"id": o.id, "kind": o.kind, "box": [round(v, 2) for v in o.box], "text": o.text,
                 **({"subtitle": o.subtitle} if o.subtitle else {}),
                 **({"on": o.on} if o.on else {}),
                 "start_frame": _ov.frame_range(o, seg)[0], "end_frame": _ov.frame_range(o, seg)[1]}
                for o in drawn
            ]
    _bounded_put(_SCENES, key, copy.deepcopy(out), SCENE_CACHE_MAX)
    return out


_SCENES: dict[str, dict] = {}   # process-local: scene key -> scene_data, bounded by SCENE_CACHE_MAX


def _centroid(poly: list[list[float]]) -> tuple[float, float]:
    """Area centroid of a polygon (vertex mean when its area is 0)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        k = x0 * y1 - x1 * y0
        a, cx, cy = a + k, cx + (x0 + x1) * k, cy + (y0 + y1) * k
    if abs(a) < 1e-12:
        return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    return cx / (3 * a), cy / (3 * a)


def region_data(r, col: dict, theme: dict, mw: float, mh: float, G: dict | None, S, beats: bool, fxc=None) -> dict:
    """Scene entry of a region: points in map px (globe scenes: `ll` lat/lng per point and the points projected at
    the region's first frame), style colours of every owner (`changes`) and its box label. The theme region_style
    applies to every scene; a region's own style wins. The theme label_style or the region's own `box` decides
    whether its label gets a solid box."""
    style = r.style or theme["region_style"]
    boxed = r.box if r.box is not None else theme["label_style"] == "box"

    def paint(e: str) -> dict:
        c = col[e]
        out = {"entity": e, "colour": c}
        if style == "block":
            b = entities.block_colour(c, theme)
            L, C, h = legibility.rgb_oklch(legibility.hex_rgb(b))
            out.update(block=b, side=legibility.oklch_hex(max(0.0, L - theme["block_side_dl"]), C, h),
                       bevel=tint(b, theme["block_bevel_tint"]))
        if r.label and boxed:
            out["box"] = entities.box_colour(c, theme)
        return out

    def xy(pts: list[list[float]], f: int) -> tuple[list, list | None]:
        if not G:
            return [[round(x * mw, 2), round(y * mh, 2)] for x, y in pts], None
        ll = [[round(90 - y * 180, 5), round(x * 360 - 180, 5)] for x, y in pts]
        v = globe.camera_at(G, f, S.fps)
        return [list(globe.project(v, a, b, S.width, S.height) or (-1000.0, -1000.0)) for a, b in ll], ll
    pts, ll = xy(r.polygon, r.at_frame)
    out = {**paint(r.entity), "edge": r.edge or theme["region_edge"], "style": style, "points": pts,
           "at_frame": r.at_frame, "changes": [{**paint(c.entity), "at_frame": c.at_frame} for c in r.changes]}
    if r.rings:   # islands and holes: the template draws one even-odd path; the flag spans the bbox of every ring
        rings = [pts] + [xy(ring, r.at_frame)[0] for ring in r.rings]
        xs, ys = [p[0] for g in rings for p in g], [p[1] for g in rings for p in g]
        out.update(rings=rings, bbox=[min(xs), min(ys), max(xs), max(ys)])
    if ll:
        out["ll"] = ll
    if style == "block":
        out.update(edge_colour=(r.edge_colour or theme["block_edge_colour"]).lower(), dashed=r.dashed,
                   draw_s=theme["block_draw_s"] if r.draw_s is None else r.draw_s)
    if r.label:
        f = r.at_frame if r.label_at_frame is None else r.label_at_frame
        (lp,), lll = xy([list(r.label_at or _centroid(r.polygon))], f)
        out["label"] = {"text": r.label, "x": round(lp[0], 2), "y": round(lp[1], 2), "at_frame": f,
                        **({"ll": lll[0]} if lll else {})}
    if r.hatch and fxc is not None:
        out["hatch"] = fx.hatch_data(fxc.hatch, r.hatch)
    return out


def figure_text(f, v: float, theme: dict) -> str:
    """On-screen text of figure f showing value v: years as plain digits; numbers rounded half up to f.decimals with
    the theme separators (same rule as the template counter)."""
    if f.kind == "year":
        return f"{f.prefix}{int(v)}{f.suffix}"
    m = 10 ** f.decimals
    n = math.floor(abs(v) * m + 0.5)
    whole = f"{n // m:,}".replace(",", theme["number_group"])
    frac = f"{theme['number_decimal']}{n % m:0{f.decimals}d}" if f.decimals else ""
    return f"{f.prefix}{'-' if v < 0 and n else ''}{whole}{frac}{f.suffix}"


# ease-in curves exactly as vendor/js/gsap.min.js defines them (its Expo lands on 0 and 1: 2^(10(p-1)) p + p^6 (1-p))
_GSAP_IN = {"none": lambda p: p, "linear": lambda p: p, "power0": lambda p: p,
            "sine": lambda p: 1.0 if p == 1 else 1 - math.cos(p * math.pi / 2),
            "expo": lambda p: 2 ** (10 * (p - 1)) * p + p ** 6 * (1 - p),
            "circ": lambda p: 1 - math.sqrt(max(0.0, 1 - p * p))}
_GSAP_POWER = {"power1": 2, "quad": 2, "power2": 3, "cubic": 3, "power3": 4, "quart": 4, "power4": 5, "quint": 5,
               "strong": 5}


def gsap_ease(name: str):
    """Python twin of a GSAP 3 ease name ("power2.out", "sine.inOut", "back.out(1.7)", "none"): the ease-in curve of the
    family, mirrored for .out and joined at 0.5 for .inOut exactly as GSAP builds them; a bare family is .out."""
    fam, _, kind = name.strip().partition(".")
    kind, _, arg = kind.partition("(")
    fam, kind = fam.lower(), kind or "out"
    if fam in _GSAP_POWER:
        n = _GSAP_POWER[fam]
        e_in = lambda p: p ** n   # noqa: E731
    elif fam == "back":
        s = float(arg.rstrip(")")) if arg else 1.70158
        e_in = lambda p: p * p * ((s + 1) * p - s)   # noqa: E731
    elif fam in _GSAP_IN:
        e_in = _GSAP_IN[fam]
    else:
        raise EngineError(f"ease {name!r}: counter figures mirror the GSAP power, sine, expo, circ, back and none eases")
    if fam in ("none", "linear", "power0") or kind == "in":
        return e_in
    if kind == "out":
        return lambda p: 1 - e_in(1 - p)
    if kind == "inOut":
        return lambda p: e_in(2 * p) / 2 if p < 0.5 else 1 - e_in(2 * (1 - p)) / 2
    raise EngineError(f"ease {name!r}: unknown ease type {kind!r} (in, out or inOut)")


def counter_value(f, frame: int, fps: int, count_s: float, ease: str = "power2.out") -> float:
    """Value a counter figure shows at `frame` (relative to the segment start): the template tweens it from count_from
    to value with the profile's [motion] entrance ease (default power2.out, the cubic ease-out)."""
    p = min(1.0, max(0.0, (frame - f.at_frame) / fps / count_s))
    return f.count_from + (f.value - f.count_from) * gsap_ease(ease)(p)


def _accent(f, th) -> str | None:
    """theme figure_accent for a number figure (not a year) without an entity; None otherwise."""
    return th.figure_accent.lower() if th.figure_accent and f.kind != "year" and not f.entity else None


def title_place(sc, cam: dict, mw: float, mh: float, tl: Timeline, px: float) -> tuple[float, float] | None:
    """(left, top) in output px of a scene title above its title_entity's region (theme title_anchor "region"): the
    region's on-screen box at the title's at_frame (layout.camera_at), the title TITLE_GAP_EM above it, or below it
    when that would leave the safe area; horizontally centred on the region and kept inside the side margins at an
    estimated width. None when no region of the title entity exists."""
    from . import layout
    rings = [ring for r in sc.regions or [] if r.entity == sc.title_entity for ring in r.all_rings()]
    if not rings:
        return None
    st = layout.camera_at(cam, sc.title_at_frame or 0)
    xs = [x * mw * st["s"] + st["x"] for ring in rings for x, _ in ring]
    ys = [y * mh * st["s"] + st["y"] for ring in rings for _, y in ring]
    S, (sx, top_min, bottom_y) = tl.settings, units(tl).safe()   # side margin, safe top y, safe bottom y (output px)
    h, gap = px * 1.04, px * TITLE_GAP_EM
    top = min(ys) - gap - h
    if top < top_min:
        top = max(ys) + gap
    top = min(max(top, top_min), bottom_y - h)
    w = min(900.0 * S.width / 1080, 0.62 * px * len(sc.title))
    left = min(max((min(xs) + max(xs)) / 2, sx + w / 2), S.width - sx - w / 2)
    return round(left, 1), round(top, 1)


def figure_end_frame(f, fps: int, default_count_s: float, pop: str = "elastic") -> int:
    """Frame (relative to the segment start) at which a figure's entrance ends: a bounce lasts FIGURE_POP_S[pop] (theme
    figure_pop), a counter its count_s."""
    return f.at_frame + math.ceil((FIGURE_POP_S[pop] if f.motion == "bounce" else (f.count_s or default_count_s)) * fps)


def sfx_events(tl: Timeline) -> list[dict]:
    """Sound cue events of the HyperFrames scenes: one "counter_tick" per frame on which a counter's shown text
    changes, at the absolute frame."""
    th, fps, out = profiles.of(tl).render_values("hf"), tl.settings.fps, []
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES:
            continue
        for i, f in enumerate(s.gsap_scene_config.figures or []):
            if f.motion != "counter":
                continue
            cs, ease = f.count_s or th["counter_s"], th["motion"]["entrance"]   # the template's MO.entrance
            prev = figure_text(f, f.count_from, th)
            for fr in range(f.at_frame + 1, figure_end_frame(f, fps, th["counter_s"]) + 1):
                txt = figure_text(f, counter_value(f, fr, fps, cs, ease), th)
                if txt != prev:
                    out.append({"kind": "counter_tick", "frame": s.start_frame + fr, "seg": s.seg_index, "figure": i})
                prev = txt
    return out


def route_end_frame(r, fps: int, default_draw_s: float) -> int:
    """Frame (relative to the segment start) at which a route's arrowhead reaches the last point."""
    return r.at_frame + math.ceil((r.draw_s or default_draw_s) * fps)


PROPS_JS = config.PKG / "hf" / "props.js"   # props layer script; in the build dir and the key of prop segments only
FX_BACKDROP_JS = config.PKG / "hf" / "fx_backdrop.js"
FX_TIMEZONE_JS = config.PKG / "hf" / "fx_timezones.js"
FX_BUBBLE_JS = config.PKG / "hf" / "fx_bubbles.js"
SCENE3D_JS = config.PKG / "hf" / "scene3d.js"   # full 3D camera (camera_full3d.py), an ES module like globe.js
FX2_JS = fx.FX2_JS


def script_files(scene: dict) -> list[Path]:
    """Scripts a scene adds to the template: hf/props.js (props), hf/parallax.js (layers), the Stage 4a/4b effect
    scripts and hf/globe.js (globe, a module)."""
    return ([PROPS_JS] if scene.get("props") else []) + ([parallax.JS] if scene.get("layers") else []) + \
        ([FX_BACKDROP_JS] if scene.get("backdrop") else []) + \
        ([FX_TIMEZONE_JS] if scene.get("timezones") else []) + \
        ([FX_BUBBLE_JS] if scene.get("bubbles") else []) + \
        ([FX2_JS] if any(scene.get(k) for k in ("flag_characters", "cutaway", "insets", "particles")) else []) + \
        ([globe.JS] if scene.get("globe") else []) + ([SCENE3D_JS] if scene.get("cam3d") else [])


def _card_name(src: Path) -> str:
    """Build-dir name of a card image: its content hash, so two images with one basename never share a file."""
    return f"card_{blake3_files([src])[0][:32]}{src.suffix.lower()}"


def split_panels(seg: Segment, tl: Timeline) -> list[tuple[Segment, Timeline]]:
    """A split segment as two (segment, timeline) panels at the panel size: the top panel is the scene without its
    split, the bottom one split.second; both keep the segment's frames. A plain segment is one panel: itself."""
    sp = seg.gsap_scene_config.split if seg.gsap_scene_config else None
    if sp is None:
        return [(seg, tl)]
    ph = (tl.settings.height - sp.divider_px) // 2
    if ph % 2:
        raise EngineError(f"split: frame height {tl.settings.height} minus divider {sp.divider_px} must give even panels")
    tlp = tl.model_copy(update={"settings": tl.settings.model_copy(update={"height": ph})})
    top = seg.model_copy(update={"gsap_scene_config": seg.gsap_scene_config.model_copy(update={"split": None})})
    return [(top, tlp), (seg.model_copy(update={"gsap_scene_config": sp.second}), tlp)]


def keys(seg: Segment, tl: Timeline, project_dir: Path) -> tuple[str, str]:
    """(segment cache key, map cache key) without writing files; equal to the keys `render` uses. A split segment's
    key covers both panels, the divider and the frame size."""
    if seg.gsap_scene_config.split is not None:
        sp = seg.gsap_scene_config.split
        ks = [keys(s, t, project_dir) for s, t in split_panels(seg, tl)]
        return blake3_json({"split": [k[0] for k in ks], "divider": [sp.divider_px, sp.divider_colour],
                            "size": [tl.settings.width, tl.settings.height]}), ks[0][1]
    scene = scene_data(seg, tl, project_dir)
    mk = globe.tex_key(project_dir / scene["map"]) if "globe" in scene else map_key(project_dir / scene["map"], _need_w(scene), _q(scene))
    for c in scene["cards"]:
        if c["img"]:
            c["img"] = "assets/" + _card_name(project_dir / c["img"])
    return _key(scene, [config.TEXTURES_DIR / f for f in textures(scene)] + script_files(scene), mk, tl), mk


def build_dir(seg: Segment, tl: Timeline, project_dir: Path, out_dir: Path) -> tuple[Path, dict]:
    """Write the per-segment HyperFrames project. Returns (dir, scene)."""
    S = tl.settings
    scene = scene_data(seg, tl, project_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    sc = seg.gsap_scene_config
    flags = [p.flag for p in sc.pins]
    flags += [f for r in sc.regions or [] for f in [r.flag, *(c.flag for c in r.changes)]]
    flags += [c.flag for c in sc.flag_characters or []]
    for flag in (f for f in flags if f):
        asset = assets.resolve(assets.flag_for(flag.code, flag.variant), project=project_dir, kind="flag")
        shutil.copyfile(asset.path, out_dir / "assets" / f"{asset.sha256[:16]}.svg")
    if "globe" in scene:
        for name, f in globe.files(seg, project_dir, scene["globe"]).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        (out_dir / "assets" / "three").mkdir()
        for f in globe.THREE_FILES:
            shutil.copyfile(f, out_dir / "assets" / "three" / f.name)
    else:
        if "cam3d" in scene:
            (out_dir / "assets" / "three").mkdir()
            for f in globe.THREE_FILES:
                shutil.copyfile(f, out_dir / "assets" / "three" / f.name)
        mp = _map_copy(project_dir / scene["map"], _need_w(scene), _q(scene))
        shutil.copyfile(mp, out_dir / "assets" / "map.jpg")
        for name, f in deep_tiles(project_dir / scene["map"], scene).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        if m := scene.get("morph"):
            src = project_dir / m["asset"]
            shutil.copyfile(_map_copy(src, _need_w(scene), _q(scene)), out_dir / m["img"])
            for name, f in deep_tiles(src, {"deep": m["deep"]}).items():
                shutil.copyfile(f, out_dir / "assets" / name)
    for c in scene["cards"]:
        if c["img"]:
            dst = out_dir / "assets" / _card_name(project_dir / c["img"])
            shutil.copyfile(project_dir / c["img"], dst)
            c["img"] = "assets/" + dst.name
    for name, src in (props.sources(seg, project_dir, S.fps).items() if scene.get("props") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    for name, src in (parallax.sources(seg, project_dir).items() if scene.get("layers") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    for name, src in (fx.sources(seg, project_dir).items() if scene.get("insets") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    shutil.copyfile(config.GSAP_JS, out_dir / "assets" / "gsap.min.js")
    for f in textures(scene):
        shutil.copyfile(config.TEXTURES_DIR / f, out_dir / "assets" / f)
    for f in font_files(scene):
        shutil.copyfile(config.FONTS_DIR / f, out_dir / "assets" / f)
    dur = seg.n_frames / S.fps
    html = (config.TEMPLATE_DIR / "index.html").read_text()
    for k, v in {"__W__": S.width, "__H__": S.height, "__DUR__": f"{dur:.6f}",
                 "__MAP__": scene["globe"]["tex"] if "globe" in scene else "assets/map.jpg",
                 "__MW__": scene["map_size"][0], "__MH__": scene["map_size"][1], "__FONTFACES__": font_faces(scene)}.items():
        html = html.replace(k, str(v))
    for f in script_files(scene):
        shutil.copyfile(f, out_dir / f.name)
        mod = ' type="module"' if f in (globe.JS, SCENE3D_JS) else ""
        html = html.replace("</body>", f'<script{mod} src="{f.name}"></script>\n</body>', 1)
    (out_dir / "index.html").write_text(html)
    (out_dir / "scene.js").write_text("window.SCENE=" + json.dumps(scene, ensure_ascii=False) + ";\n")
    if "legibility" in scene:
        scene["inks"] = _inks(seg, tl, project_dir, out_dir, scene)
        (out_dir / "scene.js").write_text("window.SCENE=" + json.dumps(scene, ensure_ascii=False) + ";\n")
    return out_dir, scene


_INKS: dict[str, dict] = {}   # in-process memo: segment key -> scene inks (the key covers scene, files, template)


def _inks(seg: Segment, tl: Timeline, project_dir: Path, seg_dir: Path, scene: dict) -> dict:
    """Adaptive text styles of a legibility segment: probe the built (ink-less) segment at every element sample time
    with the text hidden and as a glyph mask, then `legibility.choose` per element."""
    key = keys(seg, tl, project_dir)[0]
    if key in _INKS:
        return _INKS[key]
    lg, S = scene["legibility"], tl.settings
    snap = seg_dir.parent / f"{seg_dir.name}_ink"
    try:
        probe = probe_dom(seg_dir, legibility.all_times(scene, lg), S.width, S.height, snap, bg="bgonly", ref=key)
        samples = legibility.collect(scene, lg, probe)
    finally:
        shutil.rmtree(snap, ignore_errors=True)
    choices = {e["id"]: legibility.choose(e, samples[e["id"]], lg, S.fps)
               for e in legibility.elements(scene) if samples.get(e["id"])}
    inks = legibility.scene_inks(choices)
    if lg.get("glow_colour") == "entity":     # light text glows in its entity colour only (none without one)
        for e in legibility.elements(scene):
            if e["id"] in inks:
                inks[e["id"]]["glow"] = e.get("colour")
    _bounded_put(_INKS, key, inks, INK_CACHE_MAX)
    return inks


def cache_key(seg: Segment, tl: Timeline, project_dir: Path, scene: dict | None = None) -> str:
    """Segment cache key (`scene` is accepted for call-site symmetry; the key is derived from the timeline)."""
    return keys(seg, tl, project_dir)[0]


def encode_settings(tl: Timeline) -> tuple[str, int, list[str]]:
    """(capture quality, HyperFrames CRF, normalising x264 args) of a timeline: profile [render], else HF_QUALITY,
    HF_CRF and config.X264_SEGMENT."""
    r = profiles.of(tl).render
    return r.hf_quality or HF_QUALITY, HF_CRF if r.hf_crf is None else r.hf_crf, r.x264("segment")


def _key(scene: dict, files: list[Path], mk: str, tl: Timeline) -> str:
    """Segment key: engine render version, capture/encode settings, the scene (every profile value that reaches the
    template, card and prop images by content-hash name), the map derivative key, content hashes of the textures,
    the scripts the scene adds (script_files) and the fonts copied into the build dir, and the toolchain identity
    (toolchain.hf_identity), and the engine source that writes the build dir (CODE)."""
    q, crf, x264 = encode_settings(tl)
    return blake3_json({"v": HF_VERSION, "code": code_hash(CODE), "enc": [q, crf], "x264": x264, "algo": core.ALGO_VERSION,
                        "scene": scene, "map": mk, "files": blake3_files(files),
                        "fonts": blake3_files([config.FONTS_DIR / f for f in font_files(scene)]),
                        "toolchain": toolchain.hf_identity()})


def render(seg: Segment, tl: Timeline, project_dir: Path, workdir: Path, workers: int = 3,
           use_cache: bool = True) -> tuple[Path, bool]:
    key = keys(seg, tl, project_dir)[0]
    if use_cache and (hit := cache.get_file("segments", key)):
        return hit, True
    if seg.gsap_scene_config.split is not None:
        return _render_split(seg, tl, project_dir, workdir, workers, use_cache, key), False
    hf_dir = workdir / "hf" / f"seg{seg.seg_index:03d}"
    kfile = workdir / "hf" / f"seg{seg.seg_index:03d}.key"
    if (hf_dir / "index.html").is_file() and (hf_dir / "scene.js").is_file() and kfile.is_file() and kfile.read_text() == key:
        d = hf_dir
    else:
        d, _ = build_dir(seg, tl, project_dir, hf_dir)
        kfile.write_text(key)
    out = workdir / "seg" / f"seg{seg.seg_index:03d}_hf.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.mp4")
    q, crf, x264 = encode_settings(tl)
    run([config.NODE, hfversion.require().cli, "render", d, "--fps", str(tl.settings.fps), "--sdr", "-q", q, "--crf", str(crf),
         "--workers", str(workers), "-o", raw], env=config.hf_env(), timeout=900)
    n = seg.n_frames
    if _is_engine_format(raw, tl, n):
        raw.replace(out)
    else:
        # normalise to the engine's exact BT.709 stream format and frame count so concat demuxer joins Native + HF seamlessly
        run([config.FFMPEG, "-v", "error", "-y", "-i", raw, "-an", "-vf",
             f"fps={tl.settings.fps},tpad=stop_mode=clone:stop=-1,setsar=1,{config.BT709_VF}", *x264,
             "-frames:v", str(n), "-movflags", "+faststart", out])
        raw.unlink(missing_ok=True)
    streams.verify(out, n, tl.settings.fps, f"segment {seg.seg_index} (HyperFrames)")
    return (cache.put_file("segments", key, out) if use_cache else out), False


def _render_split(seg: Segment, tl: Timeline, project_dir: Path, workdir: Path, workers: int, use_cache: bool,
                  key: str) -> Path:
    """Both panels rendered by HyperFrames (each its own scene, map and camera, same frames), stacked top / divider /
    bottom into the segment file; nothing animated outside HyperFrames."""
    sp, n, S = seg.gsap_scene_config.split, seg.n_frames, tl.settings
    files = []
    for k, (s, t) in enumerate(split_panels(seg, tl)):
        sub = workdir / "split" / f"seg{seg.seg_index:03d}_{k}"
        p, _ = render(s.model_copy(update={"seg_index": seg.seg_index}), t, project_dir, sub, workers, use_cache)
        files.append(p)
    out = workdir / "seg" / f"seg{seg.seg_index:03d}_hf.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    _, _, x264 = encode_settings(tl)
    run([config.FFMPEG, "-v", "error", "-y", "-i", files[0], "-i", files[1], "-f", "lavfi", "-i",
         f"color=c={sp.divider_colour}:s={S.width}x{sp.divider_px}:r={S.fps}", "-filter_complex",
         "[0:v][2:v][1:v]vstack=inputs=3,setsar=1[v]", "-map", "[v]", "-an", *x264, "-frames:v", str(n), out])
    streams.verify(out, n, S.fps, f"segment {seg.seg_index} (split screen)")
    return cache.put_file("segments", key, out) if use_cache else out


def _is_engine_format(p: Path, tl: Timeline, n: int) -> bool:
    """HyperFrames output usable directly as the segment file without normalising: matches the engine segment stream
    parameters (codec, pix_fmt, dimensions, frame rate, time_base, colour tags, SPS/PPS extradata) and has exactly
    n frames from 0 lasting n / fps (streams.verify)."""
    from . import native
    S = tl.settings
    _, _, x264 = encode_settings(tl)
    if not native.copy_eligible(p, S.width, S.height, S.fps, x264):
        return False
    try:
        streams.verify(p, n, S.fps, "HyperFrames output")
    except EngineError:
        return False
    return True


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@contextlib.contextmanager
def serve(directory: Path):
    """Static server on a dynamic port (bind port 0). Yields the base URL."""
    h = functools.partial(_QuietHandler, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


PROBE_JS = config.PKG / "hf" / "dom_probe.mjs"
PROBE_VERSION = "dom-1"   # bump when the cached probe result format or the probe call changes
SHOT_FIELDS = ("png", "bg", "mask", "line_bg", "line_mask")   # screenshot paths of one probe time item


def probe_key(seg_dir: Path, times: list[float], width: int, height: int, snaps: bool, bg: str) -> str:
    """Cache key of a DOM probe: the content of every file of the built segment dir (paths relative to it: html,
    scene.js, assets, scripts; a globe or 3D camera script also switches the probe browser to WebGL), the probe times as passed to
    the probe (4 dp), the viewport, whether screenshots are taken and the bg mode, the toolchain identity (node,
    Chrome, npm tree, GSAP, template, dom_probe.mjs, ffmpeg) and the engine source that runs the probe (CODE)."""
    files = sorted(p for p in Path(seg_dir).rglob("*") if p.is_file())
    return blake3_json({"v": PROBE_VERSION, "code": code_hash(CODE), "toolchain": toolchain.hf_identity(),
                        "dir": list(zip((str(p.relative_to(seg_dir)) for p in files), blake3_files(files))),
                        "times": [f"{t:.4f}" for t in times], "size": [int(width), int(height)],
                        "snaps": bool(snaps), "bg": bg})


def _probe_cached(name: str, snap_dir: Path | None) -> dict | None:
    """The cached probe result `name`, its screenshots copied into snap_dir; None when the JSON or any screenshot of
    the entry is missing (an entry LRU eviction broke counts as a miss)."""
    res = cache.get_json("dom", name)
    if res is None:
        return None
    shots = []
    for item in res["times"]:
        for f in SHOT_FIELDS:
            if item.get(f):
                if snap_dir is None or (hit := cache.get_file("dom", f"{name}.{item[f]}", "")) is None:
                    return None
                shots.append((f, item, hit))
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)
    for f, item, hit in shots:
        dst = snap_dir / item[f]
        shutil.copyfile(hit, dst)
        item[f] = str(dst)
    return res


def _probe_store(name: str, res: dict) -> None:
    """Store a probe result: every screenshot first (under <name>.<file name>), the JSON with bare file names last,
    so a present JSON means a complete entry."""
    out = json.loads(json.dumps(res))
    for item in out["times"]:
        for f in SHOT_FIELDS:
            if item.get(f):
                src = Path(item[f])
                cache.put_file("dom", f"{name}.{src.name}", src, "")
                item[f] = src.name
    cache.put_json("dom", name, out)


def probe_dom(seg_dir: Path, times: list[float], width: int, height: int, snap_dir: Path | None = None,
              bg: str = "", ref: str | None = None, timeout: int | None = None) -> dict:
    """DOM probe (hf/dom_probe.mjs); bg "bg" (needs snap_dir) adds the text-hidden and glyph-mask screenshots,
    "bgonly" takes only those. Results are cached (kind "dom", probe_key; `ref` = the segment cache key of the probed
    segment, the entry name prefix `engine cache --prune` keeps entries by); a hit spawns no browser and copies the
    screenshots into snap_dir under the names the probe gives them. "cached" in the result: True on a hit."""
    name = f"{ref or 'adhoc'}.{probe_key(seg_dir, times, width, height, snap_dir is not None, bg)}"
    if (hit := _probe_cached(name, snap_dir)) is not None:
        hit["cached"] = True
        return hit
    env = config.hf_env()
    env["ENGINE_PUPPETEER"] = str(config.PUPPETEER)
    env["ENGINE_CHROME"] = str(config.CHROME)
    if (seg_dir / globe.JS.name).exists() or (seg_dir / SCENE3D_JS.name).exists():
        env["ENGINE_WEBGL"] = "1"     # globe and 3D camera scenes: SwiftShader WebGL in the probe browser
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)
    t_limit = timeout if timeout is not None else (120 + (2 * len(times) if bg else 0))
    with serve(seg_dir) as url:
        out = run([config.NODE, PROBE_JS, url + "/index.html", snap_dir or "-",
                   ",".join(f"{t:.4f}" for t in times), width, height, *([bg] if bg else [])], env=env,
                  timeout=t_limit)
    res = json.loads(out)
    _probe_store(name, res)
    res["cached"] = False
    return res


def probe_props(seg_dir: Path, times: list[float], width: int, height: int) -> dict:
    """Props probe (hf/props_probe.mjs): the #cam transform and every prop element's centre and size per time."""
    env = config.hf_env()
    env["ENGINE_PUPPETEER"] = str(config.PUPPETEER)
    env["ENGINE_CHROME"] = str(config.CHROME)
    with serve(seg_dir) as url:
        out = run([config.NODE, config.PKG / "hf" / "props_probe.mjs", url + "/index.html",
                   ",".join(f"{t:.6f}" for t in times), width, height], env=env, timeout=120)
    return json.loads(out)


