"""Layout of HyperFrames map scenes told in beats (gsap_scene_config.beats) under a profile [layout] table.

A beat lists what is on screen: one active region, labelled pins, unlabelled markers, routes, the title or the data
card in Z1, area labels. The planner derives from the beats and the profile: the camera_path (one settle key per beat
that fits its map content into Z2), the entry and exit frames of every element, the Z1 positions of the title and the
data card, the area-label positions, a per-frame pin-label track, and the trimmed routes. `apply_segment` writes the
derived frames and camera keys into the segment; `scene_layout` is the `layout` block of the scene data the template
reads. `frame_items` is the on-screen state of one frame; video_engine/gates/layout_gates.py checks it.

Coordinates are output px; profile lengths are px at 1080 x 1920 and scale with the output size."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .timeline import Segment, Timeline


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Box = tuple[float, float, float, float]   # x0, y0, x1, y1


class LayoutGrid(_M):
    x0: float = 64                 # first column left edge
    columns: int = Field(6, ge=1)
    col_w: float = 104
    gutter: float = 40
    unit: int = Field(8, ge=1)     # spacing unit: positions and gaps are multiples
    snap_tol: float = 2            # column snap tolerance px
    unit_tol: float = 1            # multiple-of-unit tolerance px

    def edges(self) -> list[float]:
        return [self.x0 + i * (self.col_w + self.gutter) for i in range(self.columns)]


class LayoutZones(_M):
    safe: tuple[float, float, float, float] = (48, 288, 888, 1248)   # text safe area x0, y0, x1, y1
    z1: tuple[float, float, float, float] = (64, 304, 888, 440)      # top slot: title or data card
    z2: tuple[float, float, float, float] = (64, 488, 888, 1100)     # map focus: active region, pins, labels, routes
    z2_pad: float = 48             # camera fit padding inside Z2
    group_gap: float = 48          # min gap between zones


class LayoutSizes(_M):
    title: float = 112
    year: float = 128
    time: float = 56
    caption: float = 72            # informational: the caption size comes from caption_style
    pin_label: float = 52
    pin_label_min: float = 44
    pin_label_step: float = 4      # size fallback step
    area_label: float = 44
    area_tracking: float = 0.08    # em
    hero_ratio: float = 1.5        # hero >= ratio x next-largest text (caption excluded)
    big_number: float = 100        # numbers at or above this size are big


class LayoutBudget(_M):
    texts: int = 4                 # visible texts per frame incl. caption and fading ones, watermark excluded
    labelled_pins: int = 2
    routes: int = 1
    animating: int = Field(2, gt=0)   # elements entering, exiting, drawing, counting or moving at once
    big_numbers: int = 1
    saturated_regions: int = 1


class LayoutTiming(_M):
    enter_s: float = 0.3
    exit_s: float = 0.25
    stagger_s: float = 0.2
    stagger_min_s: float = 0.15
    stagger_max_s: float = 0.25
    exit_min_s: float = 0.2
    exit_max_s: float = 0.3
    settle_s: float = 0.8          # camera settle per beat
    head_drop_s: float = 0.6       # teardrop head entry (bounce)
    min_hold_s: float = 1.0        # hold >= max(min_hold_s, chars / chars_per_s)
    chars_per_s: float = 20
    move_fade_s: float = 0.3       # label move: fade out + in
    move_gain: float = 0.3         # non-urgent label move: min relative cost gain
    move_hold_s: float = 0.2       # ... held this long
    move_gap_s: float = 0.5        # never two label moves within this time
    moves_per_shot: int = 1        # non-urgent label moves per shot
    open_zoom_out: float = Field(1.35, ge=1.0)   # first key: the first beat's fit zoomed out by this factor


class LayoutLabels(_M):
    offset: float = 16             # label box to pin head
    edge_clear: float = 24         # label box to any region edge
    text_clear: float = 16         # label box to other text, heads, zones
    route_clear: float = 24        # route to any text
    area_inset: float = 24         # area label box inside its polygon
    leader_max: float = 120
    leader_px: float = 2
    route_pin_gap: float = 24      # route end to its to_pin head
    card_gap: float = 32           # data card: time ink starts this far right of the year ink
    dim_alpha: float = 0.10        # fill opacity of inactive regions
    caption_ground: float = 0.10   # max fraction of the caption box over a highlighted fill
    order: tuple[str, ...] = ("TR", "BR", "TL", "BL", "TC", "BC", "R", "L")


class LayoutFly(_M):
    """Profile [layout.fly]: managed texts enter and exit by scaling through the frame (template layout block).

    Scale runs over the span's enter / exit time; opacity runs over `<kind>_opacity_in_s` on entry (span time
    when unset) and with the scale on exit."""
    title_in: float = Field(2.6, ge=1.0)            # title enters at this scale
    figure_in: float = Field(2.2, ge=1.0)           # data card figures (year) enter at this scale
    label_in: float = Field(3.0, ge=1.0)            # pin and area labels enter at this scale
    out: float = Field(2.6, ge=1.0)                 # every managed text exits to this scale
    title_opacity_in_s: float | None = Field(0.15, gt=0)
    figure_opacity_in_s: float | None = Field(0.40, gt=0)
    label_opacity_in_s: float | None = Field(None, gt=0)
    ease_in: str = "expo.out"                       # GSAP ease names
    ease_out: str = "power2.in"                     # cubic ease-in


class LayoutProfile(_M):
    """Profile [layout] table. Its presence turns on the beats layout and the L gates."""
    grid: LayoutGrid = LayoutGrid()
    zones: LayoutZones = LayoutZones()
    sizes: LayoutSizes = LayoutSizes()
    budget: LayoutBudget = LayoutBudget()
    timing: LayoutTiming = LayoutTiming()
    labels: LayoutLabels = LayoutLabels()
    fly: LayoutFly | None = None   # None = opacity fades

    @model_validator(mode="after")
    def _units(self):
        u = self.grid.unit
        for name, v in (("labels.offset", self.labels.offset), ("labels.edge_clear", self.labels.edge_clear),
                        ("labels.text_clear", self.labels.text_clear), ("labels.card_gap", self.labels.card_gap),
                        ("zones.z2_pad", self.zones.z2_pad), ("zones.group_gap", self.zones.group_gap)):
            if v % u:
                raise ValueError(f"layout {name} = {v} is not a multiple of grid.unit {u}")
        return self


# ---------------------------------------------------------------------------------------------------- text metrics

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
    from PIL import ImageFont

    from . import config
    f = ImageFont.truetype(str(config.FONTS_DIR / FONT_FILES[role]), px)
    if weight is not None:
        f.set_variation_by_axes([weight])
    feats = ["lnum", "tnum"] if nums else None   # figures: lining tabular digits (template font-variant-numeric)
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
        hw = px * (lg["label_halo_width_em"] if label else lg["halo_width_em"]) if lg["halo_alpha_max"] > 0 else 0.0
        sb, sdy, gl = float(lg["shadow_blur_px"]), float(lg["shadow_dy_px"]), float(lg["glow_px"])
        lp = [hw + max(gl, sb), hw + max(gl, sb - sdy), hw + max(gl, sb), hw + max(gl, sb + sdy)]
        pad = [max(a, c) for a, c in zip(pad, lp)]
    return tuple(pad)


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


# ---------------------------------------------------------------------------------------------------- the plan

HEAD = (-22.0, -53.0, 22.0, -9.0)    # teardrop head box relative to the pin point (template .drop)
HEAD_C = (0.0, -31.0)
SHADOW = (-17.0, -7.0, 17.0, 7.0)

ENTRY_ORDER = ("heading", "region", "dim", "head", "label", "area", "route", "fig", "title", "note")
EXIT_ORDER = ("note", "label", "area", "title", "heading", "fig", "route", "head", "dim")


@dataclass
class Span:
    t_in: int
    d_in: int
    t_out: Optional[int] = None
    d_out: int = 0

    def opacity(self, f: float) -> float:
        if f < self.t_in:
            return 0.0
        a = 1.0 if self.d_in <= 0 else min(1.0, (f - self.t_in) / self.d_in)
        if self.t_out is not None and f >= self.t_out:
            a = min(a, max(0.0, 1.0 - (f - self.t_out) / max(1, self.d_out)))
        return a


@dataclass
class Label:
    """One pin label (or area label with a leader) position: CSS box offset from the pin point."""
    dx: float
    dy: float
    px: float
    name: str                       # candidate name (TR...) or "leader:<dir>:<len>"
    leader: Optional[tuple] = None  # (x1, y1, x2, y2) relative to the pin point
    rank: float = 0.0


@dataclass
class Area:
    text: str
    region: int
    px: float
    w: float                        # text box incl. halo
    h: float
    anchor: tuple[float, float]     # map px under the box centre
    angle: float = 0.0
    leader: Optional[tuple] = None  # (mx1, my1, mx2, my2) map px from the polygon to the box
    inside: bool = True
    water: bool = False
    key: int = 0
    role: str = "title"             # font role (FONT_FILES); [print]: the surface's area font
    weight: Optional[int] = None
    tracking: float = 0.0           # em
    tracking_in: Optional[float] = None   # [print]: entry tracking (eases to `tracking` over the entry)
    title: bool = False             # [print]: the scene title placed inside its region


@dataclass
class Note:
    """[print] hand annotation: Kalam text centred on a map point, rotated, with an arrow to another map point."""
    text: str
    px: float
    w: float                        # painted box (halo pad included)
    h: float
    anchor: tuple[float, float]     # map px under the text centre
    angle: float
    arrow: Optional[tuple] = None   # (x1, y1, x2, y2) screen px relative to the anchor's screen point


@dataclass
class Heading:
    """[print] date heading: Cinzel caps centred at a baseline with a rule and a diamond on each side."""
    text: str
    px: float
    x: float                        # CSS box left / top
    y: float
    box: Box                        # painted box: text ink + rules + diamonds
    css: Box
    base: float
    rule_y: float                   # rule centre line y
    rules: tuple                    # (x0, x1) of the left rule, (x0, x1) of the right rule


@dataclass
class Plan:
    W: int
    H: int
    fps: int
    n: int
    mw: float
    mh: float
    L: LayoutProfile
    theme: dict
    keys: list[dict]                       # camera keys {at_frame, focus, zoom, move, move_s}
    cam: dict                              # hyperframes.camera_plan result
    pins: list[tuple[float, float]]        # map px
    pin_labels: list[str]
    regions: list[list[tuple[float, float]]]
    routes: list[list[tuple[float, float]]]  # trimmed, map px
    route_draw: list[int]                  # draw frames
    spans: dict[str, list[Span]]           # element key -> show spans
    region_steps: list[list[tuple[int, int, float, float]]]   # per region (frame, dur, from_level, to_level); level 1 = full
    title: Optional[dict] = None           # {x, y, px, box, text}
    figs: dict[int, dict] = field(default_factory=dict)   # figure index -> {x, y, px, box, text, kind}
    areas: dict[int, Area] = field(default_factory=dict)  # area key -> Area
    tracks: dict[int, list[tuple[int, Optional[Label], bool]]] = field(default_factory=dict)  # pin -> [(frame, label, hard)]
    anim: list[tuple[int, int, str]] = field(default_factory=list)   # (f0, f1, key) animation windows
    beats: list[dict] = field(default_factory=list)                 # per beat: {at, exits_end, settle_end, entries}
    log: list[str] = field(default_factory=list)
    pr: Optional[dict] = None                      # [print]: printstyle.scene_block (surface, roles, values)
    headings: dict[int, Heading] = field(default_factory=dict)   # beat index -> heading
    notes: dict[int, Note] = field(default_factory=dict)         # note index -> note

    # -------- camera
    def camera(self, f: float) -> dict:
        return camera_at(self.cam, f)

    def to_screen(self, f: float, p) -> tuple[float, float]:
        c = self.camera(f)
        return (c["x"] + c["s"] * p[0], c["y"] + c["s"] * p[1])

    def k(self) -> tuple[float, float]:
        return self.W / 1080, self.H / 1920

    def scaled(self, b) -> Box:
        kx, ky = self.k()
        return (b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky)

    def region_level(self, i: int, f: float) -> float:
        lv = 0.0
        for (t, d, a, b) in self.region_steps[i]:
            if f < t:
                break
            lv = b if d <= 0 or f >= t + d else a + (b - a) * (f - t) / d
        return lv

    def visible(self, key: str, f: float) -> float:
        return max((s.opacity(f) for s in self.spans.get(key, [])), default=0.0)

    def label_at(self, i: int, f: float) -> Optional[Label]:
        cur = None
        for (t, lab, _) in self.tracks.get(i, []):
            if t > f:
                break
            cur = lab
        return cur


def camera_at(cam: dict, f: float) -> dict:
    prev = cam["start"]
    for L in cam["legs"]:
        if f < L["f1"]:
            if f <= L["f0"]:
                return prev
            p = (f - L["f0"]) / (L["f1"] - L["f0"])
            from .hyperframes import leg_ease
            e = leg_ease(L, prev["s"])(p)
            return {k: prev[k] + (L["to"][k] - prev[k]) * e for k in (("x", "y", "s", "t") if "t" in prev else ("x", "y", "s"))}
        prev = L["to"]
    return prev


def _glide(t):
    return 0.35 * t + 0.65 * (1 - math.cos(math.pi * t)) / 2


def _quart_in_out(t):   # GSAP power3.inOut
    return 8 * t ** 4 if t < 0.5 else 1 - (-2 * t + 2) ** 4 / 2


EASES = {"glide": _glide, "drift": lambda t: t, "whip": _quart_in_out}
PULSE_STEPS = (0.75, 0.5, 0.25, 0.0)    # pulse sizes (fractions of [camera] pulse_zoom) tried when a wait conflicts
PULSE_FLOOR = 1e-3                      # ln zoom; a smaller pulse size is 0 (drift only)
_LABEL_MOVE = re.compile(r"\bat frame (\d+)\b")


def label_move_frames(log: list[str]) -> set[int]:
    """Frames at which the planner log records a pin label hidden or moved ("pin label ... at frame N")."""
    return {int(m.group(1)) for ln in log if ln.startswith("pin label") for m in _LABEL_MOVE.finditer(ln)}


def shrink_pulses(keys: list[dict], bad: dict[int, tuple[int, set[int]]], zoom: float) -> bool:
    """Set keys[i]["pulse"] to one size per away pulse of its wait, each pulse listed in `bad` (key -> (away pulses,
    indices)) taking the next smaller PULSE_STEPS size of `zoom` (the profile pulse size), rounded to 4 decimals.
    Every step is strictly smaller; a size below PULSE_FLOOR is 0. Returns whether any size changed (a positive
    size always changes, so repeated calls reach 0 for every listed pulse)."""
    changed = False
    for i, (n_away, hit) in bad.items():
        cur = keys[i].get("pulse", zoom)
        cur = list(cur) if isinstance(cur, list) else [cur]
        cur = [cur[min(m, len(cur) - 1)] for m in range(n_away)]
        for m in hit:
            nxt = round(next((zoom * q for q in PULSE_STEPS if round(zoom * q, 4) < cur[m] - 1e-9), 0.0), 4)
            nxt = 0.0 if nxt < PULSE_FLOOR or nxt >= cur[m] else nxt
            changed |= nxt != cur[m]
            cur[m] = nxt
        keys[i]["pulse"] = cur
    return changed


DRIFT_SHARE = 0.7      # a conflicting wait's drift bound: this share of the drift offset of its first conflict frame
DRIFT_MIN = 0.03       # ln zoom; smallest drift bound (a turn every DRIFT_MIN / camera_drift_per_s seconds)


def bound_drift(keys: list[dict], hit: dict[int, float]) -> bool:
    """Set keys[i]["drift_max"] to DRIFT_SHARE of the conflict offset of each wait in `hit` (key -> drift ln offset of
    its first conflict frame), never below DRIFT_MIN and only when smaller than the current bound, rounded to 4
    decimals. Returns whether any bound changed (repeated calls reach DRIFT_MIN and then report no change)."""
    changed = False
    for i, g in hit.items():
        cur = keys[i].get("drift_max")
        nxt = round(max(DRIFT_MIN, DRIFT_SHARE * g), 4)
        if cur is not None and nxt >= cur - 1e-9:
            nxt = round(max(DRIFT_MIN, DRIFT_SHARE * cur), 4)
        if cur is None or nxt < cur - 1e-9:
            keys[i]["drift_max"] = nxt
            changed = True
    return changed


def cubic_bezier(x1: float, y1: float, x2: float, y2: float, t: float) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2) at progress t (x solved by bisection; same math as the template's bez)."""
    if t <= 0 or t >= 1:
        return min(1.0, max(0.0, t))
    bx = lambda u: 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if bx(m) < t else (lo, m)
    u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


def profile_layout(tl: "Timeline") -> LayoutProfile | None:
    from . import profiles
    return profiles.of(tl).layout


def is_beats(seg: "Segment", tl: "Timeline") -> bool:
    from .timeline import HYPERFRAMES
    sc = seg.gsap_scene_config
    return seg.engine_type == HYPERFRAMES and sc is not None and bool(sc.beats) and profile_layout(tl) is not None


def _frames(s: float, fps: int) -> int:
    return max(1, round(s * fps))


@lru_cache(maxsize=8)
def _detail(map_path: str, mtime: float):
    """Map detail (normalised gradient magnitude) as an integral image over the map at 1/4 size."""
    import numpy as np
    from PIL import Image
    im = Image.open(map_path).convert("L")
    w, h = im.size
    g = np.asarray(im.resize((max(1, w // 4), max(1, h // 4))), dtype=np.float32)
    gx = np.abs(np.diff(g, axis=1, append=g[:, -1:]))
    gy = np.abs(np.diff(g, axis=0, append=g[-1:, :]))
    d = np.clip((gx + gy) / max(1.0, float(np.percentile(gx + gy, 95))), 0, 1)
    ii = np.zeros((d.shape[0] + 1, d.shape[1] + 1), dtype=np.float64)
    ii[1:, 1:] = d.cumsum(0).cumsum(1)
    return ii, (w, h)


def _plan_key(seg: "Segment", tl: "Timeline", project_dir: Path) -> str:
    from . import profiles
    sc = seg.gsap_scene_config.model_dump(mode="json", exclude={"camera_path", "title_at_frame"})
    for k in ("pins", "regions", "routes", "figures", "cards"):
        for e in sc.get(k) or []:
            e.pop("at_frame", None)
    p = project_dir / seg.gsap_scene_config.map_asset
    return json.dumps([sc, seg.n_frames, tl.settings.model_dump(mode="json"), profiles.of(tl).blake3,
                       str(p.resolve()), p.stat().st_mtime], sort_keys=True)


_PLANS: dict[str, Plan] = {}


def plan(seg: "Segment", tl: "Timeline", project_dir: Path) -> Plan:
    key = _plan_key(seg, tl, Path(project_dir))
    if key not in _PLANS:
        if len(_PLANS) > 16:
            _PLANS.clear()
        _PLANS[key] = _Planner(seg, tl, Path(project_dir)).run()
    return _PLANS[key]


class _Planner:
    def __init__(self, seg: "Segment", tl: "Timeline", project_dir: Path):
        from . import hyperframes, profiles
        self.hf = hyperframes
        self.seg, self.tl, self.sc = seg, tl, seg.gsap_scene_config
        S = tl.settings
        self.W, self.H, self.fps, self.n = S.width, S.height, S.fps, seg.n_frames
        self.L = profiles.of(tl).layout
        prof = profiles.of(tl)
        self.theme = prof.render_values("hf")
        self.max_zoom = prof.camera.max_zoom
        lg = prof.legibility
        if lg is not None:   # read-only here: halo extents and the effective region fill opacity
            self.theme["legibility"] = lg.model_dump(mode="json")
        self.fill_opacity = lg.entity_fill_alpha if lg is not None else self.theme["region_fill_opacity"]
        self.map_path = project_dir / self.sc.map_asset
        iw, ih = hyperframes.map_image_size(self.map_path)
        cover = max(self.W / iw, self.H / ih)
        self.mw, self.mh = iw * cover, ih * cover
        self.kx, self.ky = self.W / 1080, self.H / 1920
        sc = self.sc
        self.pins = [hyperframes._pin_xy(p, sc, self.mw, self.mh) for p in sc.pins]
        self.regions = [[(x * self.mw, y * self.mh) for x, y in r.polygon] for r in sc.regions or []]
        self.routes_raw = [[(x * self.mw, y * self.mh) for x, y in r.points] for r in sc.routes or []]
        self.z1 = self.box(self.L.zones.z1)
        self.z2 = self.box(self.L.zones.z2)
        self.safe = self.box(self.L.zones.safe)
        self.T = self.L.timing
        self.f_in, self.f_out = _frames(self.T.enter_s, self.fps), _frames(self.T.exit_s, self.fps)
        self.f_stag = _frames(self.T.stagger_s, self.fps)
        self.f_settle = _frames(self.T.settle_s, self.fps)
        self.f_drop = _frames(self.T.head_drop_s, self.fps)
        self.label_role = self.theme.get("label_font", "script")
        self.label_role = "script" if self.label_role == "script" else "inter"
        self.title_role = "title" if self.theme.get("title_font") == "title" else "inter"
        self.label_wt: int | None = None
        self.pr = prof.print
        self.prb: dict | None = None
        if self.pr is not None:   # printed typography: fonts by the map surface
            from . import printstyle
            self.prb = printstyle.scene_block(self.pr, printstyle.surface(self.map_path, self.pr))
            self.roles = printstyle.roles(self.prb["surface"], self.pr)
            self.label_role, self.label_wt = self.roles["label"][0], wt(*self.roles["label"][:2])
            self.title_role = self.roles["title"][0]
        self.f_area_in = _frames(self.pr.title_in_s, self.fps) if self.pr else None
        self.log: list[str] = []

    def box(self, b) -> Box:
        return (b[0] * self.kx, b[1] * self.ky, b[2] * self.kx, b[3] * self.ky)

    # ------------------------------------------------------------------ sets per beat
    def beat_set(self, b, k: int = 0) -> set:
        s = {("head", i) for i in b.pins + b.markers} | {("label", i) for i in b.pins} | {("route", i) for i in b.routes}
        s |= {("fig", i) for i in b.card}
        if b.title and self.pr is not None:     # [print]: the title is printed inside the beat's region
            if b.region is not None:
                s.add(("area", self.area_id(self.sc.title, b.region, False, True)))
            elif self.sc.title:
                self.log.append(f"beat at frame {b.at_frame}: title hidden (a printed title needs the beat's region)")
        elif b.title:
            s.add(("title", 0))
        s |= {("area", self.area_id(a.text, a.region, a.water)) for a in b.area_labels}
        if b.heading:
            s.add(("heading", k))
            self.heading_text[k] = b.heading
        for nt in b.notes:
            s.add(("note", self.note_id(nt)))
        return s

    def area_id(self, text: str, region: int, water: bool = False, title: bool = False) -> int:
        k = (text, region)
        if k not in self.area_keys:
            self.area_keys.append(k)
        if water:
            self.area_water.add(self.area_keys.index(k))
        if title:
            self.area_title.add(self.area_keys.index(k))
        return self.area_keys.index(k)

    def note_id(self, nt) -> int:
        if not any(n is nt for n in self.note_list):
            self.note_list.append(nt)
        return next(j for j, n in enumerate(self.note_list) if n is nt)

    def content_beat(self, k: int):
        """The beat whose map content frames beat k: k itself, else the next beat with content (None = none)."""
        for b in self.sc.beats[k:]:
            if b.region is not None or b.routes or b.pins or b.markers:
                return b
        return None

    # ------------------------------------------------------------------ camera fit
    def content_box(self, b, s: float) -> Box:
        """Screen-space box (at scale s, translation 0) of a beat's map content."""
        pts = []
        if b.region is not None:
            pts += [(s * x, s * y) for x, y in self.regions[b.region]]
        for r in b.routes:
            pts += [(s * x, s * y) for x, y in self.trimmed_guess(r)]
        boxes = [corners_box(pts)] if pts else []
        lift = self.label_lift()
        for i in b.pins + b.markers:
            px, py = self.pins[i]
            boxes.append((s * px + HEAD[0], s * py + HEAD[1] - (lift if i in b.pins else 0.0),
                          s * px + HEAD[2], s * py + SHADOW[3]))
        return (min(q[0] for q in boxes), min(q[1] for q in boxes), max(q[2] for q in boxes), max(q[3] for q in boxes))

    def label_lift(self) -> float:
        """[print]: height above the pin head of the first-choice label (leader A: gap, leader, gap, label line), so
        the camera frames it; 0 without [print] (labels then sit beside the head)."""
        if self.pr is None:
            return 0.0
        px = (self.pr.label_px if self.prb["surface"] == "paper" else self.pr.label_dark_px) * self.ky
        return 2 * self.pr.leader_gap * self.ky + self.pr.leader_len * self.ky + 1.2 * px

    def trimmed_guess(self, r: int):
        return self.routes_raw[r]

    def fit(self, b) -> tuple[tuple[float, float], float]:
        if b is None:     # no beat has map content: the whole map
            return (0.5, 0.5), 1.0
        pad = self.L.zones.z2_pad
        inner = (self.z2[0] + pad * self.kx, self.z2[1] + pad * self.ky, self.z2[2] - pad * self.kx, self.z2[3] - pad * self.ky)
        aw, ah = inner[2] - inner[0], inner[3] - inner[1]
        lo, hi = 1.0, self.max_zoom   # a camera key cannot zoom further: small content stops at [camera] max_zoom
        for _ in range(40):
            m = (lo + hi) / 2
            cb = self.content_box(b, m)
            if cb[2] - cb[0] <= aw and cb[3] - cb[1] <= ah:
                lo = m
            else:
                hi = m
        from . import hyperframes

        def framed(s):   # content box on screen at zoom s, centred in Z2 inner (the render clamps to the map edge)
            cb = self.content_box(b, s)
            X = (inner[0] + inner[2]) / 2 - (cb[0] + cb[2]) / 2
            Y = (inner[1] + inner[3]) / 2 - (cb[1] + cb[3]) / 2
            c = ((self.W / 2 - X) / s, (self.H / 2 - Y) / s)
            st = hyperframes._state(c, s, self.W, self.H, self.mw, self.mh)
            return cb, c, (cb[0] + st["x"], cb[1] + st["y"], cb[2] + st["x"], cb[3] + st["y"])

        s = lo
        pulse = self.theme.get("camera_pulse")
        if pulse and pulse["dir"] > 0:   # push-in pulses: headroom so the pushed-in view still fits Z2 inner, as
            for q in (1.0, 0.75, 0.5, 0.25):   # far as the map edge keeps the content centred in it
                h = max(1.0, lo * math.exp(-pulse["zoom"] * q))
                if inside(framed(h)[2], inner, 1.0):
                    s = h
                    break
        cb, c, got = framed(s)
        if s <= 1.0 + 1e-6 and (cb[2] - cb[0] > aw + 1 or cb[3] - cb[1] > ah + 1) or not inside(got, inner, 1.0):
            self.log.append(f"beat at frame {b.at_frame}: content {_r(got)} does not fit Z2 inner {_r(inner)} "
                            f"at zoom {s:.2f} (map edge or size)")
        return (min(1.0, max(0.0, c[0] / self.mw)), min(1.0, max(0.0, c[1] / self.mh))), s

    # ------------------------------------------------------------------ schedule
    def schedule(self, items, start: int, gap: int, dur_of, anim: list) -> dict:
        """Start frames for `items` in order: each >= the previous start + gap and only when fewer than
        budget.animating windows overlap its own window."""
        out, t = {}, start
        cap = self.L.budget.animating
        for it in items:
            d = dur_of(it)
            while any(sum(1 for (a, z, _) in anim if a <= f < z) >= cap for f in range(t, t + d)):
                t += 1
            out[it] = t
            anim.append((t, t + d, f"{it[0]}:{it[1]}"))
            t += gap
        return out

    def run(self) -> Plan:
        sc, fps, n = self.sc, self.fps, self.n
        beats = sc.beats
        self.area_keys: list[tuple[str, int]] = []
        self.area_water: set[int] = set()
        self.area_title: set[int] = set()
        self.heading_text: dict[int, str] = {}
        self.note_list: list = []
        sets = [self.beat_set(b, k) for k, b in enumerate(beats)]
        nreg = len(self.regions)
        levels = [[(1.0 if b.region == r else self.L.labels.dim_alpha / max(1e-6, self.fill_opacity))
                   for r in range(nreg)] for b in beats]
        spans: dict[str, list[Span]] = {}
        region_steps: list[list] = [[] for _ in range(nreg)]
        anim: list[tuple[int, int, str]] = []
        keys = []
        beat_info = []
        prev_view = None
        route_draw = [_frames(r.draw_s or self.theme["route_draw_s"], fps) for r in sc.routes or []]
        count_f = {i: _frames(f.count_s or self.theme["counter_s"], fps) for i, f in enumerate(sc.figures or [])
                   if f.motion == "counter"}
        for k, b in enumerate(beats):
            f0 = b.at_frame
            prev = sets[k - 1] if k else set()
            exits = sorted(prev - sets[k], key=lambda e: (EXIT_ORDER.index(e[0]), e[1]))
            if k:
                exits += [("dim", r) for r in range(nreg) if levels[k][r] < levels[k - 1][r]]
            ex = self.schedule(exits, f0, _frames(self.T.stagger_min_s, fps), lambda e: self.f_out, anim)
            for e, t in ex.items():
                if e[0] == "dim":
                    region_steps[e[1]].append((t, self.f_out, levels[k - 1][e[1]], levels[k][e[1]]))
                else:
                    spans[f"{e[0]}:{e[1]}"][-1].t_out, spans[f"{e[0]}:{e[1]}"][-1].d_out = t, self.f_out
            ex_end = max([t + self.f_out for t in ex.values()], default=f0)
            settle_end = ex_end
            if k == 0 or b.camera:
                focus, zoom = self.fit(self.content_beat(k))
                if k == 0:
                    keys.append({"at_frame": 0, "focus": list(focus), "zoom": max(1.0, zoom / self.T.open_zoom_out), "move": "glide"})
                settle_end = ex_end + self.f_settle
                keys.append({"at_frame": settle_end, "focus": list(focus), "zoom": zoom, "move": "settle",
                             "move_s": round(self.f_settle / fps, 4)})
                if b.hold == "read":
                    keys[-1]["pulse"] = 0.0   # intent: the viewer reads; the wait keeps only the drift
                prev_view = (focus, zoom)
            entries = sorted(sets[k] - prev, key=lambda e: (ENTRY_ORDER.index(e[0]), e[1]))
            regs = []
            for r in range(nreg):
                if k == 0:
                    regs.append(("region" if levels[0][r] >= 1 else "dim", r))
                elif levels[k][r] > levels[k - 1][r]:
                    regs.append(("region", r))
            entries = sorted(regs, key=lambda e: (ENTRY_ORDER.index(e[0]), e[1])) + entries

            def dur(e):
                if e[0] == "head":
                    return self.f_drop
                if e[0] == "route":
                    return route_draw[e[1]]
                if e[0] == "fig" and e[1] in count_f:
                    return max(self.f_in, count_f[e[1]])
                return self.span_in(e[0])
            en = self.schedule(entries, settle_end, self.f_stag, dur, anim)
            for e, t in en.items():
                if e[0] in ("region", "dim"):
                    region_steps[e[1]].append((t, self.f_in, 0.0 if k == 0 else levels[k - 1][e[1]], levels[k][e[1]]))
                else:
                    d = self.f_drop if e[0] == "head" else self.span_in(e[0])
                    spans.setdefault(f"{e[0]}:{e[1]}", []).append(Span(t, d))
            beat_info.append({"at": f0, "exits_end": ex_end, "settle_end": settle_end,
                              "entries": {f"{e[0]}:{e[1]}": t for e, t in en.items()},
                              "exits": {f"{e[0]}:{e[1]}": t for e, t in ex.items()}, "camera": k == 0 or b.camera})
        self.spans, self.region_steps, self.anim, self.beat_info = spans, region_steps, anim, beat_info
        log0 = list(self.log)
        pulse = self.theme.get("camera_pulse")
        rate = self.theme["camera_drift_per_s"]
        while True:
            self.log = list(log0)
            p = self.place(keys, route_draw)
            bad = self.pulse_conflicts(p) if pulse else {}
            drift = self.drift_conflicts(p) if rate > 0 else {}
            shrunk = bool(bad) and shrink_pulses(keys, bad, pulse["zoom"])
            bounded = bool(drift) and bound_drift(keys, drift)
            if not (shrunk or bounded):
                break
        return p

    def place(self, keys: list[dict], route_draw: list[int]) -> Plan:
        """Camera plan (through the same code as the render) and every placement that depends on it."""
        sc, fps, n = self.sc, self.fps, self.n
        from .timeline import CameraKey
        cks = [CameraKey(**{k2: v for k2, v in kk.items() if not (k2 == "move_s" and kk["move"] != "settle")}) for kk in keys]
        tmp = sc.model_copy(update={"camera_path": cks})
        self.cam = self.hf.camera_plan(tmp, self.W, self.H, self.mw, self.mh, n, fps, self.theme)
        p = Plan(self.W, self.H, fps, n, self.mw, self.mh, self.L, self.theme, keys, self.cam, self.pins,
                 [x.label for x in sc.pins], self.regions, [], [], self.spans, self.region_steps)
        p.anim, p.beats = list(self.anim), self.beat_info
        p.route_draw = route_draw
        p.pr = self.prb
        p.routes = [self.trim_route(i, p) for i in range(len(self.routes_raw))]
        self.z1_items(p)
        if self.pr is not None:
            self.place_headings(p)
            self.place_notes(p)
        self.place_areas(p)
        self.place_labels(p)
        p.log = self.log
        return p

    def span_in(self, kind: str) -> int:
        """Entry length of an element kind ([print]: titles and place names, headings, notes have their own)."""
        if self.pr is not None:
            if kind == "area":
                return self.f_area_in
            if kind == "heading":
                return _frames(max(self.pr.heading_fade_s, self.pr.rule_draw_s), self.fps)
            if kind == "note":
                return _frames(self.pr.note_write_s, self.fps)
        return self.f_in

    def pulse_conflicts(self, p: Plan) -> dict[int, tuple[int, set[int]]]:
        """Per key whose wait breaks a per-frame layout rule (L01-L07 checks of video_engine/gates/layout_gates.py) or
        moves a pin label on a frame off the base drift (inside a pulse or away after one): (away pulses in the wait,
        indices of the away pulses whose frames break it)."""
        from .gates import layout_gates as G
        L, W, H = self.L, self.W, self.H
        hit: dict[int, set[int]] = {}
        n_away: dict[int, int] = {}
        ats = [k["at_frame"] for k in p.keys]
        moves = label_move_frames(p.log)
        for leg in p.cam["legs"]:
            if "away" not in leg:
                continue
            i, m = max(j for j, a in enumerate(ats) if a <= leg["f0"]), leg["away"]
            cur = p.keys[i].get("pulse")
            n_away[i] = len(cur) if isinstance(cur, list) else max(n_away.get(i, 0), m + 1)
            for f in range(leg["f0"], leg["f1"]):
                items = frame_items(p, f)
                if (G.overlap(items, L, W, H) or G.boundary(items, L, W, H) or G.zones(items, L, W, H)
                        or G.route_clear(items, L, W, H) or G.budget(items, L, len(animating(p, f)))
                        or f in moves):
                    hit.setdefault(i, set()).add(m)
                    break
        return {i: (n_away[i], h) for i, h in hit.items()}

    def drift_conflicts(self, p: Plan) -> dict[int, float]:
        """Per key whose wait breaks a per-frame overlap, boundary, zone or route rule (the L01, L02, L06, L07 checks
        of video_engine/gates/layout_gates.py) on a frame of its base drift that the same frame with the camera held
        at the wait's first frame does not break (the drift caused it): the drift ln offset of the first such frame."""
        import dataclasses
        from .gates import layout_gates as G
        L, W, H = self.L, self.W, self.H
        ats = [k["at_frame"] for k in p.keys]

        def broken(pp, f):
            items = frame_items(pp, f)
            return bool(G.overlap(items, L, W, H) or G.boundary(items, L, W, H) or G.zones(items, L, W, H)
                        or G.route_clear(items, L, W, H))

        out: dict[int, float] = {}
        held: dict[int, Plan] = {}
        for leg in p.cam["legs"]:
            if leg["ease"] != "drift" or "away" in leg:
                continue
            i = max(j for j, a in enumerate(ats) if a <= leg["f0"])
            if i in out:
                continue
            for f in range(leg["f0"] + 1, leg["f1"]):
                if not broken(p, f):
                    continue
                if i not in held:
                    w0 = next(x["f0"] for x in p.cam["legs"] if x["f0"] >= ats[i])
                    held[i] = dataclasses.replace(p, cam={"start": p.camera(w0), "legs": []})
                if not broken(held[i], f):
                    out[i] = abs(math.log(p.camera(f)["s"] / p.camera(ats[i])["s"]))
                    break
        return out

    # ------------------------------------------------------------------ routes
    def trim_route(self, i: int, p: Plan):
        r = self.sc.routes[i]
        pts = list(self.routes_raw[i])
        if r.to_pin is None:
            return pts
        span = (self.spans.get(f"route:{i}") or [Span(0, 1)])[0]
        c = p.camera(span.t_in + p.route_draw[i])
        s = c["s"]
        px, py = self.pins[r.to_pin]
        gap = self.L.labels.route_pin_gap * self.kx
        head = (px * s + HEAD[0], py * s + HEAD[1], px * s + HEAD[2], py * s + SHADOW[3])
        # walk the polyline (map px) and stop at the last screen point >= gap (euclidean) from the head box
        segs = [(pts[j], pts[j + 1]) for j in range(len(pts) - 1)]
        out = [pts[0]]
        for a, b in segs:
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            steps = max(1, int(L * s))
            last_ok = None
            for q in range(steps + 1):
                t = q / steps
                m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                if seg_box_dist((m[0] * s, m[1] * s), (m[0] * s, m[1] * s), head) < gap:
                    if last_ok is not None:
                        out.append(last_ok)
                    return out
                last_ok = m
            out.append(b)
        return out

    # ------------------------------------------------------------------ Z1
    def z1_items(self, p: Plan):
        sc, L = self.sc, self.L
        col = self.L.grid.edges()[0] * self.kx
        top = math.ceil(self.z1[1] / L.grid.unit) * L.grid.unit
        if sc.title and any(b.title for b in sc.beats) and self.pr is None:
            px = L.sizes.title * self.ky
            m = text_metrics(sc.title, self.title_role, px, 1.04)
            x = col - m.ink[0]
            p.title = {"text": sc.title, "x": round(x, 2), "y": top, "px": px, "box": text_box(m, x, top, halo(self.theme, True, px)),
                       "css": (x, top, x + m.adv, top + m.h),
                       "ink": (x + m.ink[0], top + m.ink[1], x + m.ink[2], top + m.ink[3])}
        for b in sc.beats:
            if not b.card:
                continue
            if self.pr is not None:
                self.centre_card(p, b)
                continue
            fy = sc.figures[b.card[0]]
            ytext = self.hf.figure_text(fy, fy.value, self.theme)
            px = L.sizes.year * self.ky
            my = text_metrics(ytext, "serif", px, 1.0, weight=800, nums=True)
            x = col - my.ink[0]
            base = top + my.base
            p.figs[b.card[0]] = {"text": ytext, "kind": "year", "x": round(x, 2), "y": top, "px": px,
                                 "box": text_box(my, x, top, halo(self.theme, False, px)), "base": base,
                                 "css": (x, top, x + my.adv, top + my.h),
                                 "ink": (x + my.ink[0], top + my.ink[1], x + my.ink[2], top + my.ink[3])}
            if len(b.card) > 1:
                ft = sc.figures[b.card[1]]
                ttext = self.hf.figure_text(ft, ft.value, self.theme)
                pt = L.sizes.time * self.ky
                mt = text_metrics(ttext, self.title_role, pt, 1.0, nums=True)
                tx = x + my.ink[2] + L.labels.card_gap * self.kx - mt.ink[0]
                ty = base - mt.base
                p.figs[b.card[1]] = {"text": ttext, "kind": "time", "x": round(tx, 2), "y": round(ty, 2), "px": pt,
                                     "box": text_box(mt, tx, ty, halo(self.theme, False, pt)), "base": base,
                                 "css": (tx, ty, tx + mt.adv, ty + mt.h),
                                     "ink": (tx + mt.ink[0], ty + mt.ink[1], tx + mt.ink[2], ty + mt.ink[3])}

    def centre_card(self, p: Plan, b):
        """[print]: the year alone, centred (ink width year_width x the frame within year_px_min..max), the time
        centred under it in Cinzel caps."""
        pr, sc = self.pr, self.sc
        fy = sc.figures[b.card[0]]
        ytext = self.hf.figure_text(fy, fy.value, self.theme)
        role, w = self.roles["year"][:2]
        ref = text_metrics(ytext, role, 100.0, 1.0, weight=wt(role, w), nums=True)
        px = pr.year_width * self.W / max(1e-6, (ref.ink[2] - ref.ink[0]) / 100)
        px = round(min(pr.year_px_max * self.ky, max(pr.year_px_min * self.ky, px)), 2)
        my = text_metrics(ytext, role, px, 1.0, weight=wt(role, w), nums=True)
        x = round(pr.year_cx * self.kx - (my.ink[0] + my.ink[2]) / 2, 2)
        y = round(pr.year_cy * self.ky - (my.ink[1] + my.ink[3]) / 2, 2)
        ink = (x + my.ink[0], y + my.ink[1], x + my.ink[2], y + my.ink[3])
        p.figs[b.card[0]] = {"text": ytext, "kind": "year", "x": x, "y": y, "px": px, "slot": "centre", "weight": w,
                             "box": text_box(my, x, y, halo(self.theme, False, px)), "base": y + my.base,
                             "css": (x, y, x + my.adv, y + my.h), "ink": ink}
        if len(b.card) > 1:
            ft = sc.figures[b.card[1]]
            ttext = self.hf.figure_text(ft, ft.value, self.theme).upper()
            role_t, wt_t, trk = self.roles["time"][:3]
            pt = pr.time_px * self.ky
            mt = text_metrics(ttext, role_t, pt, 1.0, trk, wt(role_t, wt_t), nums=True)
            tx = round(pr.year_cx * self.kx - (mt.adv - trk * pt) / 2, 2)
            ty = round(ink[3] + pr.time_gap * self.ky - mt.ink[1], 2)
            p.figs[b.card[1]] = {"text": ttext, "kind": "time", "x": tx, "y": ty, "px": pt, "slot": "centre",
                                 "weight": wt_t, "tracking": trk, "box": text_box(mt, tx, ty, halo(self.theme, False, pt)),
                                 "base": ty + mt.base, "css": (tx, ty, tx + mt.adv, ty + mt.h),
                                 "ink": (tx + mt.ink[0], ty + mt.ink[1], tx + mt.ink[2], ty + mt.ink[3])}

    def place_headings(self, p: Plan):
        """[print] date headings: centred caps on the baseline heading_baseline_y, then on each side a gap, a diamond,
        a gap and a rule (rules drawn from the centre outwards)."""
        pr = self.pr
        role, w, trk = self.roles["heading"][:3]
        for k, text in sorted(self.heading_text.items()):
            T = text.upper()
            px = pr.heading_px * self.ky
            m = text_metrics(T, role, px, 1.0, trk, wt(role, w))
            mh = text_metrics("H", role, px, 1.0, 0.0, wt(role, w))
            base = pr.heading_baseline_y * self.ky
            y = round(base - m.base, 2)
            x = round(self.W / 2 - (m.adv - trk * px) / 2, 2)
            ink = (x + m.ink[0], y + m.ink[1], x + m.ink[2], y + m.ink[3])
            rule_y = base - (mh.base - mh.ink[1]) / 2
            g, d, ln = pr.rule_gap * self.kx, pr.diamond_px * self.kx, pr.rule_len * self.kx
            right = (ink[2] + g + d + g, ink[2] + g + d + g + ln)
            left = (ink[0] - g - d - g - ln, ink[0] - g - d - g)
            tb = text_box(m, x, y, halo(self.theme, False, px))
            box = (min(tb[0], left[0]), min(tb[1], rule_y - d / 2), max(tb[2], right[1]), max(tb[3], rule_y + d / 2))
            p.headings[k] = Heading(T, px, x, y, box, (x, y, x + m.adv, y + m.h), base, rule_y, (left, right))

    def place_notes(self, p: Plan):
        """[print] hand annotations: Kalam centred on their map point, rotated; the arrow leaves the rotated text box
        12 px out towards arrow_to and stops 14 px short of it (screen px at the note's entry, then fixed size)."""
        pr = self.pr
        for n, nt in enumerate(self.note_list):
            px = pr.note_px * self.ky
            m = text_metrics(nt.text, "hand", px, 1.0)
            tb = text_box(m, 0, 0, halo(self.theme, False, px))
            w, h = tb[2] - tb[0], tb[3] - tb[1]
            anchor = (nt.at[0] * self.mw, nt.at[1] * self.mh)
            arrow = None
            sp = p.spans.get(f"note:{n}")
            if nt.arrow_to and sp:
                c = p.camera(sp[0].t_in)
                tx = (nt.arrow_to[0] * self.mw - anchor[0]) * c["s"]
                ty = (nt.arrow_to[1] * self.mh - anchor[1]) * c["s"]
                dist = math.hypot(tx, ty)
                ux, uy = (tx / dist, ty / dist) if dist else (0.0, 1.0)
                a = math.radians(-nt.rotation)
                lx, ly = ux * math.cos(a) - uy * math.sin(a), ux * math.sin(a) + uy * math.cos(a)
                t_edge = min((w / 2) / abs(lx) if abs(lx) > 1e-9 else math.inf, (h / 2) / abs(ly) if abs(ly) > 1e-9 else math.inf)
                t0, t1 = t_edge + 12 * self.kx, dist - 14 * self.kx
                if t1 - t0 >= 24 * self.kx:
                    arrow = (round(ux * t0, 2), round(uy * t0, 2), round(ux * t1, 2), round(uy * t1, 2))
                else:
                    self.log.append(f"note '{nt.text}': arrow target too close to the text, arrow dropped")
            p.notes[n] = Note(nt.text, px, w, h, anchor, nt.rotation, arrow)

    # ------------------------------------------------------------------ obstacles of one frame
    def obstacles(self, p: Plan, f: float, skip_pin: int | None = None, labels_upto: int | None = None):
        """(text boxes, head boxes, route polylines, region polygons) visible at frame f in screen px."""
        texts, heads, routes, polys = [], [], [], []
        if p.title and p.visible("title:0", f) > 0:
            texts.append(p.title["box"])
        for i, fg in p.figs.items():
            if p.visible(f"fig:{i}", f) > 0:
                texts.append(fg["box"])
        for k, a in p.areas.items():
            if p.visible(f"area:{k}", f) > 0:
                texts.append(area_box(p, a, f))
        for k, hd in p.headings.items():
            if p.visible(f"heading:{k}", f) > 0:
                texts.append(hd.box)
        for n, nt in p.notes.items():
            if p.visible(f"note:{n}", f) > 0:
                texts.append(note_box(p, nt, f))
        for i in range(len(self.pins)):
            if i != skip_pin and p.visible(f"head:{i}", f) > 0:
                heads.append(head_box(p, i, f))
            if labels_upto is not None and i < labels_upto and p.visible(f"label:{i}", f) > 0:
                lab = p.label_at(i, f)
                if lab is not None:
                    texts.append(label_box(p, i, lab, f))
        for i, r in enumerate(p.routes):
            if p.visible(f"route:{i}", f) > 0:
                routes.append([p.to_screen(f, q) for q in r])
        for i, poly in enumerate(self.regions):
            if p.region_level(i, f) > 0:
                polys.append([p.to_screen(f, q) for q in poly])
        return texts, heads, routes, polys

    def detail(self, p: Plan, f: float, b: Box) -> float:
        ii, (iw, ih) = _detail(str(self.map_path), self.map_path.stat().st_mtime)
        c = p.camera(f)
        sx = (iw / 4) / self.mw
        sy = (ih / 4) / self.mh
        x0 = int(max(0, min(ii.shape[1] - 1, (b[0] - c["x"]) / c["s"] * sx)))
        x1 = int(max(0, min(ii.shape[1] - 1, (b[2] - c["x"]) / c["s"] * sx)))
        y0 = int(max(0, min(ii.shape[0] - 1, (b[1] - c["y"]) / c["s"] * sy)))
        y1 = int(max(0, min(ii.shape[0] - 1, (b[3] - c["y"]) / c["s"] * sy)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float((ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]) / ((x1 - x0) * (y1 - y0)))

    # ------------------------------------------------------------------ area labels
    def place_areas(self, p: Plan):
        L = self.L
        for k, (text, reg) in enumerate(self.area_keys):
            if self.pr is not None:
                self.place_area_print(p, k, text, reg)
                continue
            span = p.spans[f"area:{k}"][0]
            f = span.t_in
            px = L.sizes.area_label * self.ky
            m = text_metrics(text.upper(), "title", px, 1.0, L.sizes.area_tracking)
            tb = text_box(m, 0, 0, halo(self.theme, False, px))
            w, h = tb[2] - tb[0], tb[3] - tb[1]
            poly = [p.to_screen(f, q) for q in self.regions[reg]]
            texts, heads, routes, _ = self.obstacles(p, f)
            inset = L.labels.area_inset * self.kx
            clear = L.zones.group_gap * self.kx    # an area label is its own group: >= group_gap to heads and text
            rclear = max(L.labels.route_clear, L.zones.group_gap) * self.kx
            c = p.camera(f)

            def free(cs) -> bool:
                bb = corners_box(cs)
                if not inside(bb, self.z2):
                    return False
                if any(overlap_area(inflate(bb, clear), t) > 0 for t in texts + heads):
                    return False
                return all(polyline_box_dist(r, bb) >= rclear for r in routes)
            cx = sum(q[0] for q in poly) / len(poly)
            cy = sum(q[1] for q in poly) / len(poly)
            bb = corners_box(poly)
            best = None
            for angle in [0.0, self.long_axis(poly)]:
                cands = []
                step = L.grid.unit
                y = bb[1]
                while y <= bb[3]:
                    x = bb[0]
                    while x <= bb[2]:
                        cands.append((math.hypot(x - cx, y - cy), x, y))
                        x += step
                    y += step
                for _, x, y in sorted(cands):
                    cs = rot_corners(x, y, w + 2 * inset, h + 2 * inset, angle)
                    if quad_inside_poly(cs, poly) and free(rot_corners(x, y, w, h, angle)):
                        best = (x, y, angle, None)
                        break
                if best:
                    break
            inside_ok = best is not None
            if best is None:   # outside the polygon with a leader from its edge
                for _, x, y in sorted((math.hypot(x - cx, y - cy), x, y)
                                      for x in range(int(self.z2[0]), int(self.z2[2]), L.grid.unit)
                                      for y in range(int(self.z2[1]), int(self.z2[3]), L.grid.unit)):
                    cs = rot_corners(x, y, w + 2 * inset, h + 2 * inset, 0.0)
                    if not quad_outside_poly(cs, poly) or not free(rot_corners(x, y, w, h, 0.0)):
                        continue
                    edge = min(((_pt_seg_dist((x, y), a, b2), a, b2) for a, b2 in poly_edges(poly)))
                    _, a, b2 = edge
                    q = _closest(a, b2, (x, y))
                    if math.hypot(q[0] - x, q[1] - y) - min(w, h) / 2 > L.labels.leader_max * self.kx:
                        continue
                    best = (x, y, 0.0, q)
                    break
            if best is None:
                self.log.append(f"area label '{text}' hidden: no position inside or outside region {reg}")
                p.spans[f"area:{k}"] = [Span(s.t_in, 0, s.t_in, 0) for s in p.spans[f"area:{k}"]]
                continue
            x, y, angle, q = best
            anchor = ((x - c["x"]) / c["s"], (y - c["y"]) / c["s"])
            lead = None
            if q is not None:
                lead = ((q[0] - c["x"]) / c["s"], (q[1] - c["y"]) / c["s"])
                self.log.append(f"area label '{text}' outside region {reg} with a leader")
            p.areas[k] = Area(text, reg, px, w, h, anchor, round(angle, 2), lead, inside_ok, k in self.area_water, key=k,
                              tracking=L.sizes.area_tracking)

    def place_area_print(self, p: Plan, k: int, text: str, reg: int):
        """[print] title or place name inside its region: the largest size from title_px down to title_px_min whose
        width is <= title_width_max x the region's screen box, at angle 0 or the long axis clamped to
        +-title_rot_max, nearest the region's pole of inaccessibility; inside with area_inset at the (wider) entry
        tracking, clear of other text, heads and routes."""
        L, pr = self.L, self.pr
        role, w8, trk = self.roles["area"][:3]
        if k in self.area_water:            # water names in the label face (italic on paper)
            role = self.roles["label"][0]
        w8 = wt(role, w8)
        trk_in = trk + (pr.title_tracking_in - pr.title_tracking)
        T = text.upper()
        f = p.spans[f"area:{k}"][0].t_in
        poly = [p.to_screen(f, q) for q in self.regions[reg]]
        bb = corners_box(poly)
        texts, heads, routes, _ = self.obstacles(p, f)
        inset = L.labels.area_inset * self.kx
        clear = L.zones.group_gap * self.kx
        rclear = max(L.labels.route_clear, L.zones.group_gap) * self.kx
        c = p.camera(f)

        def free(cs) -> bool:
            b2 = corners_box(cs)
            if not inside(b2, self.z2):
                return False
            if any(overlap_area(inflate(b2, clear), t) > 0 for t in texts + heads):
                return False
            return all(polyline_box_dist(r2, b2) >= rclear for r2 in routes)
        step = L.grid.unit
        grid_pts = [(x, y) for y in _frange(bb[1], bb[3], step) for x in _frange(bb[0], bb[2], step)
                    if point_in_poly((x, y), poly)]
        if not grid_pts:
            grid_pts = [((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)]
        pole = max(grid_pts, key=lambda q: min(_pt_seg_dist(q, a, b2) for a, b2 in poly_edges(poly)))
        rot = max(-pr.title_rot_max, min(pr.title_rot_max, self.long_axis(poly)))
        angles = [0.0] + ([round(rot, 2)] if abs(rot) >= 0.5 else [])
        cands = sorted(grid_pts, key=lambda q: math.hypot(q[0] - pole[0], q[1] - pole[1]))
        px = pr.title_px * self.ky
        while px >= pr.title_px_min * self.ky - 1e-6:
            m = text_metrics(T, role, px, 1.0, trk, w8)
            if m.adv - trk * px <= pr.title_width_max * (bb[2] - bb[0]):
                tb = text_box(m, 0, 0, halo(self.theme, False, px))
                tbi = text_box(text_metrics(T, role, px, 1.0, trk_in, w8), 0, 0, halo(self.theme, False, px))
                w, h, w_in = tb[2] - tb[0], tb[3] - tb[1], tbi[2] - tbi[0]
                for angle in angles:
                    for x, y in cands:
                        if quad_inside_poly(rot_corners(x, y, w_in + 2 * inset, h + 2 * inset, angle), poly) and \
                                free(rot_corners(x, y, w_in, h, angle)):
                            anchor = ((x - c["x"]) / c["s"], (y - c["y"]) / c["s"])
                            p.areas[k] = Area(text, reg, round(px, 2), w, h, anchor, angle, None, True,
                                              k in self.area_water, key=k, role=role, weight=w8, tracking=trk,
                                              tracking_in=trk_in, title=k in self.area_title)
                            return
            px -= 4 * self.ky
        self.log.append(f"{'title' if k in self.area_title else 'place name'} '{text}' hidden: no position inside "
                        f"region {reg} from {pr.title_px:g} to {pr.title_px_min:g} px")
        p.spans[f"area:{k}"] = [Span(s.t_in, 0, s.t_in, 0) for s in p.spans[f"area:{k}"]]

    @staticmethod
    def long_axis(poly) -> float:
        n = len(poly)
        mx = sum(q[0] for q in poly) / n
        my = sum(q[1] for q in poly) / n
        sxx = sum((q[0] - mx) ** 2 for q in poly)
        syy = sum((q[1] - my) ** 2 for q in poly)
        sxy = sum((q[0] - mx) * (q[1] - my) for q in poly)
        a = math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))
        return max(-45.0, min(45.0, a))

    # ------------------------------------------------------------------ pin labels
    def candidates(self, text: str) -> list[Label]:
        L = self.L
        out = []
        sizes = []
        s = L.sizes.pin_label
        while s >= L.sizes.pin_label_min - 1e-6:
            sizes.append(s)
            s -= L.sizes.pin_label_step
        if self.pr is not None:   # [print]: one size; first choice above the pin on a vertical leader
            sizes = [self.pr.label_px if self.prb["surface"] == "paper" else self.pr.label_dark_px]
            px = sizes[0] * self.ky
            m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
            gap, ll = self.pr.leader_gap * self.ky, self.pr.leader_len * self.ky
            y1 = HEAD[1] - gap
            y2 = y1 - ll
            out.append(Label(round(-m.adv / 2, 2), round(y2 - gap - m.ink[3], 2), px, "A", (0.0, y1, 0.0, y2), -1))
        g = L.labels.offset
        hx0, hy0, hx1, hy1 = HEAD
        hm = HEAD_C[1]
        for si, sz in enumerate(sizes):
            px = sz * self.ky
            m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
            w, h = m.adv, m.h
            pos = {"TR": (hx1 + g, hy0 - h / 2), "BR": (hx1 + g, hy1 - h / 2), "TL": (hx0 - g - w, hy0 - h / 2),
                   "BL": (hx0 - g - w, hy1 - h / 2), "TC": (-w / 2, hy0 - g - h), "BC": (-w / 2, SHADOW[3] + g),
                   "R": (hx1 + g, hm - h / 2), "L": (hx0 - g - w, hm - h / 2)}
            for ri, name in enumerate(L.labels.order):
                dx, dy = pos[name]
                out.append(Label(round(dx, 2), round(dy, 2), px, name, None, ri + si * len(L.labels.order)))
        # leaders at the full size: straight 2 px line from the head edge, label attached at its far end
        px = sizes[0] * self.ky
        m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
        w, h = m.adv, m.h
        base_rank = len(out)
        dirs = [("TR", 1, -1), ("TL", -1, -1), ("BR", 1, 1), ("BL", -1, 1), ("R", 1, 0), ("L", -1, 0), ("T", 0, -1), ("B", 0, 1)]
        for li, length in enumerate(range(48, int(L.labels.leader_max) + 1, 24)):
            for di, (name, ux, uy) in enumerate(dirs):
                nrm = math.hypot(ux, uy)
                ux2, uy2 = ux / nrm, uy / nrm
                sx, sy = HEAD_C[0] + ux2 * 24, HEAD_C[1] + uy2 * 24
                ex, ey = sx + ux2 * length * self.kx, sy + uy2 * length * self.kx
                dx = ex if ux > 0 else (ex - w if ux < 0 else ex - w / 2)
                dy = ey - h / 2 if uy == 0 else (ey - h if uy < 0 else ey)
                out.append(Label(round(dx, 2), round(dy, 2), px, f"leader:{name}:{length}", (sx, sy, ex, ey),
                                 base_rank + li * len(dirs) + di))
        return out

    def label_ok(self, p: Plan, i: int, lab: Label, f: float, obs) -> bool:
        texts, heads, routes, polys = obs
        b = label_box(p, i, lab, f)
        L = self.L
        if not inside(b, self.z2):
            return False
        ec = L.labels.edge_clear * self.kx
        if any(crosses_edge(inflate(b, ec), poly) for poly in polys):
            return False
        tc = L.labels.text_clear * self.kx
        if any(overlap_area(inflate(b, tc), t) > 0 for t in texts + heads):
            return False
        rc = L.labels.route_clear * self.kx
        if any(polyline_box_dist(r, b) < rc for r in routes):
            return False
        if lab.leader is not None:
            P = p.to_screen(f, self.pins[i])
            a = (P[0] + lab.leader[0], P[1] + lab.leader[1])
            z = (P[0] + lab.leader[2], P[1] + lab.leader[3])
            if any(seg_box_dist(a, z, inflate(t, tc / 2)) == 0 for t in texts + heads):
                return False
        return True

    def cost(self, p: Plan, i: int, lab: Label, f: float, prev: Optional[Label]) -> float:
        b = label_box(p, i, lab, f)
        cxp = (b[0] + b[2]) / 2
        cyp = (b[1] + b[3]) / 2
        P = p.to_screen(f, self.pins[i])
        dist = math.hypot(cxp - P[0] - HEAD_C[0], cyp - P[1] - HEAD_C[1])
        return 10 * lab.rank + dist / 16 + 50 * (prev is None or prev.name != lab.name or prev.px != lab.px) + \
            5 * self.detail(p, f, b)

    def place_labels(self, p: Plan):
        T = self.T
        fps = self.fps
        hold = _frames(T.move_hold_s, fps)
        gapf = _frames(T.move_gap_s, fps)
        for i, pin in enumerate(self.sc.pins):
            sp = p.spans.get(f"label:{i}")
            if not sp:
                continue
            cands = self.candidates(pin.label)
            track: list = []
            moves_soft = 0
            last_move = -10 ** 9
            for s in sp:
                end = s.t_out + s.d_out if s.t_out is not None else self.n
                cur: Optional[Label] = None
                pending = None
                obs_at: dict[int, tuple] = {}

                def obs(f):
                    if f not in obs_at:
                        obs_at[f] = self.obstacles(p, f, skip_pin=i, labels_upto=i)
                    return obs_at[f]

                def run(c, f0):
                    """Frames from f0 the candidate stays valid (look-ahead to the span end)."""
                    f = f0
                    while f < end and self.label_ok(p, i, c, f, obs(f)):
                        f += 2
                    return min(f, end) - f0

                def pick(valid, f, prev):
                    if not valid:
                        return None
                    runs = {id(c): run(c, f) for c in valid}
                    longest = max(runs.values())
                    return min((c for c in valid if runs[id(c)] == longest), key=lambda c: self.cost(p, i, c, f, prev))
                for f in range(s.t_in, end):
                    valid = [c for c in cands if self.label_ok(p, i, c, f, obs(f))]
                    if cur is None and f == s.t_in:
                        prev = track[-1][1] if track else None
                        best = pick(valid, f, prev)
                        cur = best
                        track.append((f, best, False))
                        if best is None:
                            self.log.append(f"pin label '{pin.label}' hidden at frame {f}: no valid position")
                        elif best.leader is not None:
                            self.log.append(f"pin label '{pin.label}' uses a leader ({best.name}) from frame {f}")
                        continue
                    if cur is None:
                        continue
                    if not any(c.name == cur.name and c.px == cur.px for c in valid):
                        best = pick(valid, f, cur)
                        track.append((f, best, True))
                        self.log.append(f"pin label '{pin.label}' moved at frame {f} (hard rule): "
                                        f"{cur.name} -> {best.name if best else 'hidden'}")
                        cur, pending, last_move = best, None, f
                        continue
                    if moves_soft >= T.moves_per_shot or f - last_move < gapf:
                        continue
                    cc = self.cost(p, i, cur, f, cur)
                    best = min(valid, key=lambda c: self.cost(p, i, c, f, cur))
                    if best.name != cur.name and self.cost(p, i, best, f, cur) <= (1 - T.move_gain) * cc:
                        if pending is None or pending[0] != best.name:
                            pending = (best.name, f)
                        elif f - pending[1] >= hold:
                            track.append((f, best, False))
                            self.log.append(f"pin label '{pin.label}' moved at frame {f}: {cur.name} -> {best.name}")
                            cur, pending, last_move = best, None, f
                            moves_soft += 1
                    else:
                        pending = None
            p.tracks[i] = track
            for (f, lab, _) in track[1:]:
                p.anim.append((f - _frames(T.move_fade_s, fps) // 2, f + _frames(T.move_fade_s, fps) // 2, f"move:{i}"))


def _closest(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return (a[0] + t * dx, a[1] + t * dy)


# ---------------------------------------------------------------------------------------------------- boxes per frame

def head_box(p: Plan, i: int, f: float) -> Box:
    P = p.to_screen(f, p.pins[i])
    return (P[0] + HEAD[0], P[1] + HEAD[1], P[0] + HEAD[2], P[1] + HEAD[3])


def _label_metrics(p: Plan, i: int, lab: Label) -> Metrics:
    if p.pr is not None:
        role, w = p.pr["roles"]["label"][:2]
        return text_metrics(p.pin_labels[i], role, lab.px, 1.0, weight=wt(role, w))
    role = "script" if p.theme.get("label_font", "script") == "script" else "inter"
    return text_metrics(p.pin_labels[i], role, lab.px, 1.0)


def label_box(p: Plan, i: int, lab: Label, f: float) -> Box:
    P = p.to_screen(f, p.pins[i])
    return text_box(_label_metrics(p, i, lab), P[0] + lab.dx, P[1] + lab.dy, halo(p.theme, False, lab.px, True))


def area_tracking(p: Plan, a: Area, f: float) -> float:
    """Tracking (em) of an area label at frame f: tracking_in easing (easeOutCubic) to tracking over each entry."""
    if a.tracking_in is not None:
        for s in p.spans.get(f"area:{a.key}", []):
            if s.d_in > 0 and s.t_in <= f < s.t_in + s.d_in:
                u = (f - s.t_in) / s.d_in
                return a.tracking_in + (a.tracking - a.tracking_in) * (1 - (1 - u) ** 3)
    return a.tracking


def area_metrics(p: Plan, a: Area, f: float) -> Metrics:
    return text_metrics(a.text.upper(), a.role, a.px, 1.0, round(area_tracking(p, a, f), 4), a.weight)


def area_corners(p: Plan, a: Area, f: float):
    c = p.to_screen(f, a.anchor)
    if a.tracking_in is None or area_tracking(p, a, f) == a.tracking:
        return rot_corners(c[0], c[1], a.w, a.h, a.angle)
    tb = text_box(area_metrics(p, a, f), 0, 0, halo(p.theme, False, a.px))
    return rot_corners(c[0], c[1], tb[2] - tb[0], tb[3] - tb[1], a.angle)


def note_corners(p: Plan, nt: Note, f: float):
    c = p.to_screen(f, nt.anchor)
    return rot_corners(c[0], c[1], nt.w, nt.h, nt.angle)


def note_box(p: Plan, nt: Note, f: float) -> Box:
    return corners_box(note_corners(p, nt, f))


def _frange(a: float, b: float, step: float):
    v = a
    while v <= b:
        yield v
        v += step


def area_box(p: Plan, a: Area, f: float) -> Box:
    return corners_box(area_corners(p, a, f))


# ---------------------------------------------------------------------------------------------------- frame state

@dataclass
class Item:
    key: str
    kind: str                     # title | year | time | label | area | head | route | region | caption | leader
    box: Optional[Box] = None
    px: float = 0.0
    text: str = ""
    opacity: float = 1.0
    pin: Optional[int] = None     # labels and heads: the pin index
    poly: Optional[list] = None   # regions: screen polygon; routes: screen polyline; areas: rotated corners
    level: float = 0.0            # regions: fill level (1 = full)
    seg: Optional[tuple] = None   # leaders: screen segment
    ink: Optional[Box] = None     # Z1 texts: ink box
    region: Optional[int] = None  # areas: their region
    inside: bool = True           # areas: placed inside the polygon
    css: Optional[Box] = None     # texts and heads: CSS box as the browser lays it out (DOM check); areas: unrotated
    base: Optional[float] = None  # Z1 texts: baseline y
    angle: float = 0.0            # areas: rotation (deg)
    slot: str = "z1"              # title / year / time: "z1" (grid) or "centre" ([print] year alone, headings)


def frame_items(p: Plan, f: float) -> list[Item]:
    out: list[Item] = []
    if p.title and (o := p.visible("title:0", f)) > 0:
        out.append(Item("title:0", "title", p.title["box"], p.title["px"], p.title["text"], o, ink=p.title["ink"],
                        css=p.title["css"]))
    for i, fg in p.figs.items():
        if (o := p.visible(f"fig:{i}", f)) > 0:
            out.append(Item(f"fig:{i}", fg["kind"], fg["box"], fg["px"], fg["text"], o, ink=fg["ink"], css=fg["css"],
                            base=fg["base"], slot=fg.get("slot", "z1")))
    for k, hd in p.headings.items():
        if (o := p.visible(f"heading:{k}", f)) > 0:
            out.append(Item(f"heading:{k}", "heading", hd.box, hd.px, hd.text, o, css=hd.css, base=hd.base,
                            slot="centre"))
    for n, nt in p.notes.items():
        if (o := p.visible(f"note:{n}", f)) > 0:
            cs = note_corners(p, nt, f)
            c = p.to_screen(f, nt.anchor)
            m = text_metrics(nt.text, "hand", nt.px, 1.0)
            out.append(Item(f"note:{n}", "note", corners_box(cs), nt.px, nt.text, o, poly=cs, angle=nt.angle,
                            css=(c[0] - m.adv / 2, c[1] - m.h / 2, c[0] + m.adv / 2, c[1] + m.h / 2), slot="map"))
            if nt.arrow:
                a = nt.arrow
                out.append(Item(f"leader:note:{n}", "leader", None, 0, "", o,
                                seg=((c[0] + a[0], c[1] + a[1]), (c[0] + a[2], c[1] + a[3]))))
    for k, a in p.areas.items():
        if (o := p.visible(f"area:{k}", f)) > 0:
            cs = area_corners(p, a, f)
            m = area_metrics(p, a, f)
            c = p.to_screen(f, a.anchor)
            out.append(Item(f"area:{k}", "area", corners_box(cs), a.px, a.text, o, poly=cs, region=a.region,
                            inside=a.inside, css=(c[0] - m.adv / 2, c[1] - m.h / 2, c[0] + m.adv / 2, c[1] + m.h / 2),
                            angle=a.angle))
            if a.leader:
                c = p.to_screen(f, a.anchor)
                out.append(Item(f"leader:area:{k}", "leader", None, 0, "", o, seg=(p.to_screen(f, a.leader), c)))
    for i in range(len(p.pins)):
        if (o := p.visible(f"head:{i}", f)) > 0:
            hb = head_box(p, i, f)
            out.append(Item(f"head:{i}", "head", hb, 0, "", o, pin=i, css=hb))
        if (o := p.visible(f"label:{i}", f)) > 0 and (lab := p.label_at(i, f)) is not None:
            P = p.to_screen(f, p.pins[i])
            m = _label_metrics(p, i, lab)
            out.append(Item(f"label:{i}", "label", label_box(p, i, lab, f), lab.px, p.pin_labels[i], o, pin=i,
                            css=(P[0] + lab.dx, P[1] + lab.dy, P[0] + lab.dx + m.adv, P[1] + lab.dy + m.h)))
            if lab.leader:
                P = p.to_screen(f, p.pins[i])
                out.append(Item(f"leader:{i}", "leader", None, 0, "", o, pin=i,
                                seg=((P[0] + lab.leader[0], P[1] + lab.leader[1]), (P[0] + lab.leader[2], P[1] + lab.leader[3]))))
    for i, r in enumerate(p.routes):
        if (o := p.visible(f"route:{i}", f)) > 0:
            out.append(Item(f"route:{i}", "route", None, 0, "", o, poly=[p.to_screen(f, q) for q in r]))
    for i, poly in enumerate(p.regions):
        if (lv := p.region_level(i, f)) > 0:
            out.append(Item(f"region:{i}", "region", None, 0, "", 1.0, poly=[p.to_screen(f, q) for q in poly], level=lv))
    return out


def animating(p: Plan, f: float) -> list[str]:
    return [k for (a, z, k) in p.anim if a <= f < z]


# ---------------------------------------------------------------------------------------------------- write-back

def apply_segment(seg: "Segment", tl: "Timeline", project_dir: Path) -> Plan | None:
    """Writes the derived camera_path and element frames into a beats segment (idempotent). None = not a beats
    segment under a layout profile, or its map is missing."""
    if not is_beats(seg, tl) or not (Path(project_dir) / seg.gsap_scene_config.map_asset).is_file():
        return None
    from .timeline import CameraKey
    p = plan(seg, tl, project_dir)
    sc = seg.gsap_scene_config
    sc.camera_path = [CameraKey(**{k: v for k, v in kk.items() if not (k == "move_s" and kk["move"] != "settle")})
                      for kk in p.keys]
    first = lambda key, default: (p.spans.get(key) or [Span(default, 0)])[0].t_in
    for i, pin in enumerate(sc.pins):
        pin.at_frame = min(seg.n_frames - 1, first(f"head:{i}", seg.n_frames - 1))
    for i, r in enumerate(sc.regions or []):
        r.at_frame = min((t for (t, _, _, _) in p.region_steps[i]), default=seg.n_frames - 1)
    for i, r in enumerate(sc.routes or []):
        r.at_frame = first(f"route:{i}", seg.n_frames - 1)
    if sc.title:
        sc.title_at_frame = first("title:0", seg.n_frames - 1)
    for i, fg in enumerate(sc.figures or []):
        fg.at_frame = first(f"fig:{i}", seg.n_frames - 1)
    return p


def apply(tl: "Timeline", project_dir: Path) -> None:
    for s in tl.segments:
        apply_segment(s, tl, project_dir)


def scene_layout(p: Plan) -> dict:
    """The `layout` block of the scene data (seconds, output px; map px for anchors)."""
    fps = p.fps
    t = lambda f: round(f / fps, 4)
    els = {k: [[t(s.t_in), t(s.d_in), None if s.t_out is None else t(s.t_out), t(s.d_out)] for s in v]
           for k, v in p.spans.items()}
    out = {"els": els, "dim": p.L.labels.dim_alpha,
           "regions": [[[t(a), t(d), x, y] for (a, d, x, y) in st] for st in p.region_steps],
           "move_fade_s": p.L.timing.move_fade_s, "leader_px": p.L.labels.leader_px}
    if p.L.fly is not None:
        out["fly"] = p.L.fly.model_dump()
    if p.title:
        out["title"] = {"x": p.title["x"], "y": p.title["y"], "px": p.title["px"]}
    out["figures"] = {str(i): {"x": fg["x"], "y": fg["y"], "px": fg["px"]} for i, fg in p.figs.items()}
    out["labels"] = {str(i): [[t(f), None if lab is None else
                               {"x": lab.dx, "y": lab.dy, "px": lab.px, "leader": lab.leader}, hard]
                              for (f, lab, hard) in tr] for i, tr in p.tracks.items()}
    out["areas"] = [{"key": k, "text": a.text.upper(), "px": a.px, "tracking": a.tracking,
                     "anchor": [round(a.anchor[0], 2), round(a.anchor[1], 2)], "angle": a.angle,
                     "leader": None if a.leader is None else [round(a.leader[0], 2), round(a.leader[1], 2)],
                     "region": a.region, **({"water": True} if a.water else {}),
                     **({"role": a.role, "weight": a.weight, "tracking_in": a.tracking_in, "title": a.title}
                        if p.pr is not None else {})}
                    for k, a in p.areas.items()]
    if p.pr is not None:
        for i, fg in p.figs.items():
            out["figures"][str(i)].update({k: fg[k] for k in ("slot", "weight", "tracking") if k in fg})
        out["headings"] = [{"key": k, "text": h.text, "x": h.x, "y": h.y, "px": h.px, "rule_y": round(h.rule_y, 2),
                            "rules": [[round(v, 2) for v in r] for r in h.rules]} for k, h in p.headings.items()]
        out["notes"] = [{"key": n, "text": nt.text, "px": nt.px, "angle": nt.angle,
                         "anchor": [round(nt.anchor[0], 2), round(nt.anchor[1], 2)], "arrow": nt.arrow}
                        for n, nt in p.notes.items()]
    return out
