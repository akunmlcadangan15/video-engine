"""Pydantic layout profile models and dataclass state containers for beats layout."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .layout_geometry import Box
from .units import Units

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
    z3: tuple[float, float, float, float] | None = None  # optional reference zone; not read by the planner or gates
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
    box: Optional[bool] = None      # None = profile label_style; True/False = solid box label on/off


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
    """[print] date heading: Cinzel caps centred at a baseline. Ornaments (a rule and a diamond on each side)
    are drawn only when `ornaments` is set; the plain form is the text alone."""
    text: str
    px: float
    x: float                        # CSS box left / top
    y: float
    box: Box                        # painted box: text ink, plus rules and diamonds when ornaments are on
    css: Box
    base: float
    rule_y: float                   # rule centre line y (unused when ornaments are off)
    rules: tuple                    # (x0, x1) of the left rule, (x0, x1) of the right rule; () when plain
    ornaments: bool = False


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
        from .layout_camera import camera_at
        return camera_at(self.cam, f)

    def to_screen(self, f: float, p) -> tuple[float, float]:
        c = self.camera(f)
        return (c["x"] + c["s"] * p[0], c["y"] + c["s"] * p[1])

    def k(self) -> tuple[float, float]:
        u = Units.of(self.W, self.H)   # reference canvas of the orientation (units.py)
        return u.kx, u.ky

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
