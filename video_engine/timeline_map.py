"""Timeline 2D camera, map overlay, route, flag, prop, and beat models."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator

from .grade import Grade
from .timeline_common import HEX_RE, Strict
from .util import EngineError

class Hatch(Strict):
    """Diagonal SVG pattern inside a region polygon (P17); values default from profile [fx.hatch]."""
    angle: Optional[float] = Field(None, ge=0, le=180)
    spacing_px: Optional[float] = Field(None, ge=2, le=100)
    line_px: Optional[float] = Field(None, ge=1, le=20)
    colour: Optional[str] = Field(None, pattern=HEX_RE)
    alpha: Optional[float] = Field(None, ge=0, le=1)


class CameraMove(Strict):
    preset: str = "ken_burns_zoom_in"
    intensity: float = Field(1.0, ge=0.0, le=6.0)
    focus_box: Optional[list[float]] = Field(None, min_length=4, max_length=4)   # [ymin, xmin, ymax, xmax] 0..1000 (Gemini box order)
    auto_intensity: bool = Field(False, strict=True)   # creative mutation requires explicit opt-in

    @model_validator(mode="after")
    def _focus(self):
        b = self.focus_box
        if b is not None:
            if not all(math.isfinite(v) and 0 <= v <= 1000 for v in b):
                raise ValueError(f"focus_box {b}: values must be finite and within 0..1000")
            if not (b[0] < b[2] and b[1] < b[3]):
                raise ValueError(f"focus_box {b}: need [ymin, xmin, ymax, xmax] with ymin < ymax and xmin < xmax")
        return self


class Flag(Strict):
    code: str
    fit: Literal["auto", "cover", "contain", "stretch"] | None = None
    mask: Literal["polygon"] = "polygon"
    alpha: float | None = Field(None, ge=0, le=1)
    reveal_s: float | None = Field(None, ge=0.2, le=1.0)
    edge_px: float | None = Field(None, ge=2, le=7)
    glow_px: float | None = Field(None, ge=3, le=14)
    variant: Literal["4x3", "1x1"] = "4x3"

    @model_validator(mode="before")
    @classmethod
    def _input(cls, value):
        if isinstance(value, str):
            value = {"code": value}
        if isinstance(value, dict) and isinstance(value.get("code"), str):
            value = {**value, "code": value["code"].lower()}
            from .assets import flag_for
            try:
                flag_for(value["code"], value.get("variant", "4x3"))
            except EngineError as exc:
                if "closest:" in str(exc):
                    raise
                raise EngineError(f"{exc}; closest: none") from exc
            if len(value["code"]) != 2:
                raise EngineError(f"flag code {value['code']} must be ISO alpha-2; closest: none")
        return value


class Pin(Strict):
    label: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    x: Optional[float] = Field(None, ge=0, le=1)   # normalised map position (alternative to lat/lng)
    y: Optional[float] = Field(None, ge=0, le=1)
    at_frame: Optional[int] = Field(None, ge=0)     # relative to segment start
    label_side: Optional[Literal["left", "right"]] = None   # teardrop pins: label beside the head (None = right)
    box: Optional[bool] = None                      # None = profile label_style; True/False = solid box on/off
    flag: Flag | None = Field(None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _pos(self):
        if (self.lat is None or self.lng is None) and (self.x is None or self.y is None):
            raise ValueError(f"pin '{self.label}' needs lat+lng or x+y")
        return self


class Card(Strict):
    title: str
    subtitle: Optional[str] = None
    asset_ref: Optional[str] = None   # credits asset_id of an image shown in the card
    entity: Optional[str] = Field(None, min_length=1, max_length=60)   # theme.label_style "box": box in this entity's colour
    at_frame: Optional[int] = Field(None, ge=0)
    position: Literal["top", "bottom"] = "top"


class RegionChange(Strict):
    """Territory changing hands: from `at_frame` (relative to the segment start) the region takes `entity`'s colour,
    crossfading over theme.block_change_s."""
    at_frame: int = Field(ge=0)
    entity: str = Field(min_length=1, max_length=60)
    flag: Flag | None = Field(None, exclude_if=lambda value: value is None)


class Region(Strict):
    """Highlighted map area of one entity (country, army, kingdom...), appearing at `at_frame` (relative to the segment
    start). style "tint": semi-transparent fill in the entity colour with a white or same-colour glow edge. style
    "block": a raised slab in the saturated entity colour (entities.block_colour) with an extruded side, a soft drop
    shadow, an inner bevel and a neon edge that draws itself on. Globe scenes: polygon points are normalised
    [x, y] of the whole-world texture (x = (lng + 180) / 360, y = (90 - lat) / 180)."""
    entity: str = Field(min_length=1, max_length=60)
    flag: Flag | None = Field(None, exclude_if=lambda value: value is None)
    polygon: list[list[float]] = Field(min_length=3)   # normalised [x, y] map points (the main outline)
    rings: list[list[list[float]]] = Field([], exclude_if=lambda v: not v)   # further rings drawn with the polygon
                                                       # under the even-odd rule: other islands (outside it) and holes
                                                       # (inside it); one fill, edge and flag domain for all of them
    at_frame: int = Field(0, ge=0)
    edge: Optional[Literal["white", "glow"]] = None    # None = profile theme.region_edge
    style: Optional[Literal["tint", "block"]] = None   # None = profile theme.region_style
    edge_colour: Optional[str] = Field(None, pattern=HEX_RE)   # block edge (None = theme.block_edge_colour)
    dashed: bool = False                               # block edge dashed (sea border)
    draw_s: Optional[float] = Field(None, ge=0, le=8.0)   # block edge draw-on (None = theme.block_draw_s; 0 = at once)
    label: Optional[str] = Field(None, min_length=1, max_length=40)   # box label in the region colour
    label_at: Optional[list[float]] = Field(None, min_length=2, max_length=2)   # normalised map point (None = centroid)
    label_at_frame: Optional[int] = Field(None, ge=0)  # None = at_frame
    box: Optional[bool] = None                         # None = profile label_style; True/False = solid label box
    changes: list[RegionChange] = []                   # later owners, in frame order
    hatch: Optional[Hatch] = None                      # diagonal pattern over the fill (P17)

    @model_validator(mode="after")
    def _poly(self):
        for k, ring in enumerate(self.rings):
            if len(ring) < 3:
                raise ValueError(f"region '{self.entity}': rings[{k}] needs at least 3 points")
        for pt in [*self.polygon, *(p for ring in self.rings for p in ring), *([self.label_at] if self.label_at else [])]:
            if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
                raise ValueError(f"region '{self.entity}': point {pt} must be [x, y] within 0..1")
        prev = self.at_frame
        for c in self.changes:
            if c.at_frame <= prev:
                raise ValueError(f"region '{self.entity}': change at_frame {c.at_frame} must be > {prev}")
            prev = c.at_frame
        if self.label is None and (self.label_at is not None or self.label_at_frame is not None):
            raise ValueError(f"region '{self.entity}': label_at and label_at_frame need a label")
        return self

    def all_rings(self) -> list[list[list[float]]]:
        """The polygon then every further ring (even-odd: islands and holes)."""
        return [self.polygon, *self.rings]


class Border(Strict):
    """A border line on the map (land, or sea when `dashed`) that draws itself along `points` from `at_frame` (relative
    to the segment start) over `draw_s` seconds, in `colour` with a neon glow; `closed` joins the last point to the
    first. Width, glow and dashes keep their on-screen size while the camera zooms, unless `grow` > 0 (a river, a
    coastline): then they scale as zoom^grow (1 = drawn on the map like ink, 0 = constant on screen)."""
    points: list[list[float]] = Field(min_length=2)    # normalised [x, y] map points
    at_frame: int = Field(0, ge=0)
    draw_s: Optional[float] = Field(None, ge=0, le=8.0)   # None = profile theme.border_draw_s; 0 = at once
    colour: str = Field("#ffffff", pattern=HEX_RE)
    dashed: bool = False
    closed: bool = False
    width_px: Optional[float] = Field(None, gt=0, le=60, exclude_if=lambda v: v is None)   # on-screen width at zoom 1
                                                                                         # (None = theme border_px)
    glow_px: Optional[float] = Field(None, ge=0, le=80, exclude_if=lambda v: v is None)    # glow radius at zoom 1
                                                                                         # (None = theme block_glow_px)
    grow: float = Field(0.0, ge=0.0, le=1.5, exclude_if=lambda v: v == 0.0)              # width ~ zoom^grow
    casing: bool = Field(True, exclude_if=lambda v: v)   # dark casing under the line (theme block_casing_px); False =
                                                         # glow only (a lit river on satellite)

    @model_validator(mode="after")
    def _pts(self):
        for pt in self.points:
            if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
                raise ValueError(f"border at_frame {self.at_frame}: point {pt} must be [x, y] within 0..1")
        return self


class MapMorph(Strict):
    """A second map texture crossfaded over the base map without a cut (satellite <-> old paper): `asset` covers the
    extent and projection of map_asset (same aspect ratio). It fades in from `at_frame` (relative to the segment
    start; 0 = shown from the first frame) over `s` seconds and out again from `back_at_frame` (None = stays), toned
    by `sepia`, with an aged-paper texture multiplied over it at `paper` and screen grain at `grain` while it shows."""
    asset: str = Field(min_length=1)
    asset_id: Optional[str] = None
    at_frame: int = Field(0, ge=0)
    s: float = Field(1.2, ge=0.1, le=6.0)
    back_at_frame: Optional[int] = Field(None, ge=0)
    sepia: float = Field(0.5, ge=0, le=1)
    paper: float = Field(0.5, ge=0, le=1)
    grain: float = Field(0.0, ge=0, le=0.5)

    @model_validator(mode="after")
    def _back(self):
        if self.back_at_frame is not None and self.back_at_frame <= self.at_frame:
            raise ValueError(f"morph back_at_frame {self.back_at_frame} must be > at_frame {self.at_frame}")
        return self


class MapText(Strict):
    """A name written on the map along `points` (normalised map points, left to right): letters appear one after the
    other over `reveal_s` from `at_frame`, in `colour` over `depth_px` of darker extrusion and a soft shadow, and fade
    out over 0.3 s from `out_frame` (None = shown to the segment end). The text lies on the map: it pans, zooms, tilts
    and rolls with it (`px` is its size at zoom 1)."""
    text: str = Field(min_length=1, max_length=40)
    points: list[list[float]] = Field(min_length=2)
    at_frame: int = Field(0, ge=0)
    out_frame: Optional[int] = Field(None, ge=1)
    reveal_s: float = Field(0.6, ge=0.0, le=4.0)
    px: float = Field(90.0, ge=8, le=400)
    colour: str = Field("#7fae46", pattern=HEX_RE)
    depth_px: float = Field(8.0, ge=0, le=40)

    @model_validator(mode="after")
    def _mt(self):
        for pt in self.points:
            if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
                raise ValueError(f"map text '{self.text}': point {pt} must be [x, y] within 0..1")
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"map text '{self.text}': out_frame must be after at_frame")
        return self


class Route(Strict):
    """Movement on the map (army, ship, flight...): a dashed line that draws itself along `points` from `at_frame`
    (relative to the segment start) over `draw_s` seconds, with an arrowhead riding the drawing tip."""
    points: list[list[float]] = Field(min_length=2)    # normalised [x, y] map points, start to end
    at_frame: int = Field(0, ge=0)
    draw_s: Optional[float] = Field(None, ge=0.2, le=8.0)   # None = profile theme.route_draw_s
    to_pin: Optional[int] = Field(None, ge=0)          # beats scenes: the route ends layout route_pin_gap_px short of this pin

    @model_validator(mode="after")
    def _pts(self):
        for pt in self.points:
            if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
                raise ValueError(f"route at_frame {self.at_frame}: point {pt} must be [x, y] within 0..1")
        if all(pt == self.points[0] for pt in self.points):
            raise ValueError(f"route at_frame {self.at_frame}: points must not all be the same")
        return self


class Figure(Strict):
    """A big year or number over the map at a screen position, from `at_frame` (relative to the segment start) to the
    segment end. motion "bounce": elastic scale-in; "counter": counts from `count_from` to `value` over `count_s`."""
    kind: Literal["year", "number"]
    value: float
    decimals: int = Field(0, ge=0, le=3)          # numbers only
    prefix: str = Field("", max_length=12)
    suffix: str = Field("", max_length=12)
    motion: Literal["bounce", "counter"] = "bounce"
    count_from: float = 0.0
    count_s: Optional[float] = Field(None, ge=0.3, le=6.0)   # None = profile theme.counter_s
    at_frame: int = Field(0, ge=0)
    position: list[float] = Field([0.5, 0.3], min_length=2, max_length=2)   # normalised [x, y] of the frame (centre)
    entity: Optional[str] = Field(None, min_length=1, max_length=60)        # colour = entity colour (default white)
    box: Optional[bool] = None                                              # None = profile label_style; True/False
    px: Optional[float] = Field(None, ge=16, le=400, exclude_if=lambda v: v is None)   # size (None = theme figure_px)
    anchor: Literal["screen", "map"] = Field("screen", exclude_if=lambda v: v == "screen")   # map: `position` is a
                                                  # normalised map point; the figure rides the camera (pan, tilt,
                                                  # roll) at a constant on-screen size, like a label on the map

    @model_validator(mode="after")
    def _fig(self):
        if not all(0.0 <= v <= 1.0 for v in self.position):
            raise ValueError(f"figure {self.value:g}: position {self.position} must be within 0..1")
        if self.kind == "year" and (self.value != int(self.value) or not 1 <= self.value <= 9999 or self.decimals):
            raise ValueError(f"figure year {self.value:g}: a year is a whole number 1-9999 without decimals")
        if self.motion == "bounce" and (self.count_from or self.count_s is not None):
            raise ValueError(f"figure {self.value:g}: count_from and count_s apply to motion 'counter' only")
        return self


ZOOM_MAX = 4.0     # default profile [camera] max_zoom: largest camera zoom over the cover fit (CameraKey.zoom, layout fit)
ZOOM_LIMIT = 64.0  # largest [camera] max_zoom any profile may set
TILT_MAX = 60.0    # largest camera tilt (deg)
ROLL_MAX = 45.0    # largest camera roll either way (deg)


class CameraKey(Strict):
    """One view of a HyperFrames camera path: the camera reaches it at `at_frame` (relative to the segment start)."""
    at_frame: int = Field(ge=0)
    focus: list[float] = Field(min_length=2, max_length=2)   # normalised [x, y] map point at the frame centre
    zoom: float = Field(1.0, ge=1.0, le=ZOOM_LIMIT)          # scale over the cover fit, <= profile [camera] max_zoom
    move: Literal["glide", "whip", "settle", "reveal", "flow"] = "glide"   # move from the previous key into this one;
                                                  # "flow": one smooth spline through every consecutive flow key
    move_s: Optional[float] = Field(None, ge=0.1, le=3.0)    # "settle" length (None = theme whip_s)
    pulse: Optional[float | list[float]] = None   # ln zoom of the pulses while the camera waits on this view:
                                                  # one size or one per away pulse, last repeats (None = profile
                                                  # [camera] pulse_zoom, 0 = drift only)
    drift_max: Optional[float] = Field(None, ge=0.01, le=2.0)   # ln zoom bound of the drift while the camera waits
                                                                # on this view: it turns at 0 and drift_max (None =
                                                                # pushes in for the whole wait)
    fit: Optional[list] = Field(None, validation_alias=AliasChoices("fit", "keep_in_frame"))
                                                  # pin labels or indexes that must stay inside the safe area (with
                                                  # margin) for the whole hold on this view; the push-in and pulses
                                                  # of that hold are limited so they do. `keep_in_frame` is the same
                                                  # field. Empty is rejected. Does not reframe the key itself.
    tilt: float = Field(0.0, ge=0.0, le=TILT_MAX)   # perspective tilt (deg) of the map plane at this view (camera3d)
    roll: float = Field(0.0, ge=-ROLL_MAX, le=ROLL_MAX, exclude_if=lambda v: v == 0.0)   # 2D roll (deg, clockwise)
                                                    # of the map about the frame centre; the map is scaled by the
                                                    # angle's cover factor so it still fills every corner
    hold: int = Field(0, ge=0, le=90)               # frames the camera stays still on this view after at_frame

    @model_validator(mode="after")
    def _focus(self):
        if not all(0.0 <= v <= 1.0 for v in self.focus):
            raise ValueError(f"camera key at_frame {self.at_frame}: focus {self.focus} must be within 0..1")
        if self.move_s is not None and self.move != "settle":
            raise ValueError(f"camera key at_frame {self.at_frame}: move_s applies to move 'settle' only")
        sizes = self.pulse if isinstance(self.pulse, list) else [] if self.pulse is None else [self.pulse]
        if (isinstance(self.pulse, list) and not sizes) or not all(0.0 <= v <= 1.0 for v in sizes):
            raise ValueError(f"camera key at_frame {self.at_frame}: pulse {self.pulse} must be sizes within 0..1")
        if self.fit is not None:
            if not self.fit:
                raise ValueError(f"camera key at_frame {self.at_frame}: fit needs at least one pin")
            for item in self.fit:
                if isinstance(item, bool) or not isinstance(item, (str, int)):
                    raise ValueError(f"camera key at_frame {self.at_frame}: fit entries are pin labels or indexes, "
                                     f"not {item!r}")
                if isinstance(item, str) and not item.strip():
                    raise ValueError(f"camera key at_frame {self.at_frame}: fit label is blank")
                if isinstance(item, int) and item < 0:
                    raise ValueError(f"camera key at_frame {self.at_frame}: fit index {item} is negative")
        return self


class AreaLabel(Strict):
    """Name of a region, set inside its polygon by the layout (beats scenes)."""
    text: str = Field(min_length=1, max_length=40)
    region: int = Field(ge=0)                        # index into gsap_scene_config.regions
    water: bool = False                              # a sea, strait or river name: blue italic (legibility profiles)
    box: Optional[bool] = None                       # None = profile label_style; True/False = solid box on/off


class Note(Strict):
    """Hand annotation of a beat (profile [print]): short handwritten text at a map point with an optional arrow to
    another map point; written on over print.note_write_s."""
    text: str = Field(min_length=1, max_length=40)
    at: list[float] = Field(min_length=2, max_length=2)          # normalised [x, y] map point under the text centre
    arrow_to: Optional[list[float]] = Field(None, min_length=2, max_length=2)   # normalised map point of the arrow tip
    rotation: float = Field(-6.0, ge=-8.0, le=-4.0)               # deg

    @model_validator(mode="after")
    def _pts(self):
        for pt in [self.at] + ([self.arrow_to] if self.arrow_to else []):
            if not all(0.0 <= v <= 1.0 for v in pt):
                raise ValueError(f"note '{self.text}': point {pt} must be within 0..1")
        return self


PROP_EXT = {".png", ".webp"}
MODEL_EXT = {".glb", ".gltf", ".obj"}  # 3D model props (video_engine/models.py renders them to sprites)
PROP_DEPTH_MAX = 2.0   # largest Prop.depth


class PropMotion(Strict):
    """Enter or exit animation of a prop. fade: opacity; pop: size from 0 (enter) or to 0 (exit); slide: moves
    `distance_px` frame px along `dir` with a fade. `ease` is a camera ease name (layout.EASES)."""
    kind: Literal["fade", "pop", "slide"]
    s: float = Field(0.4, ge=0.05, le=3.0)                 # length in seconds
    ease: Literal["glide", "drift", "whip"] = "glide"
    dir: Optional[Literal["left", "right", "up", "down"]] = None   # slide only (None = "up")
    distance_px: Optional[float] = Field(None, gt=0, le=600)        # slide only (None = 80)

    @model_validator(mode="after")
    def _slide(self):
        if self.kind != "slide" and (self.dir is not None or self.distance_px is not None):
            raise ValueError(f"prop {self.kind}: dir and distance_px apply to kind 'slide' only")
        return self


class PropIdle(Strict):
    """Looping motion of a visible prop: bob = vertical sine of `amplitude` frame px; rotate = rotation sine of
    `amplitude` degrees; one cycle per `period_s` seconds, phase 0 at the prop's first frame."""
    kind: Literal["bob", "rotate"]
    amplitude: float = Field(gt=0, le=200)
    period_s: float = Field(ge=0.2, le=30.0)


class PropShadow(Strict):
    """Drop shadow under a prop, in frame px (black at `alpha`)."""
    dx: float = Field(0.0, ge=-60, le=60)
    dy: float = Field(8.0, ge=-60, le=60)
    blur: float = Field(10.0, ge=0, le=60)
    alpha: float = Field(0.45, gt=0, le=1.0)


class PropModel(Strict):
    """How a 3D model prop (asset .glb, .gltf or .obj) is rendered to its sprite (video_engine/models.py): the model
    turned `yaw` degrees about its vertical axis, seen from `pitch` degrees above the horizon, orthographic (`fov` 0) or
    perspective of `fov` degrees, into a square sprite of `size_px` px. `turntable_s`: the model turns `turn_deg`
    degrees every `turntable_s` seconds, looping (None = a still)."""
    yaw: float = Field(30.0, ge=-360, le=360)
    pitch: float = Field(20.0, ge=-89, le=89)
    fov: float = Field(0.0, ge=0, le=90)
    size_px: int = Field(512, ge=64, le=2048)
    turntable_s: Optional[float] = Field(None, ge=0.5, le=60.0)
    turn_deg: float = Field(360.0, ge=-360, le=360)


class Prop(Strict):
    """A free-positioned image (PNG or WebP with alpha, or a 3D model rendered to one, see PropModel) over a HyperFrames map segment, from `t_in` to `t_out`
    (frames, relative to the segment start, or to the beat start for beat props; `t_out` exclusive).
    anchor "map": the centre sits on map point (`x`, `y`) normalised 0..1 or (`lat`, `lng`) via map_bounds and moves
    with the camera; `depth` > 0 lifts the prop towards the camera (props.py: scale and parallax). anchor "screen": the
    centre sits at frame px (`x`, `y`) and ignores the camera. Size: `width_px` frame px (map props: at the prop's first
    frame) or `width_map` (map props: fraction of the map width)."""
    asset: str = Field(min_length=1)                 # project-relative PNG or WebP with alpha, or a .glb/.gltf/.obj model
    model: Optional[PropModel] = None                # model assets: the render (None = PropModel defaults)
    asset_id: Optional[str] = None                   # credits manifest id (None = matched by path)
    anchor: Literal["map", "screen"] = "map"
    x: Optional[float] = None
    y: Optional[float] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    width_px: Optional[float] = Field(None, gt=0, le=4000)
    width_map: Optional[float] = Field(None, gt=0, le=1.0)
    rotation: float = Field(0.0, ge=-360, le=360)    # degrees, clockwise
    opacity: float = Field(1.0, gt=0, le=1.0)
    t_in: int = Field(0, ge=0)
    t_out: Optional[int] = Field(None, gt=0)         # None = segment end (beat props: next beat or segment end)
    enter: Optional[PropMotion] = None
    exit: Optional[PropMotion] = None
    idle: Optional[PropIdle] = None
    depth: float = Field(0.0, ge=0.0, le=PROP_DEPTH_MAX)   # 0 = on the map plane; map props only
    z: int = Field(0, ge=-100, le=100)               # stacking among props of the same anchor (higher on top)
    shadow: Optional[PropShadow] = None
    offscreen: bool = Field(False, exclude_if=lambda v: not v)   # map props: the camera may carry it past the frame
                                                                 # edge (gate F04 skips it); its entry stays inside
    path: Optional[list[list[float]]] = Field(None, min_length=2)   # map props: normalised map points the centre
                                                                     # travels along from t_in (replaces x/y)
    path_s: Optional[float] = Field(None, gt=0, le=120)  # travel time (None = t_in to t_out)
    heading: bool = False                            # path props: rotation follows the path direction (+ rotation)

    @model_validator(mode="after")
    def _prop(self):
        tag = f"prop '{self.asset}'"
        ext = Path(self.asset).suffix.lower()
        if ext in MODEL_EXT:
            if self.model is None:
                self.model = PropModel()
        elif ext not in PROP_EXT:
            raise ValueError(f"{tag}: asset must be a .png or .webp image with alpha, or a .glb, .gltf or .obj model")
        elif self.model is not None:
            raise ValueError(f"{tag}: model applies to .glb, .gltf and .obj assets only")
        if (self.width_px is None) == (self.width_map is None):
            raise ValueError(f"{tag}: set exactly one of width_px and width_map")
        geo = self.lat is not None or self.lng is not None
        xy = self.x is not None or self.y is not None
        if self.anchor == "screen":
            if geo or self.x is None or self.y is None:
                raise ValueError(f"{tag}: anchor 'screen' needs x and y in frame px (no lat/lng)")
            if self.width_map is not None or self.depth or self.path is not None:
                raise ValueError(f"{tag}: width_map, depth and path apply to anchor 'map' only")
        elif self.path is not None:
            if geo or xy:
                raise ValueError(f"{tag}: a path replaces x, y, lat and lng")
            if not all(len(pt) == 2 and 0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0 for pt in self.path):
                raise ValueError(f"{tag}: path points must be [x, y] within 0..1")
            if all(pt == self.path[0] for pt in self.path):
                raise ValueError(f"{tag}: path points must not all be the same")
        else:
            if geo == xy or (geo and (self.lat is None or self.lng is None)) or (xy and (self.x is None or self.y is None)):
                raise ValueError(f"{tag}: anchor 'map' needs x and y (normalised 0..1) or lat and lng")
            if xy and not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
                raise ValueError(f"{tag}: map x and y must be within 0..1")
        if self.t_out is not None and self.t_out <= self.t_in:
            raise ValueError(f"{tag}: t_out {self.t_out} must be > t_in {self.t_in}")
        if self.path is None and (self.path_s is not None or self.heading):
            raise ValueError(f"{tag}: path_s and heading need a path")
        return self


LAYER_EXT = {".png", ".webp", ".jpg", ".jpeg"}
LAYER_DEPTH_MIN = -0.9  # farthest parallax layer (moves at 0.1 x the map)


class ParallaxLayer(Strict):
    """A repeating full-frame image over the map that moves with the camera at its own depth (video_engine/parallax.py)."""
    asset: str = Field(min_length=1)                     # project-relative .png/.webp (alpha) or .jpg, tiled
    asset_id: Optional[str] = None                       # credits manifest id (None = matched by path)
    depth: float = Field(0.5, ge=LAYER_DEPTH_MIN, le=PROP_DEPTH_MAX)   # 0 = moves with the map, > 0 nearer, < 0 farther
    tile_px: Optional[float] = Field(None, gt=0, le=8000) # on-screen width of one tile at frame 0 (None = image width)
    opacity: float = Field(1.0, gt=0, le=1.0)
    blend: Literal["normal", "screen", "multiply", "overlay", "soft-light"] = "normal"
    drift: list[float] = Field([0.0, 0.0], min_length=2, max_length=2)  # px / s at frame 0 scale (wind)

    @model_validator(mode="after")
    def _layer(self):
        if Path(self.asset).suffix.lower() not in LAYER_EXT:
            raise ValueError(f"layer '{self.asset}': asset must be a .png, .webp or .jpg image")
        if not all(-2000 <= v <= 2000 for v in self.drift):
            raise ValueError(f"layer '{self.asset}': drift {self.drift} must be within -2000..2000 px/s")
        return self


class Beat(Strict):
    """One beat of a beats map scene (profile [layout]): what is on screen from `at_frame` (relative to the segment
    start) to the next beat or the segment end. video_engine/layout.py derives the camera_path, the entry and exit
    times and every position from the beats. Indices point into the scene's pins, routes, figures and regions."""
    at_frame: int = Field(ge=0)
    region: Optional[int] = Field(None, ge=0)        # active region; every other region shows at layout dim_alpha
    pins: list[int] = []                             # pins with their label
    markers: list[int] = []                          # pins without their label
    routes: list[int] = []
    title: bool = False                              # the scene title in Z1
    card: list[int] = []                             # Z1 data card: [year figure] or [year figure, time figure]
    area_labels: list[AreaLabel] = []
    camera: bool = True                              # True: the camera fits this beat into Z2; False: keeps its view
    heading: Optional[str] = Field(None, min_length=1, max_length=32)   # profile [print]: date heading near the top
    notes: list[Note] = Field([], max_length=1)      # profile [print]: at most one hand annotation per beat
    props: Optional[list[Prop]] = None               # props timed from this beat's start
    grade: Optional[Grade] = None                    # full-frame grade from this beat to the next (None: the segment's)
    hold: Optional[Literal["read"]] = None           # intent: the viewer reads; no camera pulses on this beat's wait
    goal: Optional[str] = Field(None, max_length=200)   # what the viewer must understand or feel (information)

    @model_validator(mode="after")
    def _beat(self):
        if set(self.pins) & set(self.markers):
            raise ValueError(f"beat at_frame {self.at_frame}: a pin is in both pins and markers")
        if self.title and self.card:
            raise ValueError(f"beat at_frame {self.at_frame}: Z1 holds the title or the data card, not both")
        if self.heading is not None and self.title:
            raise ValueError(f"beat at_frame {self.at_frame}: a heading and the title do not share a beat")
        if len(self.card) > 2:
            raise ValueError(f"beat at_frame {self.at_frame}: the data card holds a year and at most one time")
        return self
