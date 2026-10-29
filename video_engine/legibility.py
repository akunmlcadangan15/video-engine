"""Adaptive map text (profile [legibility]): colour math, background measurement, style choice and the R64 check.

Elements: teardrop pin labels ("pin:i"), the big title ("title"), years and counters ("figure:i"). Each element gets
one polarity and one style for its whole visible interval (`choose`); a time schedule with hold, crossfade and EMA is
the fallback when no single style passes every sample. Lc = APCA-W3 0.1.9; Y = APCA screen luminance."""
from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .util import blake3_files


@functools.cache
def code_hash() -> str:
    """Hash of this module's source: part of the HyperFrames segment cache key (scene["legibility"]["v"]), because
    the painted inks are computed here and are not in the keyed scene."""
    return blake3_files([Path(__file__)])[0]


# ---------------------------------------------------------------- colour math


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[k:k + 2], 16) for k in (1, 3, 5)], dtype=float)


def rgb_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(round(min(255.0, max(0.0, float(v))))) for v in rgb)


def apca_y(rgb) -> np.ndarray:
    """APCA-W3 0.1.9 screen luminance of sRGB 0-255 (last axis RGB): simple 2.4 power, sRGB coefficients."""
    c = (np.asarray(rgb, dtype=float) / 255) ** 2.4
    return c @ np.array([0.2126729, 0.7151522, 0.0721750])


def apca_lc(y_text, y_bg) -> np.ndarray:
    """APCA-W3 0.1.9 Lc (signed: + dark text on light background, - light text on dark background)."""
    yt, yb = np.asarray(y_text, dtype=float), np.asarray(y_bg, dtype=float)
    clamp = lambda y: np.where(y > 0.022, y, y + (0.022 - np.clip(y, 0, 0.022)) ** 1.414)
    yt, yb = clamp(yt), clamp(yb)
    bow = (yb ** 0.56 - yt ** 0.57) * 1.14
    wob = (yb ** 0.65 - yt ** 0.62) * 1.14
    out = np.where(yb > yt, np.where(bow < 0.1, 0.0, bow - 0.027), np.where(wob > -0.1, 0.0, wob + 0.027))
    return np.where(np.abs(yb - yt) < 0.0005, 0.0, out) * 100


def wcag_l(rgb) -> np.ndarray:
    c = np.asarray(rgb, dtype=float) / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def wcag_ratio(l1, l2) -> np.ndarray:
    return (np.maximum(l1, l2) + 0.05) / (np.minimum(l1, l2) + 0.05)


def _lin(rgb):
    c = np.asarray(rgb, dtype=float) / 255
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _gam(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055) * 255


def rgb_oklch(rgb) -> tuple[float, float, float]:
    r, g, b = _lin(rgb)
    l_ = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return float(L), float(math.hypot(a, bb)), float(math.degrees(math.atan2(bb, a)) % 360)


def _oklch_lin(L: float, C: float, h: float) -> np.ndarray:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_, m_, s_ = L + 0.3963377774 * a + 0.2158037573 * b, L - 0.1055613458 * a - 0.0638541728 * b, L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return np.array([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                     -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                     -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s])


def oklch_hex(L: float, C: float, h: float) -> str:
    """sRGB hex of an OKLCH colour; out-of-gamut colours lose chroma (same L and hue) until they fit."""
    for k in range(41):
        c = C * (1 - k / 40)
        lin = _oklch_lin(L, c, h)
        if lin.min() >= -1e-4 and lin.max() <= 1 + 1e-4:
            return rgb_hex(_gam(lin))
    return rgb_hex(_gam(_oklch_lin(L, 0, h)))


def mix(a: str, b: str, k: float) -> str:
    return rgb_hex(hex_rgb(a) * (1 - k) + hex_rgb(b) * k)


# ---------------------------------------------------------------- elements and sample times


LAYOUT_SETTLE_S = 0.1   # after a beats element's entry tween


def elements(scene: dict) -> list[dict]:
    """Adaptive text elements of a scene with their visible (settled) interval in seconds and their font size."""
    fps, th = scene["fps"], scene["theme"]
    end = (scene["n_frames"] - 1) / fps
    out = []
    pr = scene.get("print")        # profile [print]: fixed inks per text kind (el "ink" dark, "light" light)
    if th["pin_style"] == "teardrop":
        out += [{"id": f"pin:{i}", "kind": "label", "start": p["at_frame"] / fps + 0.7, "end": end, "entity": None,
                 "px": th["pin_label_px"], **({"ink": pr["ink"]} if pr else {})} for i, p in enumerate(scene["pins"])]
    if t := scene.get("title"):
        out.append({"id": "title", "kind": "title", "start": t["at_frame"] / fps + 0.5, "end": end,
                    "entity": t.get("entity"), "colour": t.get("entity_colour"), "px": th["title_px"]})
    for i, f in enumerate(scene.get("figures") or []):
        settle = 0.9 if f["motion"] == "bounce" else 0.2
        out.append({"id": f"figure:{i}", "kind": "figure", "start": f["at_frame"] / fps + settle, "end": end,
                    "entity": f.get("entity"), "colour": f.get("entity_colour"), "px": th["figure_px"]})
        if pr:
            P = (scene.get("layout") or {}).get("figures", {}).get(str(i), {})
            time = P.get("slot") == "centre" and "tracking" in P
            out[-1].update({"colour": None, "px": P.get("px", th["figure_px"]),
                            "ink": mix(pr["ink"], scene["legibility"]["paper_colour"], 1 - pr["time_alpha"]) if time
                            else pr["ink"], "light": pr["year_dark_colour"]})
    if lay := scene.get("layout"):                  # beats scenes: area labels, in their region's entity colour
        regs = scene.get("regions") or []
        for a in lay.get("areas") or []:
            r = regs[a["region"]] if 0 <= a.get("region", -1) < len(regs) else {}
            out.append({"id": f"area:{a['key']}", "kind": "title" if a.get("title") else "area", "start": 0.0,
                        "end": end, "entity": r.get("entity"),
                        "colour": None if a.get("water") else r.get("colour"), "water": bool(a.get("water")),
                        "px": a["px"]})
        for h in lay.get("headings") or []:
            out.append({"id": f"heading:{h['key']}", "kind": "heading", "start": 0.0, "end": end, "entity": None,
                        "px": h["px"], "ink": pr["heading_ink"], "light": pr["heading_light"]})
        for n in lay.get("notes") or []:
            out.append({"id": f"note:{n['key']}", "kind": "note", "start": 0.0, "end": end, "entity": None,
                        "px": n["px"], "ink": pr["note_ink"], "light": pr["note_light"]})
        key = {"pin": "label", "title": "title", "figure": "fig", "area": "area", "heading": "heading",
               "note": "note"}   # the longest settled window
        for e in out:
            k, _, i = e["id"].partition(":")
            wins = [(t_in + d_in + LAYOUT_SETTLE_S, (end if t_out is None else t_out) - 1 / fps)
                    for t_in, d_in, t_out, _ in lay["els"].get(f"{key[k]}:{i or 0}", [])]
            e["start"], e["end"] = max(wins, key=lambda w: w[1] - w[0], default=(end + 1, end))
    if pr:                         # printed text: the surface fixes the polarity
        for e in out:
            e["polarity"] = "dark" if pr["surface"] == "paper" else "light"
    return [e for e in out if e["start"] <= e["end"]]


def sample_times(el: dict, n: int, fps: int) -> list[float]:
    """n frame times spread evenly over the element's interval (both ends included)."""
    f0, f1 = math.ceil(el["start"] * fps - 1e-6), math.floor(el["end"] * fps + 1e-6)
    return sorted({(f0 + round((f1 - f0) * k / (n - 1))) / fps for k in range(n)})


def all_times(scene: dict, lg: dict) -> list[float]:
    return sorted({t for e in elements(scene) for t in sample_times(e, lg["samples"], scene["fps"])})


def halo_px(el: dict, lg: dict) -> float:
    return el["px"] * (lg["label_halo_width_em"] if el["kind"] == "label" else lg["halo_width_em"])


def target(kind: str, lg: dict) -> float:
    return {"title": lg["lc_title"], "figure": lg["lc_figure"], "label": lg["lc_label"], "area": lg["lc_area"],
            "heading": lg["lc_heading"], "note": lg["lc_note"]}[kind]


def wcag_target(kind: str, lg: dict) -> float:
    return lg["wcag_title"] if kind in ("title", "figure", "area", "heading") else lg["wcag_label"]


# ---------------------------------------------------------------- measurement


@dataclass
class Sample:
    """Background under one element at one time: rgb (N x 3) of the glyph mask dilated by the halo width, its pixel
    centres xy (N x 2, output px), the box (x, y, w, h), and the adjacent ring rgb and centres (glyph mask dilated
    by adjacent_px at full resolution, minus the mask). cov / ring_cov: halo coverage (0-1) at those pixels
    (`halo_coverage`; None = 1)."""
    t: float
    rgb: np.ndarray
    xy: np.ndarray
    box: tuple
    ring: np.ndarray
    ring_xy: np.ndarray
    cov: np.ndarray | None = None
    ring_cov: np.ndarray | None = None


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    from PIL import Image, ImageFilter
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * r + 1))
    return np.asarray(im) > 127


def disk_dilate(mask: np.ndarray, r: float) -> np.ndarray:
    """Dilation by a disk of radius r px (the Minkowski sum a stroke of width 2 r paints around the outline)."""
    R = int(math.ceil(r))
    if R <= 0:
        return mask.copy()
    h, w = mask.shape
    P = np.pad(mask, R)
    out = np.zeros_like(mask)
    for dy in range(-R, R + 1):
        for dx in range(-R, R + 1):
            if dx * dx + dy * dy <= r * r + 1e-9:
                out |= P[R + dy:R + dy + h, R + dx:R + dx + w]
    return out


def holes(glyph: np.ndarray) -> np.ndarray:
    """Enclosed counters: background pixels (4-connected) not connected to the border of the crop."""
    from PIL import Image, ImageDraw
    im = Image.fromarray(np.pad(~glyph, 1, constant_values=True).astype(np.uint8) * 255).copy()   # own buffer
    ImageDraw.floodfill(im, (0, 0), 128)
    return (np.asarray(im) == 255)[1:-1, 1:-1]


def halo_coverage(glyph: np.ndarray, hw: float, sd: float) -> np.ndarray:
    """Painted halo alpha (0-1, before halo_alpha) around a full-resolution glyph mask, as the template paints it:
    the glyphs stroked with width 2 hw (disk dilation) plus the fill, Gaussian blur sd (halo_blur_ratio x hw), and
    0 in every enclosed counter."""
    from PIL import Image, ImageFilter
    a = disk_dilate(glyph, hw) | glyph
    cov = a.astype(float)
    if sd > 0:
        cov = np.asarray(Image.fromarray(a.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(sd)), float) / 255
    cov[holes(glyph)] = 0.0
    return cov


def _down(a: np.ndarray, f: int) -> np.ndarray:
    if f == 1:
        return a.astype(float)
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w].astype(float)
    return a.reshape(h // f, f, w // f, f, *a.shape[2:]).mean(axis=(1, 3))


def measure(bg: np.ndarray, mask_img: np.ndarray, rect: dict, el: dict, lg: dict, t: float,
            origin: tuple = (0, 0), halo: tuple | None = None) -> Sample | None:
    """bg: text-hidden frame (H x W x 3); mask_img: glyph-mask frame (black text on white, H x W x 3); both cover the
    output region starting at `origin` (x, y); rect and the returned centres are in output px. halo: painted
    (width, blur) px (R64); default the configured width and halo_blur_ratio."""
    hw = halo[0] if halo else halo_px(el, lg)
    sd = halo[1] if halo else lg["halo_blur_ratio"] * hw
    pad = int(math.ceil(hw + lg["adjacent_px"])) + 4
    H, W = bg.shape[:2]
    ox, oy = origin
    rx, ry = rect["x"] - ox, rect["y"] - oy
    x0, y0 = max(0, int(rx) - pad), max(0, int(ry) - pad)
    x1, y1 = min(W, int(math.ceil(rx + rect["w"])) + pad), min(H, int(math.ceil(ry + rect["h"])) + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    f = 1 if el["kind"] == "title" else max(1, round(1 / lg["measure_scale"]))
    full = mask_img[y0:y1, x0:x1].mean(axis=2) < 128
    own = np.zeros_like(full)              # only this element's glyphs: its box + 0.1 em (other text is excluded)
    m = 0.1 * el["px"]
    own[max(0, int(ry - m) - y0):max(0, int(math.ceil(ry + rect["h"] + m)) - y0),
        max(0, int(rx - m) - x0):max(0, int(math.ceil(rx + rect["w"] + m)) - x0)] = True
    full &= own
    glyph = _down(full, f) > (0.25 if f > 1 else 0.5)
    if not glyph.any():
        return None
    col = _down(bg[y0:y1, x0:x1], f)
    region = _dilate(glyph, int(math.ceil(hw / f)))
    ring = _dilate(full, int(math.ceil(lg["adjacent_px"]))) & ~full
    cov = halo_coverage(full, hw, sd)
    covd = _down(cov, f)

    def centres(m, k):
        ys, xs = np.nonzero(m)
        return np.stack([ox + x0 + (xs + 0.5) * k, oy + y0 + (ys + 0.5) * k], axis=1)
    return Sample(t, col[region], centres(region, f), (rect["x"], rect["y"], rect["w"], rect["h"]),
                  bg[y0:y1, x0:x1][ring].astype(float), centres(ring, 1), covd[region], cov[ring])


def _components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components of a boolean mask, as index arrays (flat)."""
    h, w = mask.shape
    seen = np.zeros(h * w, bool)
    flat = mask.ravel()
    out = []
    for p0 in np.flatnonzero(flat):
        if seen[p0]:
            continue
        seen[p0] = True
        st, comp = [p0], []
        while st:
            p = st.pop()
            comp.append(p)
            y, x = divmod(p, w)
            for q in (p - 1 if x else -1, p + 1 if x < w - 1 else -1, p - w if y else -1, p + w if y < h - 1 else -1):
                if q >= 0 and flat[q] and not seen[q]:
                    seen[q] = True
                    st.append(q)
        out.append(np.asarray(comp))
    return out


def counters(png: np.ndarray, bg: np.ndarray, mask_img: np.ndarray, rect: dict, el: dict, lg: dict,
             origin: tuple, st: dict, others: tuple = ()) -> tuple[int, float] | None:
    """Counter check of one element: the enclosed loops of its glyph mask (full resolution, own box + 0.1 em),
    eroded 1 px, of at least counter_min_px; the painted frame (png, full frame) at those pixels against the
    text-hidden frame (bg, clipped at origin) with the paper vignettes composited: the element's (st: data-ink-state)
    and those of `others` ((rect, state) of the other adaptive elements on screen, at their opacity). Returns
    (counter px, fraction whose max channel difference is <= counter_diff_max), or None without counters."""
    H, W = bg.shape[:2]
    ox, oy = origin
    rx, ry = rect["x"] - ox, rect["y"] - oy
    m = 0.1 * el["px"]
    x0, y0 = max(0, int(rx - m) - 1), max(0, int(ry - m) - 1)
    x1, y1 = min(W, int(math.ceil(rx + rect["w"] + m)) + 1), min(H, int(math.ceil(ry + rect["h"] + m)) + 1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    full = mask_img[y0:y1, x0:x1].mean(axis=2) < 128
    hl = holes(full)
    hl &= ~_dilate(~hl, 1)
    keep = np.zeros(hl.size, bool)
    for c in _components(hl):
        if len(c) >= lg["counter_min_px"]:
            keep[c] = True
    keep = keep.reshape(hl.shape)
    if not keep.any():
        return None
    ys, xs = np.nonzero(keep)
    want = bg[y0:y1, x0:x1][keep].astype(float)
    xy = np.stack([ox + x0 + xs + 0.5, oy + y0 + ys + 0.5], axis=1)
    for rc, s in ((rect, st), *others):
        va = vignette_alpha(xy, (rc["x"], rc["y"], rc["w"], rc["h"]), float(s.get("va") or 0) * rc.get("opacity", 1), lg)
        want = want * (1 - va[:, None]) + hex_rgb(s["halo"]) * va[:, None]
    got = png[oy + y0 + ys, ox + x0 + xs].astype(float)
    diff = np.abs(got - want).max(axis=1)
    return int(keep.sum()), float((diff <= lg["counter_diff_max"]).mean())


def line_lc(bg: np.ndarray, mask_img: np.ndarray, rect: dict, colours: list[str], lg: dict,
            origin: tuple) -> tuple[float, int] | None:
    """Edge contrast of one line element (pin head, route, region outline, leader): its painted pixels = line-mask
    pixels (lines on white) differing by more than line_mask_diff inside its rect; the ring = those pixels dilated by
    line_ring_px minus every line pixel, read from the lines-hidden frame. Lc = per ring pixel the best |Lc| of its
    painted colours (core or casing separates it), at the line_percentile. Returns (Lc, ring px) or None when not
    visible."""
    H, W = bg.shape[:2]
    ox, oy = origin
    R = int(math.ceil(lg["line_ring_px"]))
    x0, y0 = max(0, int(rect["x"] - ox) - 2 * R - 1), max(0, int(rect["y"] - oy) - 2 * R - 1)
    x1 = min(W, int(math.ceil(rect["x"] - ox + rect["w"])) + 2 * R + 1)
    y1 = min(H, int(math.ceil(rect["y"] - oy + rect["h"])) + 2 * R + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    allm = np.abs(mask_img[y0:y1, x0:x1].astype(float) - 255).max(axis=2) > lg["line_mask_diff"]
    own = np.zeros_like(allm)
    own[max(0, int(rect["y"] - oy) - y0 - R):int(math.ceil(rect["y"] - oy + rect["h"])) - y0 + R,
        max(0, int(rect["x"] - ox) - x0 - R):int(math.ceil(rect["x"] - ox + rect["w"])) - x0 + R] = True
    line = allm & own
    if line.sum() < 4:
        return None
    ring = disk_dilate(line, lg["line_ring_px"]) & ~allm
    if not ring.any():
        return None
    y = apca_y(bg[y0:y1, x0:x1][ring].astype(float))
    sep = np.max([np.abs(apca_lc(float(apca_y(hex_rgb(c))), y)) for c in colours], axis=0)
    return float(np.percentile(sep, lg["line_percentile"])), int(ring.sum())


# ---------------------------------------------------------------- styles and evaluation


@dataclass(frozen=True)
class Style:
    polarity: str            # "dark" | "light"
    ink: str
    halo: str
    halo_alpha: float        # dark only
    vignette_alpha: float    # dark only
    step: int                # escalation step 0-3


def halo_colour(p50_rgb, lg: dict) -> str:
    """Background p50 in OKLCH with L raised to >= halo_l_min (capped at halo_l_max), chroma x halo_c_scale, same hue."""
    L, C, h = rgb_oklch(p50_rgb)
    return oklch_hex(min(lg["halo_l_max"], max(L, lg["halo_l_min"])), C * lg["halo_c_scale"], h)


def line_casing(core: str, lg: dict) -> str:
    """Casing of a region outline: paper_colour or route_casing_colour, whichever has the larger |Lc| against the
    outline colour (the casing takes the opposite tone, so core or casing separates it from any map pixel)."""
    yc = float(apca_y(hex_rgb(core)))
    return max([lg["paper_colour"], lg["route_casing_colour"]],
               key=lambda c: abs(float(apca_lc(float(apca_y(hex_rgb(c))), yc))))


def dark_ink(el: dict, lg: dict, dl: float = 0.0) -> str:
    """Water labels: water_ink (blue); a fixed el "ink" ([print]); else computed sepia ink, or the element's entity
    colour with entity_text_l_min <= L <= entity_text_l_max and chroma x entity_text_c_scale (never_text colours ->
    the fallback); escalation lowers L by dl, never below ink_l_min."""
    if el.get("water"):
        return oklch_hex(max(lg["ink_l_min"], lg["water_ink_l"] - dl), lg["water_ink_c"], lg["water_ink_hue"])
    if el.get("ink"):
        L, C, h = rgb_oklch(hex_rgb(el["ink"]))
        return el["ink"] if dl <= 0 else oklch_hex(max(lg["ink_l_min"], L - dl), C, h)
    ent = el.get("colour")
    if ent and ent.lower() in [c.lower() for c in lg["never_text"]]:
        ent = lg["text_fallback"]
    if ent:
        L, C, h = rgb_oklch(hex_rgb(ent))
        L = max(min(L, lg["entity_text_l_max"]), lg.get("entity_text_l_min", 0.0))
        C *= lg.get("entity_text_c_scale", 1.0)
    else:
        L, C, h = lg["ink_l"], lg["ink_c"], lg["ink_hue"]
    return oklch_hex(max(lg["ink_l_min"], L - dl), C, h)


def light_ink(lg: dict, el: dict | None = None) -> str:
    if el and el.get("light"):
        return el["light"]
    return oklch_hex(lg["light_l"], lg["light_c"], lg["light_hue"])


def ladder(polarity: str, el: dict, lg: dict, halo: str) -> list[Style]:
    """Escalation for one polarity. Dark: step 0 halo_alpha; step 1 halo_alpha_max; step 2 + paper vignette
    vignette_alpha_min..max; step 3 ink L - ink_l_step per rung down to ink_l_min. Light: one style (glow + shadow).
    ladder_order "ink_first" (printed ink): step 0 the ink at halo_alpha; step 1 darker ink per rung down to
    ink_l_min; step 2 the darkest ink on the knock-back vignette (halo colour) vignette_alpha_min..max. No glyph
    halo: an element nothing passes is flagged."""
    if polarity == "light":
        return [Style("light", light_ink(lg, el), halo, 0.0, 0.0, 0)]
    if lg.get("ladder_order") == "ink_first":
        out = [Style("dark", dark_ink(el, lg), halo, lg["halo_alpha"], 0.0, 0)]
        k = 1
        while (ink := dark_ink(el, lg, k * lg["ink_l_step"])) != out[-1].ink:
            out.append(Style("dark", ink, halo, lg["halo_alpha"], 0.0, 1))
            k += 1
        n = lg["vignette_steps"]
        for j in range(n):
            va = lg["vignette_alpha_min"] + (lg["vignette_alpha_max"] - lg["vignette_alpha_min"]) * (j / (n - 1) if n > 1 else 1)
            out.append(Style("dark", out[-1].ink, halo, lg["halo_alpha"], round(va, 4), 2))
        return out
    out = [Style("dark", dark_ink(el, lg), halo, lg["halo_alpha"], 0.0, 0),
           Style("dark", dark_ink(el, lg), halo, lg["halo_alpha_max"], 0.0, 1)]
    n = lg["vignette_steps"]
    for k in range(n):
        va = lg["vignette_alpha_min"] + (lg["vignette_alpha_max"] - lg["vignette_alpha_min"]) * (k / (n - 1) if n > 1 else 1)
        out.append(Style("dark", dark_ink(el, lg), halo, lg["halo_alpha_max"], round(va, 4), 2))
    k = 1
    while True:
        ink = dark_ink(el, lg, k * lg["ink_l_step"])
        if ink == out[-1].ink:
            break
        out.append(Style("dark", ink, halo, lg["halo_alpha_max"], lg["vignette_alpha_max"], 3))
        k += 1
    return out


def vignette_alpha(xy: np.ndarray, box: tuple, alpha: float, lg: dict) -> np.ndarray:
    """Per-pixel alpha of the radial paper vignette: ellipse centred on the box, radii vignette_scale x the box half
    sizes x sqrt 2, full alpha inside, linear fade to 0 over the outer vignette_feather x box height."""
    if alpha <= 0:
        return np.zeros(len(xy))
    x, y, w, h = box
    a, b = lg["vignette_scale"] * w / 2 * math.sqrt(2), lg["vignette_scale"] * h / 2 * math.sqrt(2)
    r = np.hypot((xy[:, 0] - (x + w / 2)) / a, (xy[:, 1] - (y + h / 2)) / b)
    r0 = max(0.0, 1 - lg["vignette_feather"] * h / b)
    return alpha * np.clip((1 - r) / max(1e-6, 1 - r0), 0, 1)


def effective(s: Sample, st: Style, lg: dict, ring: bool = False) -> np.ndarray:
    """Background the glyphs sit on: map pixels with the vignette, then the halo at halo_alpha x its coverage
    (Sample.cov / ring_cov: 0 in counters, the blurred fall-off at the outer edge), composited in sRGB as the browser."""
    px = s.ring if ring else s.rgb
    if not len(px) or (st.halo_alpha <= 0 and st.vignette_alpha <= 0):
        return px
    hc = hex_rgb(st.halo)
    va = vignette_alpha(s.ring_xy if ring else s.xy, s.box, st.vignette_alpha, lg)
    out = px * (1 - va[:, None]) + hc * va[:, None]
    cov = s.ring_cov if ring else s.cov
    ha = st.halo_alpha * (np.ones(len(px)) if cov is None else cov)
    return out * (1 - ha[:, None]) + hc * ha[:, None]


@dataclass
class Eval:
    lc: float            # Lc at the hard-case percentile (positive = right polarity)
    lc_adjacent: float   # Lc against the darkest (dark ink) / lightest (light text) adjacent pixels
    wcag: float
    y: tuple             # p10, p50, p90 of the effective background Y


def evaluate(s: Sample, st: Style, lg: dict) -> Eval:
    eff = effective(s, st, lg)
    y = apca_y(eff)
    yt = float(apca_y(hex_rgb(st.ink)))
    dark = st.polarity == "dark"
    p = lg["dark_percentile"] if dark else lg["light_percentile"]
    yh = float(np.percentile(y, p))
    sign = 1 if dark else -1
    lc = sign * float(apca_lc(yt, yh))
    ring = effective(s, st, lg, ring=True)
    if len(ring):
        ya = float(np.percentile(apca_y(ring), lg["adjacent_percentile"] if dark else 100 - lg["adjacent_percentile"]))
        lca = sign * float(apca_lc(yt, ya))
    else:
        lca = lc
    lw = wcag_l(eff)
    lwh = float(np.percentile(lw, p))
    return Eval(lc, lca, float(wcag_ratio(float(wcag_l(hex_rgb(st.ink))), lwh)),
                tuple(float(v) for v in np.percentile(y, [10, 50, 90])))


def passes(e: Eval, kind: str, lg: dict, margin: float = 0.0) -> bool:
    return e.lc >= target(kind, lg) - margin and e.lc_adjacent >= lg["lc_floor"]


def polarity(samples: list[Sample], lg: dict) -> str:
    """Dark unless the median over the samples of the raw background p50 Y is below light_p50_max."""
    p50 = [float(np.percentile(apca_y(s.rgb), 50)) for s in samples]
    return "light" if p50 and float(np.median(p50)) < lg["light_p50_max"] else "dark"


# ---------------------------------------------------------------- choice


@dataclass
class Choice:
    """segments: [(t_start, Style)] in time order; flagged: no style reaches the target at every sample."""
    segments: list
    flagged: bool
    evals: list = field(default_factory=list)   # (t, Style, Eval) per sample


def _first_passing(styles: list[Style], samples: list[Sample], kind: str, lg: dict) -> Style | None:
    for st in styles:
        if all(passes(evaluate(s, st, lg), kind, lg) for s in samples):
            return st
    return None


def choose(el: dict, samples: list[Sample], lg: dict, fps: int) -> Choice:
    """One polarity and style for the whole interval: the rule polarity's ladder first, then the other polarity's;
    the first style passing every sample wins. Otherwise the stability fallback (`schedule`). An element "polarity"
    ([print]: dark ink on paper maps, light text on imagery) replaces the rule polarity."""
    pol = el.get("polarity") or polarity(samples, lg)
    other = "light" if pol == "dark" else "dark"
    pooled = np.concatenate([s.rgb for s in samples]) if samples else np.zeros((1, 3)) + 235
    # printed ink: the knock-back vignette is paper colour
    halo = lg["paper_colour"] if lg.get("ladder_order") == "ink_first" else halo_colour(np.median(pooled, axis=0), lg)
    lads = {p: ladder(p, el, lg, halo) for p in (pol, other)}
    if not lg.get("polarity_fallback", True):
        lads[other] = []
    st = _first_passing(lads[pol], samples, el["kind"], lg) or _first_passing(lads[other], samples, el["kind"], lg)
    if st:
        return Choice([(el["start"], st)], False, [(s.t, st, evaluate(s, st, lg)) for s in samples])
    return schedule(el, samples, lg, fps, pol, lads)


def _need(s: Sample, el: dict, lg: dict, pol: str, lads: dict) -> str | None:
    for p in (pol, "light" if pol == "dark" else "dark"):
        if any(passes(evaluate(s, st, lg), el["kind"], lg) for st in lads[p]):
            return p
    return None


def schedule(el: dict, samples: list[Sample], lg: dict, fps: int, pol: str, lads: dict) -> Choice:
    """Stability fallback. Each sample needs the rule polarity unless only the other passes there. A polarity switch
    happens at the first sample of a run of the other need whose observed span (first to last sample of the run) is
    >= hold_frames frames and that starts >= hold_frames after the previous switch. Each segment takes the first
    style of its polarity's ladder passing all its samples, else that ladder's last style (flagged). A switch whose
    crossfade midpoint (`midpoint`) with the final segment styles fails target - lc_fail_margin on the switch sample
    is removed, and the styles are recomputed, until every remaining switch passes."""
    kind, hold = el["kind"], lg["hold_frames"] / fps
    need = [_need(s, el, lg, pol, lads) or pol for s in samples]
    bounds = [(0, need[0] if _run_ok(samples, need, 0, hold) else pol)]
    last = -1e9
    for i in range(1, len(samples)):
        if need[i] != bounds[-1][1] and samples[i].t - last >= hold and _run_ok(samples, need, i, hold):
            bounds.append((i, need[i]))
            last = samples[i].t
    while True:
        bounds = [b for k, b in enumerate(bounds) if k == 0 or b[1] != bounds[k - 1][1]]
        styles = [_segment_style(lads[p], samples[i0:_end(bounds, k, samples)], kind, lg)
                  for k, (i0, p) in enumerate(bounds)]
        bad = next((k for k in range(1, len(bounds))
                    if not passes(evaluate(samples[bounds[k][0]], midpoint(styles[k - 1], styles[k],
                                  float(np.median(apca_y(samples[bounds[k][0]].rgb))), lg), lg),
                                  kind, lg, lg["lc_fail_margin"])), None)
        if bad is None:
            break
        del bounds[bad]
    segs, evals, flagged = [], [], False
    for k, (i0, p) in enumerate(bounds):
        part = samples[i0:_end(bounds, k, samples)]
        ev = [(s.t, styles[k], evaluate(s, styles[k], lg)) for s in part]
        flagged |= not all(passes(e, kind, lg) for _, _, e in ev)
        segs.append((el["start"] if k == 0 else part[0].t, styles[k]))
        evals += ev
    return Choice(segs, flagged, evals)


def _end(bounds, k, samples) -> int:
    return bounds[k + 1][0] if k + 1 < len(bounds) else len(samples)


def _run_ok(samples, need, i, hold) -> bool:
    """The run of equal need starting at sample i spans >= hold seconds between its first and last sample."""
    j = i
    while j + 1 < len(samples) and need[j + 1] == need[i]:
        j += 1
    return samples[j].t - samples[i].t >= hold - 1e-9


def _segment_style(lad: list[Style], part: list[Sample], kind: str, lg: dict) -> Style:
    return _first_passing(lad, part, kind, lg) or lad[-1]


def midpoint(a: Style, b: Style, bg_y: float, lg: dict) -> Style:
    """Painted state half way through the colour crossfade (crossfade_ms / 2 after the switch): inks mixed 50 % in
    sRGB; halo and vignette alpha moved by the EMA fraction 1 - exp(-crossfade_ms / 2 / ema_tau_ms) from a to b; dark
    polarity when the mixed ink is darker than the background median Y bg_y, else light."""
    ink = mix(a.ink, b.ink, 0.5)
    k = 1 - math.exp(-lg["crossfade_ms"] / 2 / lg["ema_tau_ms"]) if lg["ema_tau_ms"] > 0 else 1.0
    halo = a.halo if a.halo_alpha >= b.halo_alpha else b.halo
    return Style("dark" if apca_y(hex_rgb(ink)) < bg_y else "light", ink, halo,
                 a.halo_alpha + (b.halo_alpha - a.halo_alpha) * k,
                 a.vignette_alpha + (b.vignette_alpha - a.vignette_alpha) * k, max(a.step, b.step))


def naive_polarity(samples: list[Sample], lg: dict) -> list[str]:
    """Per-sample polarity from the p50 rule alone (no interval choice, no hysteresis): the flickering baseline."""
    return ["light" if float(np.percentile(apca_y(s.rgb), 50)) < lg["light_p50_max"] else "dark" for s in samples]


def switches(polarities: list[str]) -> int:
    return sum(1 for a, b in zip(polarities, polarities[1:]) if a != b)


# ---------------------------------------------------------------- scene inks


def scene_inks(choices: dict[str, Choice]) -> dict:
    """Scene JSON for the template: {element id: {"segs": [{t, pol, ink, halo, ha, va, step}], "flag": bool}}."""
    return {eid: {"flag": c.flagged, "segs": [{"t": round(t, 4), "pol": st.polarity, "ink": st.ink, "halo": st.halo,
                                               "ha": st.halo_alpha, "va": st.vignette_alpha, "step": st.step}
                                              for t, st in c.segments]}
            for eid, c in choices.items()}


def load_frames(item: dict):
    from PIL import Image
    return (np.asarray(Image.open(item["bg"]).convert("RGB")), np.asarray(Image.open(item["mask"]).convert("RGB")))


def collect(scene: dict, lg: dict, probe: dict) -> dict[str, list[Sample]]:
    """Samples per element from a background probe (dom_probe with bg mode) at `all_times`."""
    els = {e["id"]: e for e in elements(scene)}
    want = {e["id"]: set(sample_times(e, lg["samples"], scene["fps"])) for e in els.values()}
    out: dict[str, list[Sample]] = {k: [] for k in els}
    for item in probe["times"]:
        ids = [k for k in els if any(abs(item["t"] - t) < 1e-3 for t in want[k])]
        if not ids or not item.get("clip"):
            continue
        bg, mk = load_frames(item)
        org = (item["clip"]["x"], item["clip"]["y"])
        for r in item["rects"]:
            if r["id"] in ids and r.get("opacity", 1) >= 0.9 and r["w"] > 0:
                s = measure(bg, mk, r, els[r["id"]], lg, item["t"], org)
                if s is not None:
                    out[r["id"]].append(s)
    return out


def state(r: dict) -> dict | None:
    try:
        return json.loads(r.get("ink_state") or "null")
    except ValueError:
        return None
