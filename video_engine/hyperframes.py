"""HyperFrames path: map/pin/card motion-graphics segments only (no captions in HTML). One HTML project per segment in
/dev/shm, rendered with the vendored HyperFrames CLI; the same HTML is probed by the DOM gate (R63) on a dynamic port."""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import math
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import hypoverum_core_rs as core

from . import camera3d, cache, globe, parallax, config, entities, legibility, printstyle, profiles, props, streams, toolchain
from .timeline import HYPERFRAMES, Segment, Timeline
from .util import EngineError, blake3_files, blake3_json, code_hash, run

CODE = (Path(__file__),)   # writes the build dir (html, scene.js); legibility.py is keyed in the scene (legibility.code_hash)
HF_VERSION = "hf-2"   # hf-2: capture/encode profile HF_QUALITY + HF_CRF
MAP_VERSION = "map-1"   # bump when _map_copy output changes
MAP_JPEG_QUALITY = 90
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
    [print] the roles of its surface instead of the area-label title font."""
    th = scene.get("theme", {})
    used = {th.get("label_font"), th.get("title_font")}
    if any(f["kind"] == "year" for f in scene.get("figures", [])):
        used.add("serif")
    if pr := scene.get("print"):
        used |= {r[0] for r in pr["roles"].values()}
    elif scene.get("layout", {}).get("areas"):
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
MAP_MAX_SIDE = 3000     # widest whole-map derivative; deeper zoom shows deep tiles over it (deep_plan)
DEEP_TILE = 1024        # deep tile side (deep raster px)
DEEP_PAD = 2            # deep raster px each tile reaches into its right and bottom neighbours (no seams)
DEEP_MARGIN = 0.05      # viewport growth (fraction of its size) when picking the tiles a frame needs
DEEP_VERSION = "deep-1" # bump when deep tile output changes
MORPH_PAPER, MORPH_GRAIN = "paper-F1E8D4.jpg", "grain-512.png"   # vendor/textures of a map morph
# HyperFrames capture/encode profile for the intermediate segment file (re-encoded once more by the build).
# "draft" = JPEG q80 screenshot + x264 ultrafast; CRF 14 keeps the intermediate close to lossless.
HF_QUALITY = "draft"
HF_CRF = 14
CARD_TOP = {"top": 0.12, "bottom": 0.77}
TITLE_TOP = 0.11                       # big title top edge (fraction of the frame height)



def map_image_size(path: Path) -> tuple[int, int]:
    from .native import image_size
    return image_size(path)


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
    key's move_s, without blur. leg_motion = the visible motion of each key's move (checked by R56). After the last key
    a drift runs to the segment end so the camera never stops. With theme `camera_pulse` (profile [camera]) every
    drift is split by `drift_legs` into drifts and pulses; a key's `pulse` sets the pulse sizes of the wait on its view
    (0 = drift only) and its `drift_max` bounds the drift of that wait."""
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
    for i, (k, (c, s)) in enumerate(zip(keys[1:], views[1:])):
        to = _state(c, s, W, H, mw, mh)
        f0 = legs[-1]["f1"] if legs else 0
        if k.move in ("whip", "settle"):
            fw = max(f0, k.at_frame - max(1, round((k.move_s or theme["whip_s"]) * fps)))
            if fw > f0:
                legs += drifts(i, f0, fw)
                prev = legs[-1]["to"]
            blur = theme["whip_blur_px"] if k.move == "whip" else 0
            legs.append({"f0": fw, "f1": k.at_frame, "to": to, "ease": "whip",
                         **({"blur": whip_blur(prev, to, W, H, blur)} if blur > 0 else {})})
        else:
            legs.append({"f0": f0, "f1": k.at_frame, "to": to, "ease": "glide"})
        motion.append(round(motion_px(prev, to, W, H), 2))
        prev = to
    last = keys[-1].at_frame
    if last < n:
        legs += drifts(len(keys) - 1, last, n)
    if any(k.tilt for k in keys):   # tilt `t` (deg) of every transform: a leg ending at a key frame moves to its tilt
        tilt = lambda f: next(k.tilt for k in reversed(keys) if k.at_frame <= f)
        start = {**start, "t": keys[0].tilt}
        legs = [{**L, "to": {**L["to"], "t": tilt(L["f1"])}} for L in legs]
    return {"start": start, "end": legs[-1]["to"], "legs": legs, "leg_motion": motion}


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
    math). Shape B: glide 0.35 t + 0.65 (1 - cos pi t) / 2, whip power3.inOut, a pulse leg's cubic-bezier `bez`.
    drift: constant ln-rate zoom (rho^t - 1) / (rho - 1), rho = to.s / from.s (linear when rho = 1). A leg with `lr`
    (pulse legs: the base drift's ln change) moves its ln zoom at a
    constant rate r plus the B share of the rest, (exp(r t + (ln rho - r) B(t)) - 1) / (rho - 1), with
    r = sign(ln rho) min(|lr|, |ln rho|): the zoom keeps moving at both ends and never reverses inside the leg."""
    from .layout import EASES, cubic_bezier
    rho = L["to"]["s"] / s_from
    if "bez" in L:
        b = L["bez"]
        shape = lambda t: cubic_bezier(*b, t)
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
    s, w, n, e = sc.map_bounds
    return (p.lng - w) / (e - w) * mw, (n - p.lat) / (n - s) * mh


def pin_frame(p, i: int, n: int, fps: int) -> int:
    """Appear frame of pin i (relative to the segment start): at_frame, else 0.4 s + 0.5 s per pin."""
    return p.at_frame if p.at_frame is not None else min(n - 1, round(fps * (0.4 + 0.5 * i)))


def card_frame(c, n: int, fps: int) -> int:
    """Appear frame of a card (relative to the segment start): at_frame, else 0.2 s."""
    return c.at_frame if c.at_frame is not None else min(n - 1, round(fps * 0.2))


def scene_data(seg: Segment, tl: Timeline, project_dir: Path) -> dict:
    from . import layout
    lay = layout.apply_segment(seg, tl, project_dir)   # beats scenes: derived camera_path and frames first
    S = tl.settings
    sc = seg.gsap_scene_config
    n = seg.n_frames
    prof = profiles.of(tl)
    theme = prof.render_values("hf")
    G = None
    if sc.globe:   # the 2D camera stays at identity over a frame-sized #cam; hf/globe.js draws the globe and moves pins
        G = globe.scene_block(seg, tl, project_dir, theme["camera_drift_per_s"])
        mw, mh = float(S.width), float(S.height)
        still = {"x": 0.0, "y": 0.0, "s": 1.0}
        cam = {"start": still, "end": still, "legs": [{"f0": 0, "f1": n, "to": still, "ease": "glide"}], "leg_motion": []}
        km = globe.key_motion(G, S.width, S.height)
        motion = min(km) if km else 0.0
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
        else:
            px, py = _pin_xy(p, sc, mw, mh)
        pins.append({"label": p.label, "px": round(px, 2), "py": round(py, 2),
                     "at_frame": f, **({"label_side": p.label_side} if p.label_side else {}),
                     **({"lat": p.lat, "lng": p.lng} if G else {})})
    cards = []
    for i, c in enumerate(sc.cards):
        img = None
        if c.asset_ref:
            cr = tl.credit_for(ref=c.asset_ref)
            if cr is None or not cr.path:
                raise EngineError(f"card '{c.title}': asset_ref '{c.asset_ref}' has no credits_manifest entry with a path")
            img = cr.path
        cards.append({"title": c.title, "subtitle": c.subtitle, "img": img, "top": round(CARD_TOP[c.position] * S.height),
                      "at_frame": card_frame(c, n, S.fps)})
    ecol = entities.colours(tl)
    if theme["label_style"] == "box":
        for c, cd in zip(sc.cards, cards):
            cd["box"] = entities.box_colour(ecol[c.entity], theme) if c.entity else theme["label_box_colour"]
    out = {"fps": S.fps, "n_frames": n, "width": S.width, "height": S.height, "map": sc.map_asset,
           "map_size": [round(mw, 2), round(mh, 2)], "camera": {"start": cam["start"], "end": cam["end"], "legs": cam["legs"],
                                                               **({"tilt": {"p": camera3d.perspective_px(S.height)}}
                                                                  if "t" in cam["start"] else {})},
           "motion_px": round(motion, 2), "pins": pins, "cards": cards, "theme": theme}
    if sc.regions:
        out["regions"] = [region_data(r, ecol, theme, mw, mh, G, S, lay is not None) for r in sc.regions]
    if sc.borders:
        out["borders"] = [{"points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in b.points], "at_frame": b.at_frame,
                           "draw_s": theme["border_draw_s"] if b.draw_s is None else b.draw_s, "colour": b.colour.lower(),
                           "dashed": b.dashed, "closed": b.closed} for b in sc.borders]
    if sc.title or sc.figures:
        col = entities.colours(tl)
    if sc.title:
        out["title"] = {"text": sc.title, "entity": sc.title_entity,
                        "colour": tint(col.get(sc.title_entity, "#ffffff"), theme["title_tint"]),
                        "at_frame": sc.title_at_frame or 0, "top": round(TITLE_TOP * S.height)}
    if sc.figures:
        out["figures"] = [{"kind": f.kind, "text": figure_text(f, f.value, theme), "value": f.value, "from": f.count_from,
                           "decimals": f.decimals, "prefix": f.prefix, "suffix": f.suffix, "motion": f.motion,
                           "count_s": f.count_s or theme["counter_s"], "at_frame": f.at_frame,
                           "x": round(f.position[0] * S.width, 2), "y": round(f.position[1] * S.height, 2),
                           "entity": f.entity, "colour": col.get(f.entity, "#ffffff")} for f in sc.figures]
    if theme["label_style"] == "box" and lay is None:
        for t in [out.get("title"), *out.get("figures", [])]:
            if t:
                t["box"] = entities.box_colour(col[t["entity"]], theme) if t["entity"] else theme["label_box_colour"]
    if prof.legibility:
        out["legibility"] = {**prof.legibility.model_dump(), "v": legibility.code_hash()}
        for t in [out.get("title"), *out.get("figures", [])]:
            if t:
                t["entity_colour"] = col.get(t["entity"]) if t.get("entity") else None
        for r in out.get("regions", []):
            r["casing"] = legibility.line_casing(r["edge_colour"] if r["style"] == "block" else r["colour"], out["legibility"])
        for b in out.get("borders", []):
            b["casing"] = legibility.line_casing(b["colour"], out["legibility"])
    if prof.print:
        surf = printstyle.surface(project_dir / sc.map_asset, prof.print)
        out["print"] = printstyle.scene_block(prof.print, surf)
        for r in out.get("regions", []):
            r["edge_ink"] = printstyle.edge_ink(r["colour"], prof.print, surf)
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
        out["deep"] = deep_entries(project_dir / sc.map_asset, dp)
    if sc.morph:
        out["morph"] = morph_data(sc.morph, out, project_dir, (iw, ih))
    return out


def _centroid(poly: list[list[float]]) -> tuple[float, float]:
    """Area centroid of a polygon (vertex mean when its area is 0)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        k = x0 * y1 - x1 * y0
        a, cx, cy = a + k, cx + (x0 + x1) * k, cy + (y0 + y1) * k
    if abs(a) < 1e-12:
        return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    return cx / (3 * a), cy / (3 * a)


def region_data(r, col: dict, theme: dict, mw: float, mh: float, G: dict | None, S, beats: bool) -> dict:
    """Scene entry of a region: points in map px (globe scenes: `ll` lat/lng per point and the points projected at
    the region's first frame), style colours of every owner (`changes`) and its box label. The theme region_style
    applies outside beats scenes; their regions stay "tint" unless the region sets a style."""
    style = r.style or ("tint" if beats else theme["region_style"])

    def paint(e: str) -> dict:
        c = col[e]
        out = {"entity": e, "colour": c}
        if style == "block":
            b = entities.block_colour(c, theme)
            L, C, h = legibility.rgb_oklch(legibility.hex_rgb(b))
            out.update(block=b, side=legibility.oklch_hex(max(0.0, L - theme["block_side_dl"]), C, h),
                       bevel=tint(b, theme["block_bevel_tint"]))
        if r.label:
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
    return out


def morph_data(m, scene: dict, project_dir: Path, base_size: tuple[int, int]) -> dict:
    """Scene block of a map morph: its whole-map derivative and deep tiles (the base map's camera), times and tones.
    The morph map must have the base map's aspect ratio (same extent and projection)."""
    src = project_dir / m.asset
    if not src.is_file():
        raise EngineError(f"morph asset {m.asset} not found")
    w, h = map_image_size(src)
    if abs(w / h - base_size[0] / base_size[1]) > 0.005 * base_size[0] / base_size[1]:
        raise EngineError(f"morph asset {m.asset} is {w}x{h}; it must have the aspect ratio of map_asset "
                          f"({base_size[0]}x{base_size[1]}): same extent and projection")
    dp = deep_plan(scene, (w, h))
    return {"img": f"assets/morph_{map_key(src, _need_w(scene))[:32]}.jpg", "asset": m.asset,
            "deep": deep_entries(src, dp) if dp else [], "at_frame": m.at_frame, "s": m.s,
            "back_at_frame": m.back_at_frame, "sepia": m.sepia, "paper": m.paper, "grain": m.grain,
            "paper_texture": MORPH_PAPER, "grain_texture": MORPH_GRAIN}


def deep_plan(scene: dict, src_size: tuple[int, int]) -> dict | None:
    """Deep tiles of a map scene: when the camera zooms past the whole-map derivative's resolution (MAP_MAX_SIDE), a
    deep raster of width min(source width, map width x largest zoom) is cut into DEEP_TILE tiles and only the tiles
    some such frame shows (its viewport grown by DEEP_MARGIN) are kept. None when the derivative suffices."""
    from . import layout
    mw, mh = scene["map_size"]
    need = _need_w(scene)
    base = _map_width(need) / mw                          # derivative px per map px
    dw = min(src_size[0], math.ceil(need))
    if dw <= _map_width(need):
        return None
    dh = round(src_size[1] * dw / src_size[0])
    d = dw / mw                                           # deep px per map px
    W, H, cam = scene["width"], scene["height"], scene["camera"]
    nx, ny = math.ceil(dw / DEEP_TILE), math.ceil(dh / DEEP_TILE)
    tiles = set()
    for f in range(scene["n_frames"]):
        c = layout.camera_at(cam, f)
        mag = camera3d.max_mag(c["t"], W, H) if c.get("t") else 1.0
        if c["s"] * mag <= base + 1e-9:
            continue
        x0, y0, x1, y1 = -c["x"] / c["s"], -c["y"] / c["s"], (W - c["x"]) / c["s"], (H - c["y"]) / c["s"]
        if c.get("t"):   # the flat picture the tilted screen shows: its corners unprojected (past the horizon: map top)
            q = [camera3d.unproject(sx, sy, c["t"], W, H) for sx, sy in ((0, 0), (W, 0), (0, H), (W, H))]
            qs = [p for p in q if p is not None]
            x0 = min(p[0] for p in qs) - c["x"]
            x1 = max(p[0] for p in qs) - c["x"]
            y0 = (min(p[1] for p in qs) - c["y"]) if len(qs) == 4 else -c["y"] - mh * c["s"]
            y1 = max(p[1] for p in qs) - c["y"]
            x0, y0, x1, y1 = x0 / c["s"], y0 / c["s"], x1 / c["s"], y1 / c["s"]
        gx, gy = (x1 - x0) * DEEP_MARGIN, (y1 - y0) * DEEP_MARGIN
        i0, i1 = max(0, math.floor((x0 - gx) * d / DEEP_TILE)), min(nx - 1, math.floor((x1 + gx) * d / DEEP_TILE))
        j0, j1 = max(0, math.floor((y0 - gy) * d / DEEP_TILE)), min(ny - 1, math.floor((y1 + gy) * d / DEEP_TILE))
        tiles |= {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}
    if not tiles:
        return None
    return {"w": dw, "h": dh, "tiles": sorted(tiles)}


def _deep_box(dp: dict, i: int, j: int) -> tuple[int, int, int, int]:
    """Deep raster box (x0, y0, x1, y1) of tile (i, j), DEEP_PAD into its neighbours, clipped to the raster."""
    return (i * DEEP_TILE, j * DEEP_TILE, min(dp["w"], (i + 1) * DEEP_TILE + DEEP_PAD),
            min(dp["h"], (j + 1) * DEEP_TILE + DEEP_PAD))


def deep_key(src_hash: str, dp: dict, i: int, j: int) -> str:
    import PIL
    return blake3_json({"v": DEEP_VERSION, "src": src_hash, "w": dp["w"], "h": dp["h"], "box": _deep_box(dp, i, j),
                        "resample": "lanczos", "q": MAP_JPEG_QUALITY, "pil": PIL.__version__})


def deep_entries(src: Path, dp: dict) -> list[dict]:
    """Scene block of the deep tiles: image name (its content key), raster box (x0, y0, x1, y1) and raster size; the
    template places a tile at box x map size / raster size in map px."""
    h = blake3_files([src])[0]
    return [{"img": f"assets/deep_{deep_key(h, dp, i, j)[:32]}.jpg", "box": list(_deep_box(dp, i, j)),
             "raster": [dp["w"], dp["h"]]} for i, j in dp["tiles"]]


def deep_tiles(src: Path, scene: dict) -> dict[str, Path]:
    """Build-dir name -> cached file of every deep tile of a scene; renders the missing ones from one decode."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    out, todo = {}, []
    for e in scene.get("deep", []):
        name = Path(e["img"]).name
        if hit := cache.get_file("maps", Path(name).stem, ".jpg"):
            out[name] = hit
        else:
            todo.append(e)
    if not todo:
        return out
    with Image.open(src) as im:
        dw, dh = todo[0]["raster"]
        if dw * 2 <= im.width:
            im.draft("RGB", (dw, dh))                      # JPEG: decode at a reduced scale when that is enough
        sx, sy = im.width / dw, im.height / dh
        im = im.convert("RGB")
        for e in todo:
            x0, y0, x1, y1 = e["box"]
            t = im.resize((x1 - x0, y1 - y0), Image.LANCZOS, box=(x0 * sx, y0 * sy, x1 * sx, y1 * sy))
            name = Path(e["img"]).name
            tmp = cache.acquire(cache.tmp_name(config.CACHE_DIR / "maps" / f"{Path(name).stem}.part.jpg").with_suffix(".jpg"))
            tmp.parent.mkdir(parents=True, exist_ok=True)
            t.save(tmp, format="JPEG", quality=MAP_JPEG_QUALITY)
            out[name] = cache.put_file("maps", Path(name).stem, tmp, ".jpg")
            tmp.unlink(missing_ok=True)
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


def counter_value(f, frame: int, fps: int, count_s: float) -> float:
    """Value a counter figure shows at `frame` (relative to the segment start): GSAP power2.out (cubic ease-out) from
    count_from to value."""
    p = min(1.0, max(0.0, (frame - f.at_frame) / fps / count_s))
    return f.count_from + (f.value - f.count_from) * (1 - (1 - p) ** 3)


def figure_end_frame(f, fps: int, default_count_s: float) -> int:
    """Frame (relative to the segment start) at which a figure's entrance (bounce 0.9 s or counter) ends."""
    return f.at_frame + math.ceil((0.9 if f.motion == "bounce" else (f.count_s or default_count_s)) * fps)


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
            cs = f.count_s or th["counter_s"]
            prev = figure_text(f, f.count_from, th)
            for fr in range(f.at_frame + 1, figure_end_frame(f, fps, th["counter_s"]) + 1):
                txt = figure_text(f, counter_value(f, fr, fps, cs), th)
                if txt != prev:
                    out.append({"kind": "counter_tick", "frame": s.start_frame + fr, "seg": s.seg_index, "figure": i})
                prev = txt
    return out


def route_end_frame(r, fps: int, default_draw_s: float) -> int:
    """Frame (relative to the segment start) at which a route's arrowhead reaches the last point."""
    return r.at_frame + math.ceil((r.draw_s or default_draw_s) * fps)


def _map_width(need_w: float) -> int:
    return int(min(MAP_MAX_SIDE, math.ceil(need_w)))


def map_key(src: Path, need_w: float) -> str:
    """Key of the map derivative: its full recipe (version, width after the MAP_MAX_SIDE cap, LANCZOS resize and
    JPEG quality, Pillow version) and the source content hash."""
    import PIL
    return blake3_json({"v": MAP_VERSION, "algo": core.ALGO_VERSION, "src": blake3_files([src])[0],
                        "w": _map_width(need_w), "max": MAP_MAX_SIDE, "resample": "lanczos", "q": MAP_JPEG_QUALITY,
                        "pil": PIL.__version__})


def _need_w(scene: dict) -> float:
    """Widest on-screen map width: map width x the largest camera scale (x the tilt magnification at the bottom edge
    of the most tilted transform, camera3d.max_mag)."""
    c = scene["camera"]
    st = [c["start"], c["end"]] + [L["to"] for L in c["legs"]]
    W, H = scene["width"], scene["height"]
    return scene["map_size"][0] * max(x["s"] * (camera3d.max_mag(x["t"], W, H) if x.get("t") else 1.0) for x in st)


def _map_copy(src: Path, need_w: float) -> Path:
    """Downscaled JPEG of the map (content cache), never larger than needed for the max zoom."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    tw = _map_width(need_w)
    key = map_key(src, need_w)
    if hit := cache.get_file("maps", key, ".jpg"):
        return hit
    tmp = cache.acquire(cache.tmp_name(config.CACHE_DIR / "maps" / f"{key}.part.jpg").with_suffix(".jpg"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > tw:
            im = im.resize((tw, round(im.height * tw / im.width)), Image.LANCZOS)
        im.save(tmp, quality=MAP_JPEG_QUALITY)
    out = cache.put_file("maps", key, tmp, ".jpg")
    tmp.unlink(missing_ok=True)
    return out


def textures(scene: dict) -> list[str]:
    """vendor/textures files of a scene: the paper texture and grain of a [print] paper surface and of a map morph."""
    pr, m, out = scene.get("print"), scene.get("morph"), []
    if pr and pr["surface"] == "paper":
        out += [f for f, a in ((pr["paper_texture"], pr["paper_alpha"]), (pr["grain"], pr["grain_alpha"])) if a > 0]
    if m:
        out += [f for f, a in ((m["paper_texture"], m["paper"]), (m["grain_texture"], m["grain"])) if a > 0 and f not in out]
    return out


PROPS_JS = config.PKG / "hf" / "props.js"   # props layer script; in the build dir and the key of prop segments only


def script_files(scene: dict) -> list[Path]:
    """Scripts a scene adds to the template: hf/props.js (props), hf/parallax.js (layers), hf/globe.js (globe, a module)."""
    return ([PROPS_JS] if scene.get("props") else []) + ([parallax.JS] if scene.get("layers") else []) + \
        ([globe.JS] if scene.get("globe") else [])


def _card_name(src: Path) -> str:
    """Build-dir name of a card image: its content hash, so two images with one basename never share a file."""
    return f"card_{blake3_files([src])[0][:32]}{src.suffix.lower()}"


def keys(seg: Segment, tl: Timeline, project_dir: Path) -> tuple[str, str]:
    """(segment cache key, map cache key) without writing files; equal to the keys `render` uses."""
    scene = scene_data(seg, tl, project_dir)
    mk = globe.tex_key(project_dir / scene["map"]) if "globe" in scene else map_key(project_dir / scene["map"], _need_w(scene))
    for c in scene["cards"]:
        if c["img"]:
            c["img"] = "assets/" + _card_name(project_dir / c["img"])
    return _key(scene, [config.TEXTURES_DIR / f for f in textures(scene)] + script_files(scene), mk), mk


def build_dir(seg: Segment, tl: Timeline, project_dir: Path, out_dir: Path) -> tuple[Path, dict]:
    """Write the per-segment HyperFrames project. Returns (dir, scene)."""
    S = tl.settings
    scene = scene_data(seg, tl, project_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    if "globe" in scene:
        for name, f in globe.files(seg, project_dir, scene["globe"]).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        (out_dir / "assets" / "three").mkdir()
        for f in globe.THREE_FILES:
            shutil.copyfile(f, out_dir / "assets" / "three" / f.name)
    else:
        mp = _map_copy(project_dir / scene["map"], _need_w(scene))
        shutil.copyfile(mp, out_dir / "assets" / "map.jpg")
        for name, f in deep_tiles(project_dir / scene["map"], scene).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        if m := scene.get("morph"):
            src = project_dir / m["asset"]
            shutil.copyfile(_map_copy(src, _need_w(scene)), out_dir / m["img"])
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
        mod = ' type="module"' if f == globe.JS else ""
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
    _INKS[key] = inks
    return _INKS[key]


def cache_key(seg: Segment, tl: Timeline, project_dir: Path, scene: dict | None = None) -> str:
    """Segment cache key (`scene` is accepted for call-site symmetry; the key is derived from the timeline)."""
    return keys(seg, tl, project_dir)[0]


def _key(scene: dict, files: list[Path], mk: str) -> str:
    """Segment key: engine render version, capture/encode settings, the scene (every profile value that reaches the
    template, card and prop images by content-hash name), the map derivative key, content hashes of the textures,
    the scripts the scene adds (script_files) and the fonts copied into the build dir, and the toolchain identity
    (toolchain.hf_identity), and the engine source that writes the build dir (CODE)."""
    return blake3_json({"v": HF_VERSION, "code": code_hash(CODE), "enc": [HF_QUALITY, HF_CRF], "x264": config.X264, "algo": core.ALGO_VERSION,
                        "scene": scene, "map": mk, "files": blake3_files(files),
                        "fonts": blake3_files([config.FONTS_DIR / f for f in font_files(scene)]),
                        "toolchain": toolchain.hf_identity()})


def render(seg: Segment, tl: Timeline, project_dir: Path, workdir: Path, workers: int = 3,
           use_cache: bool = True) -> tuple[Path, bool]:
    key = keys(seg, tl, project_dir)[0]
    if use_cache and (hit := cache.get_file("segments", key)):
        return hit, True
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
    run([config.NODE, config.HF_CLI, "render", d, "--fps", str(tl.settings.fps), "--sdr", "-q", HF_QUALITY, "--crf", str(HF_CRF),
         "--workers", str(workers), "-o", raw], env=config.hf_env(), timeout=900)
    n = seg.n_frames
    if _is_engine_format(raw, tl, n):
        raw.replace(out)
    else:
        # normalise to the engine's stream format with the exact frame count (pad by cloning / trim)
        run([config.FFMPEG, "-v", "error", "-y", "-i", raw, "-an", "-vf",
             f"fps={tl.settings.fps},tpad=stop_mode=clone:stop=-1,setsar=1", *config.X264, "-frames:v", str(n), out])
        raw.unlink(missing_ok=True)
    streams.verify(out, n, tl.settings.fps, f"segment {seg.seg_index} (HyperFrames)")
    return (cache.put_file("segments", key, out) if use_cache else out), False


def _is_engine_format(p: Path, tl: Timeline, n: int) -> bool:
    """HyperFrames output usable as the segment file: H.264 yuv420p at the timeline size and frame rate with exactly
    n frames from 0 lasting n / fps (streams.verify). The final mux decodes it on its own when its codec parameters
    differ from the other segments (audio.video_inputs)."""
    S = tl.settings
    v = streams.params(p, "v:0")[0]
    if (v["codec_name"], v["pix_fmt"], v["width"], v["height"], v["r_frame_rate"], v["avg_frame_rate"]) != \
            ("h264", "yuv420p", S.width, S.height, f"{S.fps}/1", f"{S.fps}/1"):
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
    scene.js, assets, scripts; a globe script also switches the probe browser to WebGL), the probe times as passed to
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
              bg: str = "", ref: str | None = None) -> dict:
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
    if (seg_dir / globe.JS.name).exists():
        env["ENGINE_WEBGL"] = "1"     # globe scenes: SwiftShader WebGL in the probe browser
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)
    with serve(seg_dir) as url:
        out = run([config.NODE, PROBE_JS, url + "/index.html", snap_dir or "-",
                   ",".join(f"{t:.4f}" for t in times), width, height, *([bg] if bg else [])], env=env,
                  timeout=120 + (2 * len(times) if bg else 0))
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
