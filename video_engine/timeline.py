"""master_timeline.json data contract. All times are integer frames; sec_to_frame() is the ONLY seconds->frames
conversion in the engine (delegates to the Rust crate so Python and Rust can never disagree)."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import hypoverum_core_rs as core

from .projection import MERC_LAT_MAX

from . import config
from .grade import Grade
from .util import EngineError

NATIVE = "NATIVE_RUST_FFMPEG"
HYPERFRAMES = "HYPERFRAMES_GSAP"
HEX_RE = r"#[0-9a-fA-F]{6}"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
VisualType = Literal["map", "photo", "video", "diagram", "archive", "illustration"]


def sec_to_frame(seconds: float, fps: int) -> int:
    return core.sec_to_frame(float(seconds), int(fps))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Strict):
    width: int = Field(1080, gt=0, multiple_of=2)
    height: int = Field(1920, gt=0, multiple_of=2)
    fps: int = Field(30, ge=1, le=99)
    work_dir_shm: Optional[str] = None
    output_drive_name: Optional[str] = None
    project_number: Optional[str] = Field(None, pattern=r"^\d{2,3}$")
    topic: Optional[str] = None
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class Credit(Strict):
    asset_id: str
    source_url: str
    license: str
    path: Optional[str] = None
    author: Optional[str] = None
    watermark_free: Optional[bool] = None


class CaptionFix(Strict):
    """Replace a recognised word sequence (case-insensitive, punctuation kept from the replacement)."""
    match: str = Field(alias="from")
    to: str
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


SFX_KINDS = ("pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser")
SfxKind = Literal["pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser"]


class SfxCue(Strict):
    """One sound effect of the mix, generated from a render event (sfx.cues): the sound `asset_id` of `kind` starts
    at `time_s` (absolute seconds) at `gain_db`. `trim_s` is cut from the file start (cue placed before 0 s);
    the cue stops at `max_s` with a short fade."""
    time_s: float = Field(ge=0)
    kind: SfxKind
    gain_db: float = Field(ge=-60, le=0)
    asset_id: str = Field(min_length=1)   # source asset id of the resolved file (provenance `id`)
    event: str                            # render event: pin, region, route, whip, label, title, figure, counter_tick
    frame: int = Field(ge=0)              # absolute frame of the render event
    trim_s: float = Field(0.0, ge=0)
    max_s: float = Field(2.0, gt=0, le=10)


class MusicBrief(Strict):
    """What the background music should be; the sound resolver picks the file."""
    mood: str = Field(min_length=1, max_length=60)          # e.g. "tense", "playful", "folk"
    energy: Literal["low", "medium", "high"]
    bpm_min: int = Field(ge=30, le=240)
    bpm_max: int = Field(ge=30, le=240)
    duration_s: float = Field(gt=0, le=600)                  # music length to cover (normally the video length)

    @model_validator(mode="after")
    def _bpm(self):
        if self.bpm_max < self.bpm_min:
            raise ValueError(f"music_brief: bpm_max {self.bpm_max} < bpm_min {self.bpm_min}")
        return self


class Stt(Strict):
    """xAI Grok Voice Transcribe settings: model id and optional keyterms (at most 100, each at most 50 chars)."""
    model: str = "grok-voice-transcribe-2.0"
    keyterms: list[str] = []

    @model_validator(mode="after")
    def _keyterms(self):
        if len(self.keyterms) > 100:
            raise ValueError(f"stt.keyterms: {len(self.keyterms)} terms, at most 100")
        long = [k for k in self.keyterms if len(k) > 50]
        if long:
            raise ValueError(f"stt.keyterms: terms longer than 50 characters: {long[:3]}")
        return self


class AudioChain(Strict):
    voiceover_path: Optional[str] = None
    bgm_path: Optional[str] = None
    bgm_asset_id: Optional[str] = None
    music_brief: Optional[MusicBrief] = None   # profile [sound]: music from the sound resolver (else bgm_path)
    target_lufs: Optional[float] = None   # None = profile value (profiles.loudness)
    true_peak: Optional[float] = None
    lra: Optional[float] = Field(None, gt=0)
    auto_ducking: bool = True
    bgm_gain_db: float = -10.0
    language: str = "id"
    stt: Stt = Stt()
    auto_captions: bool = True
    caption_fixes: list[CaptionFix] = []

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in (_LEGACY_MODEL_KEY, "initial_prompt")}
        return data


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


GLOBE_ALT_MIN = 0.005   # lowest globe camera altitude (earth radii above the surface, ~32 km)
GLOBE_ALT_MAX = 12.0


class GlobeKey(Strict):
    """One view of a globe camera: it looks at (lat, lng) from `altitude` earth radii above the surface at `at_frame`."""
    at_frame: int = Field(ge=0)
    lat: float = Field(ge=-85, le=85)
    lng: float = Field(ge=-180, le=180)
    altitude: float = Field(ge=GLOBE_ALT_MIN, le=GLOBE_ALT_MAX)
    tilt: float = Field(0.0, ge=0.0, le=TILT_MAX)      # deg from straight down, the camera south of the point
    hold: int = Field(0, ge=0, le=90)                  # frames still on this key


class Globe(Strict):
    """Blue Marble globe (three.js, video_engine/globe.py): map_asset is the whole-world equirectangular texture."""
    keys: list[GlobeKey] = Field(min_length=1)
    detail_tiles: Optional[str] = None   # project-relative dir of the NASA 8-tile set (*.A1.jpg ... *.D2.jpg, 90 deg each)
    halo: Optional[GlobeHalo] = None     # soft atmospheric rim (P14)

    @model_validator(mode="after")
    def _order(self):
        if self.keys[0].at_frame != 0:
            raise ValueError("globe.keys[0].at_frame must be 0")
        for i in range(1, len(self.keys)):
            if self.keys[i].at_frame <= self.keys[i - 1].at_frame:
                raise ValueError(f"globe.keys[{i}].at_frame must be > globe.keys[{i - 1}].at_frame")
            if self.keys[i - 1].at_frame + self.keys[i - 1].hold >= self.keys[i].at_frame:
                raise ValueError(f"globe.keys[{i - 1}].at_frame + hold must be < globe.keys[{i}].at_frame")
        return self


class GridBackdrop(Strict):
    """Screen-fixed blue grid behind the map/globe (P12). Values default from profile [fx.grid]; `graticule`
    also draws latitude/longitude lines over the map in map coordinates (they follow the camera)."""
    colour: Optional[str] = Field(None, pattern=HEX_RE)
    bg: Optional[str] = Field(None, pattern=HEX_RE)
    cell_pct: Optional[float] = Field(None, gt=0, le=50)
    line_px: Optional[float] = Field(None, ge=1, le=20)
    alpha: Optional[float] = Field(None, ge=0, le=1)
    graticule: bool = False


class StarsBackdrop(Strict):
    """Deterministic starfield behind a globe (P14); positions come from `seed` or the project id + segment index."""
    count: Optional[int] = Field(None, ge=0, le=5000)
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)
    twinkle: Optional[float] = Field(None, ge=0, le=1)
    parallax: Optional[float] = Field(None, ge=0, le=1)


class Backdrop(Strict):
    grid: Optional[GridBackdrop] = None
    stars: Optional[StarsBackdrop] = None


class GlobeHalo(Strict):
    """Soft atmospheric rim around the sphere (P14); follows the globe radius on every frame."""
    colour: Optional[str] = Field(None, pattern=HEX_RE)
    width_pct: Optional[float] = Field(None, gt=0, le=20)
    alpha: Optional[float] = Field(None, ge=0, le=1)


class TimezoneBand(Strict):
    from_lng: float = Field(ge=-180, le=180)
    to_lng: float = Field(ge=-180, le=180)
    label: str = Field(min_length=1, max_length=12)
    colour: Optional[str] = Field(None, pattern=HEX_RE)

    @model_validator(mode="after")
    def _order(self):
        if self.to_lng <= self.from_lng:
            raise ValueError(f"timezone band {self.label}: to_lng {self.to_lng} must be > from_lng {self.from_lng}")
        return self


class Timezones(Strict):
    """Vertical longitude bands on a flat map (P17); `alpha`/`at_frame` default from profile [fx.timezone]."""
    bands: list[TimezoneBand] = Field(min_length=1)
    alpha: Optional[float] = Field(None, ge=0, le=1)
    at_frame: Optional[int] = Field(None, ge=0)


class Hatch(Strict):
    """Diagonal SVG pattern inside a region polygon (P17); values default from profile [fx.hatch]."""
    angle: Optional[float] = Field(None, ge=0, le=180)
    spacing_px: Optional[float] = Field(None, ge=2, le=100)
    line_px: Optional[float] = Field(None, ge=1, le=20)
    colour: Optional[str] = Field(None, pattern=HEX_RE)
    alpha: Optional[float] = Field(None, ge=0, le=1)


class BubbleAnchor(Strict):
    pin: Optional[int] = Field(None, ge=0)
    region: Optional[int] = Field(None, ge=0)
    xy: Optional[list[float]] = Field(None, min_length=2, max_length=2)
    overlay: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _one(self):
        set_ = [k for k in ("pin", "region", "xy", "overlay") if getattr(self, k) is not None]
        if len(set_) != 1:
            raise ValueError("bubble anchor needs exactly one of pin, region, xy, overlay")
        if self.xy is not None and not all(0.0 <= v <= 1.0 for v in self.xy):
            raise ValueError(f"bubble anchor xy {self.xy} must be within 0..1")
        return self


class Bubble(Strict):
    """Graphic dialogue bubble tied to a map element (P13). Never a caption: the text is short and the bubble
    stays above the caption band."""
    text: str = Field(min_length=1, max_length=60)
    anchor: BubbleAnchor
    side: Literal["auto", "left", "right", "top"] = "auto"
    at_frame: int = Field(0, ge=0)
    out_frame: Optional[int] = Field(None, ge=0)
    style: Optional[Literal["light", "dark"]] = None

    @model_validator(mode="after")
    def _shape(self):
        if not self.text.strip():
            raise ValueError("bubble text must not be empty")
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"bubble out_frame {self.out_frame} must be > at_frame {self.at_frame}")
        return self


class FlagCharacter(Strict):
    """A flag-icon SVG treated as a character (P15): soft deterministic cloth wave, two eyes with look direction and
    a seeded blink schedule. Screen-anchored; sits above the map and below the captions."""
    flag: Flag
    xy: list[float] = Field(min_length=2, max_length=2)   # normalised frame centre
    height_pct: float = Field(14, gt=0, le=60)            # character height as a share of the frame height
    eyes: str = Field("blink", pattern=r"^(look_left|look_right|blink|look_at:\d+)$")
    at_frame: int = Field(0, ge=0)
    out_frame: Optional[int] = Field(None, ge=0)
    wave: bool = True

    @model_validator(mode="after")
    def _shape(self):
        if not all(0.0 <= v <= 1.0 for v in self.xy):
            raise ValueError(f"flag character {self.flag.code}: xy {self.xy} must be within 0..1")
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"flag character {self.flag.code}: out_frame {self.out_frame} must be > at_frame {self.at_frame}")
        return self


class CutawayLayer(Strict):
    label: str = Field(min_length=1, max_length=40)
    colour: str = Field(pattern=HEX_RE)
    thickness: float = Field(gt=0, le=1)                  # relative share of the section height


class Cutaway(Strict):
    """Stacked cross-section diagram (P16): layers listed top to bottom, revealed one after another. Labels sit
    beside the section; no dark box (P10: boxes are for map labels and big numbers only)."""
    layers: list[CutawayLayer] = Field(min_length=1, max_length=8)
    xy: list[float] = Field([0.5, 0.5], min_length=2, max_length=2)   # normalised centre of the section
    width_pct: float = Field(42, gt=0, le=90)             # section width as a share of the frame width
    at_frame: int = Field(0, ge=0)
    reveal_s: Optional[float] = Field(None, ge=0.05, le=5.0)   # None = profile [fx.cutaway] reveal_s

    @model_validator(mode="after")
    def _shape(self):
        if not all(0.0 <= v <= 1.0 for v in self.xy):
            raise ValueError(f"cutaway xy {self.xy} must be within 0..1")
        return self


class InsetAnchor(Strict):
    pin: Optional[int] = Field(None, ge=0)
    xy: Optional[list[float]] = Field(None, min_length=2, max_length=2)

    @model_validator(mode="after")
    def _one(self):
        n = [k for k in ("pin", "xy") if getattr(self, k) is not None]
        if len(n) != 1:
            raise ValueError("inset anchor needs exactly one of pin, xy")
        if self.xy is not None and not all(0.0 <= v <= 1.0 for v in self.xy):
            raise ValueError(f"inset anchor xy {self.xy} must be within 0..1")
        return self


class Inset(Strict):
    """Photo inset with a thin white frame and a leader line to its location (P16). The asset is resolved through the
    asset registry, so its credit is added automatically."""
    asset: str = Field(min_length=1)
    anchor: InsetAnchor
    size_pct: float = Field(28, gt=0, le=70)              # inset width as a share of the frame width
    at_frame: int = Field(0, ge=0)
    out_frame: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _shape(self):
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"inset '{self.asset}': out_frame {self.out_frame} must be > at_frame {self.at_frame}")
        return self


class Geoid(Strict):
    """Exaggerated, coloured Earth model on a globe scene (P18). The displacement is a seeded sum of smooth waves;
    values default from profile [fx.geoid]."""
    exaggeration: Optional[float] = Field(None, ge=0, le=0.5)
    colour_ramp: Optional[list[str]] = Field(None, min_length=2, max_length=8)
    rotate_deg_s: Optional[float] = Field(None, ge=-360, le=360)
    at_frame: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _shape(self):
        for c in self.colour_ramp or []:
            if not re.fullmatch(HEX_RE, c):
                raise ValueError(f"geoid colour {c!r} must be #rrggbb")
        return self


class Particle(Strict):
    """Deterministic particle accent (P19): analytic in (seed, frame), so any seek renders the same positions.
    `fireworks` is a radial burst with gravity; `fire` is an upward flame with seeded noise."""
    kind: Literal["fireworks", "fire"]
    xy: list[float] = Field(min_length=2, max_length=2)   # normalised frame position
    at_frame: int = Field(0, ge=0)
    duration_s: Optional[float] = Field(None, ge=0.2, le=10.0)   # None = profile [fx.particles] duration_s
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)        # None = project id + segment index
    scale: float = Field(1.0, ge=0.2, le=4.0)
    colours: Optional[list[str]] = Field(None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def _shape(self):
        if not all(0.0 <= v <= 1.0 for v in self.xy):
            raise ValueError(f"particle {self.kind}: xy {self.xy} must be within 0..1")
        for c in self.colours or []:
            if not re.fullmatch(HEX_RE, c):
                raise ValueError(f"particle colour {c!r} must be #rrggbb")
        return self


class Thermometer(Strict):
    """Screen thermometer (components.py): one source `value` drives the fill height, the fill colour (colour_low at
    `min` to colour_high at `max`) and the counter text together, rising from `min` over `fill_s` from `at_frame`.
    `note` is shown under the label: give "ilustratif" (or the source) whenever the value is not a sourced reading."""
    value: float
    min: float = 0.0
    max: float = 50.0
    unit: str = Field("°C", max_length=6)
    decimals: int = Field(0, ge=0, le=2)
    label: Optional[str] = Field(None, min_length=1, max_length=24)
    note: Optional[str] = Field(None, min_length=1, max_length=32)
    at_frame: int = Field(0, ge=0)
    out_frame: Optional[int] = Field(None, ge=0)
    fill_s: float = Field(1.6, ge=0.3, le=6.0)
    position: list[float] = Field([0.82, 0.5], min_length=2, max_length=2)   # frame fraction of the tube centre
    height_pct: float = Field(30.0, ge=12.0, le=60.0)                        # tube + bulb height, % of frame height
    colour_low: str = Field("#4aa3ff", pattern=HEX_RE)
    colour_high: str = Field("#ff4a3d", pattern=HEX_RE)

    @model_validator(mode="after")
    def _t(self):
        if not self.min < self.max:
            raise ValueError(f"thermometer: min {self.min} must be < max {self.max}")
        if not self.min <= self.value <= self.max:
            raise ValueError(f"thermometer: value {self.value} must be within {self.min}..{self.max}")
        if not all(0.0 <= v <= 1.0 for v in self.position):
            raise ValueError(f"thermometer: position {self.position} must be within 0..1")
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"thermometer: out_frame {self.out_frame} must be > at_frame {self.at_frame}")
        return self


class Flow(Strict):
    """Parametric flow (wind, current, migration; components.py): dashed streams along map `paths` (normalised [x, y]
    points, start to end) that move toward the end at `speed_px` screen px per second of render time, with an optional
    arrowhead at each end. Fades in over `reveal_s` from `at_frame` and out over `fade_s` from `out_frame`. Width,
    dashes and speed keep their on-screen size while the camera zooms; `seed` fixes each stream's dash phase. `label`
    goes to the scene's flow legend with the colour."""
    paths: list[list[list[float]]] = Field(min_length=1, max_length=16)
    colour: str = Field("#7fd4ff", pattern=HEX_RE)
    alpha: float = Field(0.9, gt=0, le=1)
    width_px: float = Field(6.0, ge=2.0, le=24.0)
    dash_px: float = Field(18.0, ge=4.0, le=120.0)
    gap_px: float = Field(26.0, ge=4.0, le=200.0)
    speed_px: float = Field(120.0, ge=10.0, le=800.0)
    arrows: bool = True
    at_frame: int = Field(0, ge=0)
    reveal_s: float = Field(0.8, ge=0.0, le=4.0)
    out_frame: Optional[int] = Field(None, ge=0)
    fade_s: float = Field(0.5, ge=0.1, le=4.0)
    label: Optional[str] = Field(None, min_length=1, max_length=32)
    seed: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _f(self):
        for i, p in enumerate(self.paths):
            if len(p) < 2:
                raise ValueError(f"flow paths[{i}]: at least 2 points")
            for pt in p:
                if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
                    raise ValueError(f"flow paths[{i}]: point {pt} must be [x, y] within 0..1")
        if self.out_frame is not None and self.out_frame <= self.at_frame:
            raise ValueError(f"flow: out_frame {self.out_frame} must be > at_frame {self.at_frame}")
        return self


class GsapScene(Strict):
    template: Literal["map_pins"] = "map_pins"
    map_region: Optional[str] = None
    map_asset: str                       # path (project-relative) of the base map image
    map_asset_id: Optional[str] = None
    map_bounds: Optional[list[float]] = Field(None, min_length=4, max_length=4)  # [south, west, north, east]
    map_projection: Literal["equirectangular", "mercator"] = Field(   # how map_asset maps lat/lng (projection.py)
        "equirectangular", exclude_if=lambda v: v == "equirectangular")
    title: Optional[str] = None                     # big glow title, from title_at_frame to the segment end
    title_entity: Optional[str] = Field(None, min_length=1, max_length=60)  # title colour = this entity's colour
    title_at_frame: Optional[int] = Field(None, ge=0)  # None = 0
    pins: list[Pin] = []
    cards: list[Card] = []
    camera_preset: str = "pan_northeast"
    camera_intensity: float = Field(1.0, ge=0.0, le=6.0)
    focus: Optional[list[float]] = Field(None, min_length=2, max_length=2)  # normalised [x, y] the camera centres on
    camera_path: Optional[list[CameraKey]] = None   # >= 2 keys: replaces camera_preset with key-to-key legs
    regions: Optional[list[Region]] = None          # entity highlights drawn on the map under the pins
    borders: Optional[list[Border]] = None          # self-drawing neon border lines (dashed: sea borders)
    morph: Optional[MapMorph] = None                # second map texture crossfaded over the map
    routes: Optional[list[Route]] = None            # self-drawing dashed movement lines with an arrowhead
    map_texts: Optional[list[MapText]] = Field(None, max_length=8)   # names written on the map along a curve
    figures: Optional[list[Figure]] = None          # big years and numbers
    beats: Optional[list[Beat]] = None              # layout profiles: beats replace camera_path and element frames
    props: Optional[list[Prop]] = None              # free-positioned images (map- or screen-anchored)
    layers: Optional[list[ParallaxLayer]] = None    # repeating depth layers over the map (parallax)
    globe: Optional[Globe] = None                   # 3D globe instead of the flat map (map_asset = its texture)
    backdrop: Optional[Backdrop] = None             # screen-fixed grid/stars behind the map or globe (P12/P14)
    timezones: Optional[Timezones] = None           # longitude bands on a flat map (P17)
    bubbles: Optional[list[Bubble]] = None          # graphic dialogue bubbles, never captions (P13)
    flag_characters: Optional[list[FlagCharacter]] = None   # flag-icon characters with eyes (P15)
    cutaway: Optional[Cutaway] = None               # stacked cross-section diagram (P16)
    insets: Optional[list[Inset]] = None            # framed photo insets with leader lines (P16)
    geoid: Optional[Geoid] = None                   # exaggerated coloured Earth on a globe scene (P18)
    particles: Optional[list[Particle]] = None      # deterministic fireworks / fire accents (P19)
    thermometers: Optional[list[Thermometer]] = Field(None, max_length=3)   # screen thermometers (components.py)
    flows: Optional[list[Flow]] = Field(None, max_length=8)    # moving dashed streams on the map (components.py)
    flow_legend: Literal["top", "bottom"] = Field("bottom", exclude_if=lambda v: v == "bottom")   # labelled flows
    split: Optional["SplitView"] = None             # two stacked viewports, each its own map and camera (top = this
                                                    # scene, bottom = split.second; hyperframes.split_panels)

    @model_validator(mode="after")
    def _split(self):
        if self.split is None:
            return self
        if self.split.second.split is not None:
            raise ValueError("split: the second panel cannot split again")
        for name, sc in (("top", self), ("bottom", self.split.second)):
            if not sc.camera_path:
                raise ValueError(f"split: the {name} panel needs its own camera_path (independent cameras)")
            if sc.beats or sc.globe:
                raise ValueError(f"split: the {name} panel cannot be a beats or globe scene")
        return self

    def panels(self) -> list["GsapScene"]:
        """This scene, then the split's second panel when there is one."""
        return [self] + ([self.split.second] if self.split else [])

    @model_validator(mode="after")
    def _bubbles(self):
        n = {"pin": len(self.pins), "region": len(self.regions or [])}
        for i, b in enumerate(self.bubbles or []):
            for what in ("pin", "region"):
                idx = getattr(b.anchor, what)
                if idx is not None and idx >= n[what]:
                    raise ValueError(f"bubbles[{i}]: {what} index {idx} out of range ({n[what]} {what}s)")
        return self

    @model_validator(mode="after")
    def _fx(self):
        pins = len(self.pins)
        for i, c in enumerate(self.flag_characters or []):
            if c.eyes.startswith("look_at:"):
                idx = int(c.eyes.split(":", 1)[1])
                if idx >= pins:
                    raise ValueError(f"flag_characters[{i}]: look_at pin {idx} out of range ({pins} pins)")
        for i, ins in enumerate(self.insets or []):
            if ins.anchor.pin is not None and ins.anchor.pin >= pins:
                raise ValueError(f"insets[{i}]: pin index {ins.anchor.pin} out of range ({pins} pins)")
        if self.geoid is not None and self.globe is None:
            raise ValueError("geoid needs a globe scene (gsap_scene_config.globe)")
        return self

    @model_validator(mode="after")
    def _map_bounds(self):
        b = self.map_bounds
        if b is None and self.map_projection == "mercator":
            raise ValueError("map_projection mercator needs map_bounds")
        if b is not None:
            south, west, north, east = b
            if not south < north or not west < east:
                raise ValueError(f"map_bounds [south, west, north, east] = {b} needs south < north and west < east")
            if self.map_projection == "mercator" and max(abs(south), abs(north)) > MERC_LAT_MAX:
                raise ValueError(f"map_bounds {b}: a mercator map stays within latitude +-{MERC_LAT_MAX}")
        return self

    @model_validator(mode="after")
    def _beats(self):
        b = self.beats
        if not b:
            return self
        if b[0].at_frame != 0:
            raise ValueError(f"beats[0].at_frame is {b[0].at_frame}; the first beat starts at frame 0")
        for i in range(1, len(b)):
            if b[i].at_frame <= b[i - 1].at_frame:
                raise ValueError(f"beats[{i}].at_frame {b[i].at_frame} must be > beats[{i - 1}].at_frame {b[i - 1].at_frame}")
        n = {"pins": len(self.pins), "routes": len(self.routes or []), "figures": len(self.figures or []),
             "regions": len(self.regions or [])}
        for i, x in enumerate(b):
            refs = [("pins", x.pins + x.markers), ("routes", x.routes), ("figures", x.card),
                    ("regions", ([x.region] if x.region is not None else []) + [a.region for a in x.area_labels])]
            for what, idx in refs:
                for j in idx:
                    if j >= n[what]:
                        raise ValueError(f"beats[{i}]: {what} index {j} out of range ({n[what]} {what})")
            if x.title and not self.title:
                raise ValueError(f"beats[{i}]: title is true but gsap_scene_config.title is empty")
            if x.card and self.figures[x.card[0]].kind != "year":
                raise ValueError(f"beats[{i}]: the first data card figure must be a year")
        for r in self.routes or []:
            if r.to_pin is not None and r.to_pin >= len(self.pins):
                raise ValueError(f"route to_pin {r.to_pin} out of range ({len(self.pins)} pins)")
        return self

    @model_validator(mode="after")
    def _globe(self):
        g = self.globe
        if g is None:
            return self
        for f in ("camera_path", "beats", "routes", "borders", "morph", "props", "layers", "map_bounds"):
            if getattr(self, f):
                raise ValueError(f"globe scenes do not take {f} (the globe camera is globe.keys; pins use lat/lng)")
        if self.map_projection != "equirectangular":
            raise ValueError("globe scenes take an equirectangular world texture (map_projection)")
        if self.flows:
            raise ValueError("globe scenes do not take flows yet (flows are drawn on a flat map)")
        for r in self.regions or []:
            if r.rings:
                raise ValueError(f"region '{r.entity}': multi-ring regions (rings) are not drawn on the globe yet")
        for p in self.pins:
            if p.lat is None or p.lng is None:
                raise ValueError(f"globe pin '{p.label}' needs lat and lng")
        return self

    @model_validator(mode="after")
    def _path(self):
        k = self.camera_path
        if not k:
            return self
        if len(k) < 2:
            raise ValueError("camera_path needs at least 2 keys")
        if k[0].at_frame != 0:
            raise ValueError(f"camera_path[0].at_frame is {k[0].at_frame}; the first key is the opening view at frame 0")
        for i in range(1, len(k)):
            if k[i].at_frame <= k[i - 1].at_frame + k[i - 1].hold:
                raise ValueError(f"camera_path[{i}].at_frame {k[i].at_frame} must be > camera_path[{i - 1}].at_frame "
                                 f"{k[i - 1].at_frame} + hold {k[i - 1].hold}")
        return self


class SplitView(Strict):
    """Split screen: the frame holds two panels of equal height stacked top / bottom with a `divider_px` line between
    them; each panel is rendered by HyperFrames at the panel size with its own map and camera, both on the segment's
    frame clock (same n_frames and fps), and the engine stacks them (hyperframes.render)."""
    second: GsapScene
    divider_px: int = Field(8, ge=2, le=24, multiple_of=2)
    divider_colour: str = Field("#ffffff", pattern=HEX_RE)


GsapScene.model_rebuild()


class Overlay(Strict):
    """On-screen element of a native segment, in output px. `kind` "reserve" only reserves space (caption placement,
    R63 clearance). "label", "text_card" and "lower_third" are drawn by the engine (Pillow PNG overlaid by ffmpeg)
    inside `box`; gate O01 checks that the text fits the box and the box sits in the safe area. "heading" (profile
    [print]) is the printed date heading: one line of caps between two rules, centred in `box` with its baseline
    overlays.HEADING_PAD px above the box bottom, in heading ink on a light picture (`on` "light") or light with a soft
    shadow on a photo (`on` "photo")."""
    id: str
    box: list[float] = Field(min_length=4, max_length=4)   # [x, y, w, h] output px
    text: Optional[str] = None
    subtitle: Optional[str] = None
    kind: Literal["reserve", "label", "text_card", "lower_third", "heading"] = "reserve"
    on: Optional[Literal["light", "photo"]] = None           # heading only: the picture tone under it
    start_frame: Optional[int] = Field(None, ge=0)          # relative to segment start; default 0
    end_frame: Optional[int] = Field(None, gt=0)            # relative, exclusive; default segment length

    @model_validator(mode="after")
    def _shape(self):
        x, y, w, h = self.box
        if not all(math.isfinite(v) for v in self.box) or w <= 0 or h <= 0 or x < 0 or y < 0:
            raise ValueError(f"overlay '{self.id}': box {self.box} needs finite x, y >= 0 and w, h > 0")
        if self.kind != "reserve" and not (self.text and self.text.strip()):
            raise ValueError(f"overlay '{self.id}': kind '{self.kind}' is drawn and needs text")
        if (self.kind == "heading") != (self.on is not None):
            raise ValueError(f"overlay '{self.id}': `on` (light | photo) is required for kind 'heading' and only for it")
        if self.kind == "heading" and (self.subtitle or len(self.text or "") > 32):
            raise ValueError(f"overlay '{self.id}': a heading is one line of at most 32 characters, no subtitle")
        if self.start_frame is not None and self.end_frame is not None and self.end_frame <= self.start_frame:
            raise ValueError(f"overlay '{self.id}': end_frame {self.end_frame} must be > start_frame {self.start_frame}")
        return self

    @property
    def drawn(self) -> bool:
        return self.kind != "reserve"


class SegmentIntent(Strict):
    """Local creative expectations, never an exemption from render/decode integrity."""
    motion: Optional[Literal["static", "moving"]] = None
    negative_space: bool = Field(False, strict=True)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _intent(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("intent.reason must not be blank")
        if self.motion is None and not self.negative_space:
            raise ValueError("intent needs motion or intentional negative_space")
        return self


class Segment(Strict):
    seg_index: int = Field(ge=0)
    engine_type: Literal["NATIVE_RUST_FFMPEG", "HYPERFRAMES_GSAP"]
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    source_video: Optional[str] = None
    source_image: Optional[str] = None
    asset_id: Optional[str] = None
    source_in_frame: int = Field(0, ge=0)   # in TIMELINE fps frames
    camera_move: Optional[CameraMove] = None
    overlays: list[Overlay] = []
    gsap_scene_config: Optional[GsapScene] = None
    visual_type: Optional[VisualType] = None   # None = inferred: HyperFrames map, native still photo, native video
    transition_in: Optional[Literal["cut", "crossfade", "state", "zoom_whip", "zoom_through", "page_turn"]] = None   # overrides profile
    grade: Optional[Grade] = None              # full-frame grade (video_engine/grade.py); None = profile [grade]
    hold: Optional[Literal["read"]] = None     # intent: the viewer reads over the whole segment (gates/severity.py)
    intent: Optional[SegmentIntent] = None
    creative_autofix: bool = Field(False, strict=True)
    goal: Optional[str] = Field(None, max_length=200)   # what the viewer must understand or feel (information)

    @property
    def motion_expectation(self) -> Optional[str]:
        # Explicit local intent takes precedence over the backwards-compatible read hold.
        if self.intent and self.intent.motion is not None:
            return self.intent.motion
        return "static" if self.hold == "read" else None

    @property
    def kind(self) -> str:
        """Visual type: declared visual_type, else map (HyperFrames), photo (native still) or video (native video)."""
        if self.visual_type:
            return self.visual_type
        if self.engine_type == HYPERFRAMES:
            return "map"
        return "photo" if self.is_still else "video"

    @property
    def n_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def source(self) -> Optional[str]:
        return self.source_video or self.source_image

    @property
    def is_still(self) -> bool:
        return self.source is not None and Path(self.source).suffix.lower() in IMAGE_EXT

    @model_validator(mode="after")
    def _shape(self):
        if self.end_frame <= self.start_frame:
            raise ValueError(f"segment {self.seg_index}: end_frame {self.end_frame} <= start_frame {self.start_frame}")
        if self.engine_type == NATIVE:
            if bool(self.source_video) == bool(self.source_image):
                raise ValueError(f"segment {self.seg_index}: native segment needs exactly one of source_video/source_image")
            if self.gsap_scene_config is not None:
                raise ValueError(f"segment {self.seg_index}: gsap_scene_config on a native segment")
        else:
            if self.gsap_scene_config is None:
                raise ValueError(f"segment {self.seg_index}: HYPERFRAMES_GSAP segment needs gsap_scene_config")
            for i, b in enumerate(self.gsap_scene_config.bubbles or []):
                if b.anchor.overlay is not None and b.anchor.overlay >= len(self.overlays):
                    raise ValueError(f"segment {self.seg_index} bubbles[{i}]: overlay index {b.anchor.overlay} "
                                     f"out of range ({len(self.overlays)} overlays)")
        return self


MAX_FRAME = 1 << 40   # = Rust num::MAX_FRAME; frames are 0 <= frame < 2**40
_LEGACY_MODEL_KEY = "whis" + "per_model"
_LEGACY_SOURCE = "whis" + "per"


class CaptionWord(Strict):
    word: str
    start_frame: int = Field(ge=0, lt=MAX_FRAME)
    end_frame: int = Field(gt=0, lt=MAX_FRAME)
    highlight: bool = False

    @model_validator(mode="after")
    def _order(self):
        if self.end_frame <= self.start_frame:
            raise ValueError(f"caption word {self.word!r}: end_frame {self.end_frame} must be > start_frame "
                             f"{self.start_frame}")
        return self


class CaptionsMeta(Strict):
    """Provenance of pinned `captions` (written by `engine words` / the build). Gate W01 compares it with the
    current voiceover, caption_fixes and fps; any mismatch means the pinned words are stale."""
    source: Literal["stt"] = "stt"
    voiceover_blake3: str
    stt_model: str
    language: str
    keyterms_blake3: str = ""
    fixes_blake3: str
    fps: int = Field(ge=1, le=99)
    total_frames: int = Field(gt=0, lt=MAX_FRAME)
    clipped_words: int = Field(ge=0, default=0)   # transcribed words dropped or cut at total_frames
    words_cache_hit: bool = False

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        old_model = d.pop(_LEGACY_MODEL_KEY, None)
        d.pop("prompt", None)
        if d.get("source") == _LEGACY_SOURCE:
            d["source"] = "stt"
        if old_model is not None and not d.get("stt_model"):
            d["stt_model"] = old_model
        return d


MIN_WAIVER_REASON = 10   # characters; a waiver has to say why


class GateWaiver(Strict):
    """One `gates.waive_with_reason` entry: a technical layout or overlay gate the project lowers to info, with the
    reason recorded in build_report.json."""
    gate: str
    reason: str

    @model_validator(mode="after")
    def _w(self):
        from .gate_registry import WAIVABLE_WITH_REASON
        if self.gate not in WAIVABLE_WITH_REASON:
            raise ValueError(f"gates.waive_with_reason: {self.gate!r} cannot be waived "
                             f"(waivable with a reason: {', '.join(WAIVABLE_WITH_REASON)})")
        if len(self.reason.strip()) < MIN_WAIVER_REASON:
            raise ValueError(f"gates.waive_with_reason: the reason for {self.gate} must say why "
                             f"(at least {MIN_WAIVER_REASON} characters)")
        return self


class ProjectGates(Strict):
    """Project gate requests (gates/severity.py). `require` adds style gates to the channel's required set; `waive`
    lowers findings of style gates the channel does not require to info. `waive_with_reason` lowers the findings of
    the few technical gates marked waivable in the gate registry (L01, L06, O01), with a written reason that the build
    report keeps. Nothing here can lower a channel-required gate or any other technical gate."""
    require: list[str] = []
    waive: list[str] = []
    waive_with_reason: list[GateWaiver] = []

    @model_validator(mode="after")
    def _ids(self):
        from .profiles import KNOWN_GATE_IDS, STYLE_GATE_IDS
        bad = [g for g in self.require if g not in STYLE_GATE_IDS]
        if bad:
            raise ValueError(f"gates.require: unknown style gate(s) {bad} (known: {', '.join(STYLE_GATE_IDS)})")
        bad = [g for g in self.waive if g not in KNOWN_GATE_IDS]
        if bad:
            raise ValueError(f"gates.waive: unknown gate(s) {bad} (known: {', '.join(KNOWN_GATE_IDS)})")
        names = [w.gate for w in self.waive_with_reason]
        dup = sorted({g for g in names if names.count(g) > 1})
        if dup:
            raise ValueError(f"gates.waive_with_reason: {', '.join(dup)} listed more than once")
        return self


class Timeline(Strict):
    project_id: str = Field(pattern=config.PROJECT_ID_PATTERN)
    profile: Optional[str] = None          # channel style profile (profiles/<name>.toml); None = default profile
    settings: Settings = Settings()
    credits_manifest: list[Credit] = []
    audio_chain: AudioChain = AudioChain()
    segments: list[Segment] = Field(min_length=1)
    captions: list[CaptionWord] = []
    captions_meta: Optional[CaptionsMeta] = None
    caption_style: dict = {}
    gates: Optional[ProjectGates] = None   # project gate requests (None = channel rules as they are)
    profile_overrides: dict = Field({}, exclude_if=lambda v: not v)   # project values over the channel profile
                                                                       # (profiles.OVERRIDABLE tables; null removes)

    @model_validator(mode="after")
    def _profile_exists(self):
        from . import profiles
        from .util import EngineError
        try:
            prof = profiles.load(self.profile, self.profile_overrides)
            mz = prof.camera.max_zoom
        except EngineError as e:
            raise ValueError(str(e)) from e
        for s in self.segments[1:]:
            if s.transition_in == "page_turn" and not prof.transitions.page_turn:
                raise ValueError(f"segment {s.seg_index}: transition_in 'page_turn' is not allowed by the profile "
                                 "([transitions] page_turn = false)")
        try:
            cs = self.caption_style
            if "forbid_box" in cs and not isinstance(cs["forbid_box"], bool):
                raise EngineError("caption_style.forbid_box must be true or false")
            ab = cs.get("ab_sizes")
            if ab is not None and (not isinstance(ab, list) or not ab
                                   or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in ab)):
                raise EngineError("caption_style.ab_sizes must be a non-empty list of positive integers")
            st = {**prof.caption_style, **cs}
            if st.get("forbid_box") and st.get("box_bg") is True:
                raise EngineError("caption_style.forbid_box = true but box_bg = true; captions never use a box")
        except EngineError as e:
            raise ValueError(str(e)) from e
        for s in self.segments:
            for i, k in enumerate((s.gsap_scene_config.camera_path or []) if s.gsap_scene_config else []):
                if k.zoom > mz + 1e-9:
                    raise ValueError(f"segment {s.seg_index} camera_path[{i}]: zoom {k.zoom:g} exceeds the profile "
                                     f"[camera] max_zoom {mz:g}")
        return self

    @model_validator(mode="after")
    def _captions_sorted(self):
        for i in range(1, len(self.captions)):
            if self.captions[i].start_frame < self.captions[i - 1].start_frame:
                raise ValueError(f"captions[{i}] ({self.captions[i].word!r}) starts before captions[{i - 1}]: "
                                 "captions must be sorted by start_frame")
        return self

    @property
    def total_frames(self) -> int:
        return max(s.end_frame for s in self.segments)

    def credit_for(self, ref: Optional[str] = None, path: Optional[str] = None) -> Optional[Credit]:
        for c in self.credits_manifest:
            if (ref and c.asset_id == ref) or (path and c.path and Path(c.path) == Path(path)):
                return c
        return None


def load(path: str | Path) -> Timeline:
    p = Path(path)
    if p.is_dir():
        p = p / "master_timeline.json"
    try:
        text = p.read_text()
    except OSError as e:
        raise EngineError(f"cannot read timeline {p}: {e}") from None
    try:
        return Timeline.model_validate_json(text)
    except json.JSONDecodeError as e:
        raise EngineError(f"timeline {p} is not valid JSON: {e}") from None
    except ValidationError as e:
        details = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'value'}: {x['msg']}" for x in e.errors())
        raise EngineError(f"timeline {p}: invalid values ({details})") from e


def save(tl: Timeline, path: str | Path) -> None:
    p = Path(path)
    if p.is_dir():
        p = p / "master_timeline.json"
    data = tl.model_dump(mode="json", by_alias=True, exclude_none=True)
    fd, name = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".part")   # unique per writer
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp, p.stat().st_mode & 0o777 if p.exists() else 0o644)
        os.replace(tmp, p)   # atomic: a reader sees one whole version of the file, never a half-written one
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
