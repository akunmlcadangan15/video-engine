"""Pure 2D geometry, polygon, and PIL text measurement helpers for beats layout."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

Box = tuple[float, float, float, float]   # x0, y0, x1, y1


FONT_FILES = {"script": "KaushanScript-Regular.ttf", "title": "ArchivoBlack-Regular.ttf",
              "serif": "PlayfairDisplay[wght].ttf", "inter": "Inter-Bold.otf",
              "garamond": "EBGaramond[wght].ttf", "garamond_italic": "EBGaramond-Italic[wght].ttf",
              "cinzel": "Cinzel[wght].ttf", "hand": "Kalam-Bold.ttf"}
VARIABLE = {"serif", "garamond", "garamond_italic", "cinzel"}   # fonts with a weight axis


def wt(role: str, weight: int | None) -> int | None:
    """Weight axis value for text_metrics: None for single-weight fonts."""
    return weight if role in VARIABLE else None


@dataclass(frozen=True)
class Metrics:
    adv: float      # CSS box width (advance + letter spacing)
    h: float        # CSS line box height (line-height x px)
    base: float     # baseline from the box top
    ink: Box        # ink box relative to the box top-left


@lru_cache(maxsize=512)
def text_metrics(text: str, role: str, px: float, line_height: float = 1.0, tracking_em: float = 0.0,
                 weight: int | None = None, nums: bool = False) -> Metrics:
    """Box of `text` as the browser lays it out (FreeType hhea metrics, half-leading model) with its ink bounds."""
    from PIL import ImageFont, features as pil_features

    from . import config
    f = ImageFont.truetype(str(config.FONTS_DIR / FONT_FILES[role]), px)
    if weight is not None:
        f.set_variation_by_axes([weight])
    # Pillow without libraqm rejects any features= (KeyError); fall back to None so layout still runs,
    # only the lnum/tnum shaping precision is lost.
    feats = ["lnum", "tnum"] if nums and pil_features.check("raqm") else None   # figures: lining tabular digits (template font-variant-numeric)
    track = tracking_em * px
    adv = f.getlength(text, features=feats) + track * len(text)
    asc, desc = f.getmetrics()
    h = line_height * px
    base = (h - (asc + desc)) / 2 + asc
    x0, y0, x1, y1 = f.getbbox(text, anchor="ls", features=feats)
    x1 += track * max(0, len(text) - 1)
    return Metrics(adv, h, base, (float(x0), base + y0, float(x1), base + y1))


def halo(theme: dict, glow: bool = False, px: float = 0.0, label: bool = False) -> tuple[float, float, float, float]:
    """Extra px around a text box for its outline, shadow (and title glow): left, top, right, bottom. With a
    profile [legibility] (theme["legibility"]): also its stroke halo (px x halo em), glow and shadow, whichever is
    larger. Its background vignette is not text and is not counted."""
    o = float(theme.get("text_outline_px", 0) or 0)
    b = float(theme.get("text_shadow_blur_px", 8) or 0) / 2
    dy = float(theme.get("text_shadow_dy_px", 3) or 0)
    g = float(theme.get("title_glow_px", 0) or 0) / 2 if glow else 0.0
    side = o + max(b, g)
    pad = [side, o + max(0.0, b - dy, g), side, o + max(b + dy, g)]
    lg = theme.get("legibility")
    if lg:
        hw = px * (lg["label_halo_width_em"] if label else lg["halo_width_em"]) if max(lg["halo_alpha_max"], lg.get("light_halo_alpha_max") or 0) > 0 else 0.0
        sb, sdy, gl = float(lg["shadow_blur_px"]), float(lg["shadow_dy_px"]), float(lg["glow_px"])
        lp = [hw + max(gl, sb), hw + max(gl, sb - sdy), hw + max(gl, sb), hw + max(gl, sb + sdy)]
        pad = [max(a, c) for a, c in zip(pad, lp)]
    return (pad[0], pad[1], pad[2], pad[3])


def text_box(m: Metrics, x: float, y: float, pad: tuple[float, float, float, float]) -> Box:
    """Painted box of a text element whose CSS box top-left is (x, y): advance joined with the ink horizontally
    (script overhang), ink vertically, plus the halo."""
    return (x + min(0.0, m.ink[0]) - pad[0], y + m.ink[1] - pad[1],
            x + max(m.adv, m.ink[2]) + pad[2], y + m.ink[3] + pad[3])


# ---------------------------------------------------------------------------------------------------- geometry

def inflate(b: Box, d: float) -> Box:
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def overlap_area(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def inside(b: Box, outer: Box, tol: float = 0.0) -> bool:
    return b[0] >= outer[0] - tol and b[1] >= outer[1] - tol and b[2] <= outer[2] + tol and b[3] <= outer[3] + tol


def _r(b) -> tuple:
    return tuple(round(v) for v in b)


def _seg_hits_box(p, q, b: Box) -> bool:
    """Liang-Barsky: does segment p-q touch box b."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - b[0]), (dx, b[2] - x0), (-dy, y0 - b[1]), (dy, b[3] - y0)):
        if pp == 0:
            if qq < 0:
                return False
            continue
        r = qq / pp
        if pp < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def poly_edges(poly):
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def crosses_edge(b: Box, poly) -> bool:
    return any(_seg_hits_box(p, q, b) for p, q in poly_edges(poly))


def point_in_poly(pt, poly) -> bool:
    x, y = pt
    c = False
    for (x1, y1), (x2, y2) in poly_edges(poly):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _pt_seg_dist(p, a, b) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def seg_box_dist(p, q, b: Box) -> float:
    if _seg_hits_box(p, q, b):
        return 0.0
    corners = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
    d = min(_pt_seg_dist(c, p, q) for c in corners)
    for e in (p, q):
        dx = max(b[0] - e[0], 0.0, e[0] - b[2])
        dy = max(b[1] - e[1], 0.0, e[1] - b[3])
        d = min(d, math.hypot(dx, dy))
    return d


def polyline_box_dist(pts, b: Box) -> float:
    return min(seg_box_dist(pts[i], pts[i + 1], b) for i in range(len(pts) - 1))


def clip_area(poly, b: Box) -> float:
    """Area of polygon `poly` inside box b (Sutherland-Hodgman)."""
    def clip(pts, inside_fn, inter):
        out = []
        for i in range(len(pts)):
            cur, prev = pts[i], pts[i - 1]
            ci, pi = inside_fn(cur), inside_fn(prev)
            if ci:
                if not pi:
                    out.append(inter(prev, cur))
                out.append(cur)
            elif pi:
                out.append(inter(prev, cur))
        return out

    def ix(x):
        return lambda a, c: (x, a[1] + (c[1] - a[1]) * (x - a[0]) / (c[0] - a[0]))

    def iy(y):
        return lambda a, c: (a[0] + (c[0] - a[0]) * (y - a[1]) / (c[1] - a[1]), y)
    pts = [tuple(p) for p in poly]
    for fn, it in ((lambda p: p[0] >= b[0], ix(b[0])), (lambda p: p[0] <= b[2], ix(b[2])),
                   (lambda p: p[1] >= b[1], iy(b[1])), (lambda p: p[1] <= b[3], iy(b[3]))):
        pts = clip(pts, fn, it)
        if not pts:
            return 0.0
    return abs(sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))) / 2


def rot_corners(cx, cy, w, h, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in
            ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def corners_box(cs) -> Box:
    xs, ys = [p[0] for p in cs], [p[1] for p in cs]
    return (min(xs), min(ys), max(xs), max(ys))


def quad_inside_poly(cs, poly) -> bool:
    if not all(point_in_poly(c, poly) for c in cs):
        return False
    for i in range(4):
        a, b = cs[i], cs[(i + 1) % 4]
        for p, q in poly_edges(poly):
            if _segs_cross(a, b, p, q):
                return False
    return True


def _segs_cross(a, b, c, d) -> bool:
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 1e-9) - (v < -1e-9)
    return o(a, b, c) != o(a, b, d) and o(c, d, a) != o(c, d, b)


def quad_outside_poly(cs, poly) -> bool:
    if any(point_in_poly(c, poly) for c in cs) or any(point_in_poly(p, cs) for p in poly):
        return False
    return not any(_segs_cross(cs[i], cs[(i + 1) % 4], p, q) for i in range(4) for p, q in poly_edges(poly))


def _closest(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return (a[0] + t * dx, a[1] + t * dy)
