"""Channel style profiles: profiles/<name>.toml merged over profiles/default.toml.

Precedence: default profile < channel profile (`profile` in master_timeline.json) < project values
(`caption_style` keys, `audio_chain` loudness fields set in the timeline). A channel profile lists only the keys it
changes; tables merge key by key. Global safety limits (true peak, loudness tolerance) may only be tightened."""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import config
from .grade import Grade
from .layout import LayoutProfile
from .util import EngineError, blake3_json

if TYPE_CHECKING:
    from .timeline import Timeline

DEFAULT = "default"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioProfile(_Strict):
    target_lufs: float
    true_peak: float
    lra: float = Field(gt=0)
    tolerance_lu: float = Field(gt=0)
    require_source: Literal["epidemic"] | None = None   # A02: every resolved music and SFX file from this source


def _order(lo: float, hi: float, what: str) -> None:
    if lo > hi:
        raise ValueError(f"{what}: warn threshold must not be beyond the fail threshold")


class HookGate(_Strict):
    """H01: narration and first caption start, opening shot, first graphic (seconds from frame 0)."""
    vo_warn_s: float = Field(ge=0)
    vo_fail_s: float = Field(ge=0)
    caption_warn_s: float = Field(ge=0)
    caption_fail_s: float = Field(ge=0)
    graphic_warn_s: float = Field(ge=0)
    graphic_fail_s: float = Field(ge=0)
    open_on_moving_map: bool = True

    @model_validator(mode="after")
    def _o(self):
        _order(self.vo_warn_s, self.vo_fail_s, "vo")
        _order(self.caption_warn_s, self.caption_fail_s, "caption")
        _order(self.graphic_warn_s, self.graphic_fail_s, "graphic")
        return self


class CutGate(_Strict):
    """C01: hard cuts and shot changes per 10 s and time of the first one. A shot change is any segment boundary; a
    hard cut is a boundary without a [transitions] transition. WARN levels apply to hard cuts, FAIL levels to shot
    changes (without transitions both are the same boundaries)."""
    warn_per_10s: float = Field(ge=0)    # WARN when hard cuts are more frequent
    fail_per_10s: float = Field(ge=0)    # FAIL when shot changes are more frequent
    first_warn_s: float = Field(ge=0)    # WARN when the first hard cut is earlier
    first_fail_s: float = Field(ge=0)    # FAIL when the first shot change is earlier

    @model_validator(mode="after")
    def _o(self):
        _order(self.warn_per_10s, self.fail_per_10s, "per_10s")
        _order(self.first_fail_s, self.first_warn_s, "first cut")
        return self


class VisualTypeGate(_Strict):
    """V01: a hard cut between two segments of the same visual type."""


class NoveltyGate(_Strict):
    """N01: longest stretch without a new on-screen element or a new shot."""
    max_gap_s: float = Field(gt=0)


class SilenceGate(_Strict):
    """S01 (final file): longest gap below threshold_db."""
    max_gap_s: float = Field(gt=0)
    threshold_db: float = Field(lt=0)


class KeywordReturnGate(_Strict):
    """K01: a hook keyword is on screen again at the end."""
    hook_s: float = Field(gt=0)
    end_s: float = Field(gt=0)
    min_chars: int = Field(ge=1)


class DurationGate(_Strict):
    """D01: total length in seconds."""
    min_s: float = Field(gt=0)
    max_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _o(self):
        _order(self.min_s, self.max_s, "duration")
        return self


class ExactDurationGate(_Strict):
    """D02: total length pinned to a target inside a tolerance (seconds)."""
    target_s: float = Field(gt=0)
    tolerance_s: float = Field(0.5, ge=0)


class CtaGate(_Strict):
    """C02: no call to action and no end card. Every phrase is matched as whole words in on-screen text and captions."""
    phrases: list[str] = Field(min_length=1)


class EndingKeywordGate(_Strict):
    """K02: a hook keyword is still on screen on the final frame (not only somewhere in the last seconds)."""
    hook_s: float = Field(gt=0)
    end_s: float = Field(gt=0)
    min_chars: int = Field(ge=1)


class CameraGate(_Strict):
    """P01: camera motion over the timeline (moving fraction, longest hold) from the planned camera."""
    hold_warn_s: float = Field(1.7, gt=0)               # WARN when a hold (camera still) lasts longer
    hold_fail_s: float = Field(3.0, gt=0)               # FAIL when a hold lasts longer
    moving_warn: float = Field(0.8, ge=0, le=1)         # WARN when the moving fraction of the measured frames is lower
    still_zoom_ln_s: float = Field(0.05, gt=0)          # a frame is still when |zoom speed| < this (ln scale / s) ...
    still_pan_w_s: float = Field(0.02, gt=0)            # ... and the frame-centre speed < this (frame widths / s)
    min_drift_per_s: float | None = Field(None, gt=0)   # P02: theme camera_drift_per_s must reach this (None = off)

    @model_validator(mode="after")
    def _o(self):
        _order(self.hold_warn_s, self.hold_fail_s, "hold")
        return self


STYLE_GATE_IDS = ("H01", "C01", "C02", "V01", "N01", "P01", "P02", "K01", "K02", "D01", "D02", "S01",
                  "AS01", "AS02", "AS03", "AS05", "AS06")
TECHNICAL_GATE_IDS = ("A01", "A02", "AS04", "B01", "E01", "F01", "F02", "F03", "F04", "G01", "L01", "L02", "L03", "L04",
                      "L05", "L06", "L07", "L08", "L09", "L10", "L22", "M01", "M02", "M03", "M04", "G02", "O01",
                      "R07", "R14", "R38", "R44", "R56", "R56px", "R62", "R63", "R64", "T01", "T02", "W01", "Z01")
KNOWN_GATE_IDS = STYLE_GATE_IDS + TECHNICAL_GATE_IDS
NEVER_ADVISORY = ("T02", "R07", "R14", "A01", "A02", "W01", "R38", "R56px", "M01", "M03", "G01", "L22")

ANTI_SLOP_IDS = ("AS01", "AS02", "AS03", "AS04", "AS05", "AS06")


class GeoGate(_Strict):
    """G02: geographic facts of map scenes; require_land also fails a pin that is in the water."""
    require_land: bool = False


class AntiSlopGate(_Strict):
    """Anti-AI-slop rules (gates/anti_slop.py). Taste rules default to warn; a profile may raise a rule through
    `[gates.anti_slop.levels]` (and `[gates] require`, for the style-class ids, to stop a fail being lowered)."""
    level: Literal["warn", "fail"] = "warn"
    levels: dict[str, Literal["warn", "fail"]] = {}
    panel: bool = True                 # AS01: decorative side-strip / glass panels
    copy_tics: bool = Field(True, alias="copy")   # AS02: em-dash saturation, buzzwords, aphoristic cadence, "theater"
    motion: bool = True                # AS03: ease-in entrance, scale near 0, bounce figures
    read_time: bool = True             # AS04: minimum readable on-screen time for drawn text
    source_tells: bool = True          # AS05: gradient text, glass, side strips, stripes, glow, clip-path, kicker
    palette: bool = True               # AS06: violet/cyan tell hues in literal composition colours
    em_dash_max: int = Field(8, ge=1)
    em_dash_per_500: float = Field(1.0, gt=0)
    aphoristic_min: int = Field(3, ge=1)
    buzzwords: list[str] = ["streamline", "empower", "supercharge", "world-class", "enterprise-grade",
                            "next-generation", "cutting-edge", "seamless", "game-changing", "revolutionize"]
    chars_per_s: float = Field(20.0, gt=0)
    min_read_s: float = Field(0.8, ge=0)
    forbidden_hues: list[tuple[int, int]] = [(260, 310), (170, 197)]
    side_strip_px: float = Field(1.0, ge=0)
    clip_vertices: int = Field(6, ge=3)

    @model_validator(mode="after")
    def _v(self):
        bad = sorted(set(self.levels) - set(ANTI_SLOP_IDS))
        if bad:
            raise ValueError(f"gates.anti_slop.levels: unknown rule(s) {', '.join(bad)} "
                             f"(known: {', '.join(ANTI_SLOP_IDS)})")
        for lo, hi in self.forbidden_hues:
            if not (0 <= lo < hi <= 360):
                raise ValueError("gates.anti_slop.forbidden_hues: each range needs 0 <= lo < hi <= 360")
        return self

    def level_for(self, rule: str) -> str:
        return self.levels.get(rule, self.level)


class StyleGates(_Strict):
    """Channel style gates; an absent table = gate off. `require`: style gates whose fail findings block a build
    (gates/severity.py); `advisory`: non-fatal technical gates lowered to warn for this profile."""
    require: list[str] = []
    advisory: list[str] = []
    hook: HookGate | None = None
    cuts: CutGate | None = None
    camera: CameraGate | None = None
    visual_type: VisualTypeGate | None = None
    novelty: NoveltyGate | None = None
    silence: SilenceGate | None = None
    keyword_return: KeywordReturnGate | None = None
    ending_keyword: EndingKeywordGate | None = None
    duration: DurationGate | None = None
    duration_exact: ExactDurationGate | None = None
    cta: CtaGate | None = None
    geo: GeoGate | None = None
    anti_slop: AntiSlopGate = AntiSlopGate()

    @model_validator(mode="after")
    def _req(self):
        bad = [g for g in self.require if g not in STYLE_GATE_IDS]
        if bad:
            raise ValueError(f"gates.require: unknown style gate(s) {bad} (known: {', '.join(STYLE_GATE_IDS)})")
        bad_adv = [g for g in self.advisory if g not in TECHNICAL_GATE_IDS or g in NEVER_ADVISORY]
        if bad_adv:
            raise ValueError(f"gates.advisory: gate(s) {bad_adv} cannot be advisory")
        return self


class DraftProfile(_Strict):
    min_seg_s: float = Field(gt=0)
    max_seg_s: float = Field(gt=0)
    rules: list[str] = []
    map_path: bool = False   # the model may give a map segment a multi-key camera path (map_path)

    @model_validator(mode="after")
    def _o(self):
        _order(self.min_seg_s, self.max_seg_s, "draft segment length")
        return self


class Theme(_Strict):
    """Renderer values (HyperFrames scene `theme`; part of the HyperFrames segment cache key)."""
    camera_drift_per_s: float = Field(ge=0, le=0.1)   # zoom growth per second after the last camera_path key
    camera_start_speed: float = Field(ge=0, le=1)      # globe and camera_path: first-key speed as a share of the first leg's mean
    camera_end_speed: float = Field(ge=0, le=1)        # globe and camera_path: last-key speed as a share of the last leg's mean
    whip_s: float = Field(ge=0.1, le=1.5)             # length of a whip move (ends on its key)
    whip_blur_px: float = Field(ge=0, le=40)          # peak motion blur (Gaussian deviation, px) at mid-whip
    region_fill_opacity: float = Field(gt=0, le=1)    # region highlight fill opacity
    region_edge: Literal["white", "glow"]              # region edge when the region sets none
    region_edge_px: float = Field(gt=0, le=20)        # on-screen edge width (constant while the camera zooms)
    region_glow_px: float = Field(ge=0, le=60)        # glow radius of a "glow" edge
    region_glow_alpha: float = Field(ge=0, le=1)      # glow strength of a "glow" edge (1 = full entity colour)
    route_colour: str                                 # route line and arrowhead colour
    route_width_px: float = Field(gt=0, le=30)        # on-screen line width
    route_dash_px: float = Field(gt=0, le=120)        # on-screen dash length
    route_gap_px: float = Field(gt=0, le=120)         # on-screen gap length
    route_arrow_px: float = Field(gt=0, le=120)       # on-screen arrowhead length
    route_draw_s: float = Field(ge=0.2, le=8.0)       # draw duration when the route sets none
    title_font: Literal["inter", "title"]             # big title and number font (title = Archivo Black)
    title_px: float = Field(ge=40, le=200)            # big title size
    title_glow_px: float = Field(ge=0, le=80)         # glow radius of the big title (entity colour)
    title_tint: float = Field(ge=0, le=0.6)           # big title colour = entity colour mixed this far toward white
    text_outline_px: float = Field(ge=0, le=6)        # outline of teardrop labels and titles (0 = no outline, plain shadow)
    text_outline_colour: str                          # outline colour (CSS variable --txt-outline)
    text_outline_alpha: float = Field(ge=0, le=1)
    text_shadow_dy_px: float = Field(ge=0, le=12)     # drop shadow under outlined text (CSS variable --txt-shadow)
    text_shadow_blur_px: float = Field(ge=0, le=30)
    text_shadow_alpha: float = Field(ge=0, le=1)
    figure_px: float = Field(ge=40, le=320)           # year / number size
    counter_s: float = Field(ge=0.3, le=6.0)          # counter duration when the figure sets none
    number_group: Literal[".", ",", " ", ""]          # thousands separator of numbers
    number_decimal: Literal[",", "."]                 # decimal separator of numbers
    pin_style: Literal["dot", "teardrop"]             # dot + ring + boxed label above, or teardrop + label beside
    pin_colour: str                                   # dot / teardrop / ring colour (CSS variable --pin)
    label_font: Literal["inter", "script"]            # teardrop pin label font (script = Kaushan Script)
    pin_label_px: float = Field(ge=20, le=120)        # teardrop pin label size (px, constant on screen)
    region_style: Literal["tint", "block"] = "tint"   # region style when the region sets none
    block_l_min: float = Field(0.55, ge=0.3, le=0.9)  # block colour: entity colour with OKLCH L clamped to these
    block_l_max: float = Field(0.74, ge=0.3, le=0.9)
    block_chroma: float = Field(0.15, ge=0, le=0.3)   # ... and chroma raised to at least this (gamut-mapped)
    block_fill_alpha: float = Field(0.9, gt=0, le=1)  # block face opacity
    block_side_dl: float = Field(0.18, ge=0, le=0.5)  # extruded side = block colour this much darker (OKLCH L)
    block_depth_px: float = Field(7.0, ge=0, le=40)   # side offset below the face (on-screen px, map-plane down)
    block_shadow_px: float = Field(14.0, ge=0, le=60)  # drop shadow offset (down) and blur, on-screen px
    block_shadow_blur_px: float = Field(10.0, ge=0, le=60)
    block_shadow_alpha: float = Field(0.5, ge=0, le=1)
    block_bevel_px: float = Field(3.0, ge=0, le=20)   # inner highlight band along the edge
    block_bevel_tint: float = Field(0.4, ge=0, le=1)  # highlight = block colour mixed this far toward white
    block_edge_colour: str = "#ffffff"                # neon edge when the region sets none
    block_edge_px: float = Field(3.0, gt=0, le=20)
    block_glow_px: float = Field(9.0, ge=0, le=60)    # neon glow radius of block edges and borders
    block_casing_px: float = Field(1.25, ge=0, le=6)  # dark casing per side under the neon edge (legibility.line_casing)
    block_draw_s: float = Field(0.9, ge=0, le=8)      # edge draw-on length when the region sets none
    block_change_s: float = Field(0.7, ge=0.05, le=4)  # colour crossfade of a region change
    border_px: float = Field(4.0, gt=0, le=20)        # border line width, dash and gap (on-screen px)
    border_dash_px: float = Field(18.0, gt=0, le=120)
    border_gap_px: float = Field(12.0, gt=0, le=120)
    border_draw_s: float = Field(1.2, ge=0, le=8)     # border draw-on length when the border sets none
    label_style: Literal["plain", "box"] = "plain"   # box: solid box labels (cards, region labels, pin labels)
    label_box_colour: str = "#15181d"                 # box of labels without an entity
    label_box_px: float = Field(44.0, ge=20, le=120)  # region and pin box label size
    label_box_radius_px: float = Field(3.0, ge=0, le=12)   # at 1080 px frame width
    label_box_lc: float = Field(60.0, ge=30, le=108)  # entity boxes darken until white text reaches this APCA |Lc|
    label_pop_s: float = Field(0.6, ge=0.1, le=2)     # elastic pop-in of box labels
    year_box_colour: str | None = None                # label_style box: year figures on this box (None = like figures)
    year_box_ink: str = "#ffffff"                     # year text on year_box_colour
    year_box_px: float | None = Field(None, ge=20, le=320)   # year size on year_box_colour (None = figure_px)
    year_box_texture: str | None = None               # vendor/textures file multiplied over year_box_colour
    entities: dict[str, str] = {}                     # entity -> "#rrggbb" (resolved into the scene, not in `theme`)
    palette: list[str] = Field(min_length=1)          # colours for entities missing from `entities`, in order

    @model_validator(mode="after")
    def _hex(self):
        if self.block_l_min > self.block_l_max:
            raise ValueError("theme block_l_min must be <= block_l_max")
        for c in [*self.entities.values(), *self.palette, self.pin_colour, self.route_colour, self.text_outline_colour,
                  self.block_edge_colour, self.label_box_colour, self.year_box_ink,
                  *([self.year_box_colour] if self.year_box_colour else [])]:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                raise ValueError(f"theme colour {c!r} must be #rrggbb")
        if self.year_box_texture and not self.year_box_colour:
            raise ValueError("theme year_box_texture needs year_box_colour")
        return self


BEZIER_PULSE = (0.3, 0.0, 0.15, 1.0)
MOTION_FPS = 30.0   # the still-frame rate the duration tokens are expressed at


class MotionProfile(_Strict):
    """Named motion tokens (Emil Kowalski, translated to video frames at 30 fps). `entrance`, `move` and `exit` are
    GSAP ease names; the default role mapping keeps the engine look, so a profile adopts the Emil curves by name.
    `micro_frames` (140 ms), `overlay_frames` (220 ms), `panel_frames` (400 ms) and `stagger_frames` (50 ms) are
    exposed to the renderer as seconds at 30 fps, independent of the output frame rate."""
    ease_out: tuple[float, float, float, float] = (0.23, 1.0, 0.32, 1.0)
    ease_in_out: tuple[float, float, float, float] = (0.77, 0.0, 0.175, 1.0)
    ease_drawer: tuple[float, float, float, float] = (0.32, 0.72, 0.0, 1.0)
    micro_frames: int = Field(4, ge=1)
    overlay_frames: int = Field(7, ge=1)
    overlay_frames_range: tuple[int, int] | None = None   # optional profile guard: overlay_frames within [lo, hi]
    panel_frames: int = Field(12, ge=1)
    stagger_frames: int = Field(2, ge=0)
    entrance: str = "power2.out"
    move: str = "power2.inOut"
    exit: str = "power2.in"
    entrance_scale: float = Field(0.96, gt=0, le=1)
    overlay_fade_s: float = Field(0.20, ge=0, le=2)

    @model_validator(mode="after")
    def _m(self):
        for name in ("ease_out", "ease_in_out", "ease_drawer"):
            x1, _, x2, _ = getattr(self, name)
            if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
                raise ValueError(f"motion.{name}: x1 and x2 must be in 0..1")
        if self.micro_frames > self.overlay_frames or self.overlay_frames > self.panel_frames:
            raise ValueError("motion: duration tokens must grow micro <= overlay <= panel")
        r = self.overlay_frames_range
        if r is not None:
            if len(r) != 2 or r[0] > r[1] or r[0] < 1:
                raise ValueError("motion.overlay_frames_range must be [lo, hi] with 1 <= lo <= hi")
            if not r[0] <= self.overlay_frames <= r[1]:
                raise ValueError(f"motion.overlay_frames {self.overlay_frames} outside its declared range {r[0]}..{r[1]}")
        for name in ("entrance", "move", "exit"):
            if not getattr(self, name).strip():
                raise ValueError(f"motion.{name} must name an ease")
        return self

    def seconds(self) -> dict:
        return {"micro": self.micro_frames / MOTION_FPS, "overlay": self.overlay_frames / MOTION_FPS,
                "panel": self.panel_frames / MOTION_FPS, "stagger": self.stagger_frames / MOTION_FPS}

    def render_values(self) -> dict:
        s = self.seconds()
        return {"ease_out": list(self.ease_out), "ease_in_out": list(self.ease_in_out),
                "ease_drawer": list(self.ease_drawer), "micro_s": s["micro"], "overlay_s": s["overlay"],
                "panel_s": s["panel"], "stagger_s": s["stagger"], "entrance": self.entrance,
                "move": self.move, "exit": self.exit, "entrance_scale": self.entrance_scale}


class CameraProfile(_Strict):
    """HyperFrames camera_path pulses: inside every drift (the camera waiting on a view), fast pulses away from the
    base view (`pulse_direction` in = push in, out = pull out) about `pulse_anchor`, alternating with pulses back,
    with drift between them. pulse_zoom 0 = no pulses (the drift alone). `max_zoom`: the largest camera zoom over the
    cover fit any camera key or layout fit may use (the map derivative and its deep tiles follow it)."""
    pulse_zoom: float = Field(0.0, ge=0, le=1.0)        # ln zoom change of one pulse (out, then back in)
    pulse_s: float = Field(0.8, ge=0.2, le=2.0)         # pulse length
    pulse_gap_s: float = Field(0.6, ge=0.1, le=5.0)     # minimum drift before, between and after pulses
    pulse_pan: float = Field(0.0, ge=0, le=0.3)         # frame-centre shift of a pulse away (frame widths, alternating sides)
    pulse_ease: tuple[float, float, float, float] = BEZIER_PULSE   # CSS cubic-bezier(x1, y1, x2, y2) of a pulse
    pulse_anchor: tuple[float, float] = (0.5, 0.5)     # screen point (frame fractions) that stays put in a pulse
    pulse_direction: Literal["in", "out"] = "out"       # first pulse of a pair: push in or pull out
    max_zoom: float = Field(4.0, ge=1.0, le=64.0)       # largest camera zoom over the cover fit (keys, layout fit)

    @model_validator(mode="after")
    def _b(self):
        x1, _, x2, _ = self.pulse_ease
        if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
            raise ValueError("camera.pulse_ease: x1 and x2 must be in 0..1")
        if not all(0 <= v <= 1 for v in self.pulse_anchor):
            raise ValueError("camera.pulse_anchor: frame fractions must be in 0..1")
        return self

    def scene(self) -> dict | None:
        """Pulse values for camera_plan (HyperFrames scene theme key `camera_pulse`); None without pulses."""
        if self.pulse_zoom <= 0:
            return None
        return {"zoom": self.pulse_zoom, "s": self.pulse_s, "gap_s": self.pulse_gap_s, "pan": self.pulse_pan,
                "ease": list(self.pulse_ease), "anchor": list(self.pulse_anchor),
                "dir": 1 if self.pulse_direction == "in" else -1}


TRANSITION_KINDS = ("cut", "crossfade", "zoom_whip", "page_turn", "state")
TransitionKind = Literal["cut", "crossfade", "zoom_whip", "page_turn", "state"]


class TransitionsProfile(_Strict):
    """Segment-boundary transitions drawn by the final mux (video_engine/transitions.py). A boundary takes
    same_type[type] when both sides share that visual type, else by_type[incoming visual type], else
    by_type[outgoing visual type], else `kind`. `page_turn` false: no boundary may take page_turn. `map_morph`
    false: no map segment may carry a morph (T02 fail)."""
    kind: TransitionKind = "cut"
    by_type: dict[str, TransitionKind] = {}
    same_type: dict[str, TransitionKind] = {}   # both sides this visual type: before by_type
    fade_s: float = Field(0.4, ge=0.1, le=1.0)          # crossfade: dissolve length, centred on the boundary
    whip_out_s: float = Field(0.1, ge=0.03, le=1.0)     # zoom_whip: outgoing last frames push in to whip_scale, blur rising
    whip_blur_in_s: float = Field(0.1, ge=0.0, le=1.0)  # zoom_whip: radial blur fading out on the incoming first frames
    whip_in_s: float = Field(0.4, ge=0.05, le=1.5)      # zoom_whip: incoming shot settles from whip_scale to 1
    whip_scale: float = Field(1.3, ge=1.0, le=2.0)      # zoom_whip: outgoing end scale and incoming start scale
    whip_blur: float = Field(0.12, ge=0.0, le=0.5)      # zoom_whip: peak radial blur length (fraction of the scale)
    page_s: float = Field(0.4, ge=0.1, le=1.5)          # page_turn: the outgoing last frame folds away over the incoming
    state_s: float = Field(0.2, ge=0.1, le=0.4)         # state: very short dissolve when both sides share the same base map
    page_turn: bool = True                              # false: page_turn is not allowed anywhere in this profile
    map_morph: bool = True                              # false: map segments may not morph to a second (paper) map

    @model_validator(mode="after")
    def _t(self):
        from typing import get_args
        from .timeline import VisualType
        known = get_args(VisualType)
        bad = sorted((set(self.by_type) | set(self.same_type)) - set(known))
        if bad:
            raise ValueError(f"transitions.by_type / same_type: unknown visual type(s) {', '.join(bad)} (known: {', '.join(known)})")
        if self.whip_blur_in_s > self.whip_in_s:
            raise ValueError("transitions.whip_blur_in_s must be <= whip_in_s")
        if not self.page_turn and "page_turn" in self._kinds():
            raise ValueError("transitions: page_turn = false, but kind / by_type / same_type use page_turn")
        return self

    def _kinds(self) -> list[str]:
        return [self.kind, *self.by_type.values(), *self.same_type.values()]

    def active(self) -> bool:
        return any(v != "cut" for v in self._kinds())


X264_TUNES = Literal["none", "fastdecode", "film", "animation", "grain", "stillimage"]   # none = no -tune
X264_PRESETS = Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]


class RenderProfile(_Strict):
    """Encode and capture quality; every key absent (None) = the engine default. Intermediates (`segment_*`) = every
    segment file (native renders, grades, HyperFrames normalisation); `final_*` = the one composite pass (x264 CRF,
    preset, tune "none" = no -tune, optional VBV cap); `hf_*` = the HyperFrames capture and its own encode
    (hyperframes.HF_QUALITY / HF_CRF; draft: JPEG q80 screenshots + x264 ultrafast, standard: q100 + medium, high:
    q100 + slow); `map_jpeg_quality` = whole-map derivatives and deep tiles (maps.MAP_JPEG_QUALITY)."""
    hf_quality: Literal["draft", "standard", "high"] | None = None
    hf_crf: int | None = Field(None, ge=0, le=51)
    segment_crf: int | None = Field(None, ge=0, le=51)
    segment_preset: X264_PRESETS | None = None
    segment_tune: X264_TUNES | None = None
    final_crf: int | None = Field(None, ge=0, le=51)
    final_preset: X264_PRESETS | None = None
    final_tune: X264_TUNES | None = None
    final_maxrate_kbps: int | None = Field(None, ge=1000, le=200000)
    map_jpeg_quality: int | None = Field(None, ge=50, le=100)

    def x264(self, stage: Literal["segment", "final"]) -> list[str]:
        """Encoder arguments of a stage: the stage default (config.X264_SEGMENT / config.X264) when the stage sets
        nothing, else config.x264 with the stage's values over that stage's default CRF and preset."""
        from . import config
        seg = stage == "segment"
        crf, preset, tune, cap = ((self.segment_crf, self.segment_preset, self.segment_tune, None) if seg
                                  else (self.final_crf, self.final_preset, self.final_tune, self.final_maxrate_kbps))
        if (crf, preset, tune, cap) == (None, None, None, None):
            return list(config.X264_SEGMENT if seg else config.X264)
        d_crf, d_preset = (config.SEGMENT_CRF, config.SEGMENT_PRESET) if seg else (config.FINAL_CRF, config.FINAL_PRESET)
        return config.x264(d_crf if crf is None else crf, preset or d_preset,
                           None if tune in (None, "none") else tune, cap)


class Legibility(_Strict):
    """Adaptive map text (teardrop labels, title, years, counters, area labels) and map overlay style; absent = fixed
    theme colours, R64 off. Keys with a default may be omitted (area labels, water ink, halo bands, counters, region
    and leader casings, R64 lines). Lc = APCA-W3 0.1.9 lightness contrast; Y = APCA screen luminance (0-1); OKLCH
    L 0-1, C, hue in degrees."""
    # measurement
    samples: int = Field(ge=2, le=64)                         # background samples per element life
    measure_scale: float = Field(ge=0.1, le=1)                # labels, years, counters; titles at full resolution
    dark_percentile: float = Field(ge=0, le=50)               # hard-case Y percentile for dark ink
    light_percentile: float = Field(ge=50, le=100)            # hard-case Y percentile for light text
    adjacent_px: float = Field(ge=1, le=6)                    # ring around the glyphs for the darkest-neighbour check
    adjacent_percentile: float = Field(ge=0, le=10)           # darkest (dark ink) / lightest (light text) neighbours
    # polarity
    light_p50_max: float = Field(ge=0.05, le=0.4)             # light text only when the background p50 Y is below
    # APCA Lc targets at the hard-case percentile
    lc_title: float = Field(ge=15, le=108)
    lc_title_aim: float = Field(ge=15, le=108)
    lc_figure: float = Field(ge=15, le=108)
    lc_label: float = Field(ge=15, le=108)
    lc_area: float = Field(default=60, ge=15, le=108)                 # area labels (region names, water names)
    lc_heading: float = Field(default=60, ge=15, le=108)              # [print] date headings
    lc_note: float = Field(default=60, ge=15, le=108)                 # [print] hand annotations
    lc_floor: float = Field(ge=0, le=60)                      # darkest-neighbour minimum
    lc_fail_margin: float = Field(ge=0, le=30)                # R64 fails below target - margin
    wcag_label: float = Field(ge=1, le=21)                    # WCAG backup (warn below)
    wcag_title: float = Field(ge=1, le=21)
    # ink (OKLCH)
    ink_l: float = Field(ge=0.18, le=0.30)
    ink_c: float = Field(ge=0, le=0.04)
    ink_hue: float = Field(ge=50, le=70)
    ink_l_min: float = Field(ge=0.1, le=0.3)                  # darkest ink after escalation
    ink_l_step: float = Field(gt=0, le=0.1)                   # escalation step 3
    entity_text_l_max: float = Field(ge=0.2, le=0.45)         # text in an entity colour: OKLCH L capped here
    entity_text_l_min: float = Field(default=0.0, ge=0, le=0.45)      # ... and raised to at least this
    entity_text_c_scale: float = Field(default=1.0, ge=0.5, le=1)     # ... chroma x this
    text_fallback: str                                        # text colour for entities in never_text (umber)
    never_text: list[str] = []                                # entity colours never used as text
    light_l: float = Field(ge=0.85, le=0.98)                  # light text (OKLCH)
    light_c: float = Field(ge=0, le=0.05)
    light_hue: float = Field(ge=0, le=360)
    water_ink_l: float = Field(default=0.34, ge=0.25, le=0.45)        # water labels: blue italic ink (OKLCH)
    water_ink_c: float = Field(default=0.07, ge=0, le=0.15)
    water_ink_hue: float = Field(default=235, ge=180, le=280)
    # halo (dark ink only): paper-coloured band around the glyphs, enclosed counters left open
    halo_width_em: float = Field(ge=0.06, le=0.10)            # titles, years, counters
    label_halo_width_em: float = Field(ge=0.06, le=0.10)      # script labels
    halo_l_min: float = Field(ge=0.85, le=0.97)               # halo = background p50 with L raised to >= this
    halo_l_max: float = Field(ge=0.85, le=0.99)               # never pure white
    halo_c_scale: float = Field(ge=0, le=1)
    halo_alpha: float = Field(ge=0, le=0.85)                  # 0 = no halo (printed ink)
    halo_alpha_max: float = Field(ge=0, le=0.85)              # escalation step 1
    halo_width_min_em: float = Field(default=0.06, ge=0.06, le=0.10)  # R64: painted halo width range (x font size)
    halo_width_max_em: float = Field(default=0.10, ge=0.06, le=0.10)
    halo_blur_ratio: float = Field(default=0.25, ge=0, le=0.5)        # Gaussian blur sd = this x halo width
    halo_blur_max_ratio: float = Field(default=0.5, ge=0, le=0.5)     # R64: painted blur <= this x painted width
    counter_min_px: int = Field(default=6, ge=1, le=64)               # R64 counters: loops of >= this area (px)
    counter_diff_max: float = Field(default=16, ge=1, le=64)          # counter px open when it differs <= this
    counter_open_min: float = Field(default=0.9, ge=0.5, le=1)        # fail below this open fraction
    # light text only
    glow_px: float = Field(ge=0, le=4)
    glow_colour: Literal["ink", "entity"] = "ink"           # entity = glow in the element's entity colour, none without one
    glow_alpha: float = Field(ge=0, le=0.25)
    shadow_dy_px: float = Field(ge=0, le=2)
    shadow_blur_px: float = Field(ge=3, le=6)
    shadow_alpha: float = Field(ge=0, le=0.35)
    shadow_colour: str
    # light text escalation (plain labels on imagery): after the glow + shadow rung, a dark glyph halo (same geometry
    # as the dark-ink halo) at light_halo_alpha_min..max in light_halo_steps rungs; max 0 = off (one light style)
    light_halo_alpha_min: float = Field(default=0.0, ge=0, le=0.85)
    light_halo_alpha_max: float = Field(default=0.0, ge=0, le=0.85)
    light_halo_steps: int = Field(default=3, ge=1, le=8)
    light_halo_colour: str | None = None                      # None = shadow_colour
    light_halo_ink: str | None = None    # last light rung: this ink at light_halo_alpha_max (None = no extra rung)
    # escalation step 2: soft radial paper vignette
    vignette_scale: float = Field(ge=1.3, le=1.5)             # ellipse radii = scale x the half box diagonal axes
    vignette_feather: float = Field(gt=0, le=0.6)             # feather band = this x box height
    vignette_alpha_min: float = Field(ge=0.1, le=0.70)
    vignette_alpha_max: float = Field(ge=0.1, le=0.70)
    vignette_steps: int = Field(ge=1, le=8)
    # escalation order of dark ink: halo -> vignette -> darker ink, or darker ink -> vignette (printed ink, no halo)
    ladder_order: Literal["halo_first", "ink_first"] = "halo_first"
    polarity_fallback: bool = True                            # try the other polarity when the rule polarity fails
    # stability (fallback when one style cannot pass the whole interval)
    hold_frames: int = Field(ge=1, le=60)
    crossfade_ms: float = Field(ge=0, le=1000)
    ema_tau_ms: float = Field(ge=0, le=2000)
    # overlays
    basemap_chroma: float = Field(ge=0.6, le=0.9)             # map saturation while regions or routes are on screen
    entity_fill_alpha: float = Field(ge=0.2, le=0.36)
    entity_outline_px: float = Field(ge=2, le=4)
    pin_outline_px: float = Field(ge=2, le=3)
    paper_colour: str                                         # pin outline
    route_casing_px: float = Field(ge=1.5, le=2)
    route_casing_colour: str
    region_casing_px: float = Field(default=1.0, ge=0, le=2)          # region outline casing per side (line_casing); 0 = none
    leader_casing_px: float = Field(default=1.75, ge=0, le=2)         # leader casing: label halo / dark ink; 0 = none
    # R64 lines: pins, routes, region outlines, leaders against the map around them
    lc_line: float = Field(default=30, ge=15, le=108)                 # pass at or above
    lc_line_fail: float = Field(default=25, ge=0, le=108)             # fail below; warn in between
    line_ring_px: float = Field(default=3, ge=1, le=8)                # background ring around the painted line
    line_percentile: float = Field(default=10, ge=0, le=50)           # hard-case percentile of the ring Lc
    line_mask_diff: float = Field(default=24, ge=1, le=128)           # line-mask px differing > this from white

    @model_validator(mode="after")
    def _v(self):
        for c in [self.text_fallback, self.shadow_colour, self.paper_colour, self.route_casing_colour, *self.never_text,
                  *([self.light_halo_colour] if self.light_halo_colour else []),
                  *([self.light_halo_ink] if self.light_halo_ink else [])]:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                raise ValueError(f"legibility colour {c!r} must be #rrggbb")
        for lo, hi in [("halo_alpha", "halo_alpha_max"), ("vignette_alpha_min", "vignette_alpha_max"),
                       ("halo_l_min", "halo_l_max"), ("ink_l_min", "ink_l"), ("lc_title", "lc_title_aim"),
                       ("halo_width_min_em", "halo_width_em"), ("halo_width_min_em", "label_halo_width_em"),
                       ("halo_width_em", "halo_width_max_em"), ("label_halo_width_em", "halo_width_max_em"),
                       ("halo_blur_ratio", "halo_blur_max_ratio"), ("lc_line_fail", "lc_line"),
                       ("light_halo_alpha_min", "light_halo_alpha_max")]:
            if getattr(self, lo) > getattr(self, hi):
                raise ValueError(f"legibility.{lo} must not exceed legibility.{hi}")
        return self


class Watermark(_Strict):
    """Channel text mark burned into the final video (top right, inside the safe area) by the final mux."""
    text: str = Field(min_length=1, max_length=40)
    font: Literal["inter", "title"] = "inter"          # inter = Inter Bold, title = Archivo Black
    px: float = Field(36, ge=16, le=120)                # font size, px at 1920 height
    colour: str = "#ffffff"
    opacity: float = Field(0.85, gt=0, le=1)
    shadow: bool = True                                 # soft dark shadow under the text

    @model_validator(mode="after")
    def _hex(self):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.colour):
            raise ValueError(f"watermark colour {self.colour!r} must be #rrggbb")
        if self.text != self.text.strip() or "\n" in self.text:
            raise ValueError("watermark text must be one line without leading or trailing spaces")
        return self


SOUND_EVENTS = ("pin", "region", "route", "whip", "label", "title", "figure", "counter_tick")


class PrintStyle(_Strict):
    """Profile [print]: printed typography. On paper maps (map p50 APCA Y >= paper_min_y) map text is dark ink
    printed into the map (multiply, no halo, no glow, no box); on dark imagery it is light text with a soft shadow.
    Lengths are px at 1080 x 1920. Absent = the theme typography."""
    paper_min_y: float = Field(0.3, ge=0.05, le=0.9)          # map p50 APCA Y at or above this = paper surface
    max_typefaces: int = Field(2, ge=1, le=4)                 # T01: typefaces per frame (caption and watermark excluded)
    watermark_clear_px: float = Field(32, ge=0, le=200)       # B01: element boxes keep this far from the watermark text
    ink: str = "#2A231D"                                      # paper ink: pin labels, years, the time under a year
    # map grade (paper surface)
    vignette_alpha: float = Field(0.10, ge=0, le=0.12)        # frame vignette (replaces the theme shade)
    grade_saturate: float = Field(0.88, ge=0.85, le=0.9)
    grade_contrast: float = Field(0.78, ge=0.75, le=0.8)
    paper_texture: str = "paper-F1E8D4.jpg"                   # vendor/textures file, multiply over the map
    paper_alpha: float = Field(0.5, ge=0, le=1)
    grain: str = "grain-512.png"                              # vendor/textures file, multiply (2.5 % mean darkening)
    grain_alpha: float = Field(1.0, ge=0, le=1)
    # regions
    region_multiply: bool = True                              # paper: fill and edge multiply; dark imagery: normal
    region_edge_l: float = Field(0.35, ge=0.2, le=0.5)        # solid edge = entity colour at this OKLCH L
    region_edge_l_dark: float = Field(0.75, ge=0.5, le=0.95)  # ... on a dark surface (satellite)
    region_edge_alpha: float = Field(0.85, ge=0.5, le=1)
    # entity title and place names inside their region (EB Garamond caps on paper, Inter Bold on dark imagery);
    # the title sizes apply to both
    title_px: float = Field(88, ge=84, le=96)
    title_px_min: float = Field(64, ge=40, le=96)             # shrink floor for the width rule
    title_width_max: float = Field(0.55, gt=0, le=1)          # box width <= this x the region's screen box width
    title_rot_max: float = Field(8, ge=0, le=15)              # |rotation| <= this (deg)
    title_tracking: float = Field(0.12, ge=0, le=0.3)         # em (paper)
    title_tracking_in: float = Field(0.30, ge=0, le=0.5)      # em at the start of the entry
    title_weight: int = Field(600, ge=400, le=800)
    title_in_s: float = Field(0.35, ge=0.1, le=1)             # entry: opacity 0 -> 1, tracking in -> final (easeOutCubic)
    dark_tracking: float = Field(0.04, ge=0, le=0.3)          # em (dark imagery)
    emboss_colour: str = "#FAF5E8"                            # paper emboss: a copy under the ink, offset down
    emboss_alpha: float = Field(0.40, ge=0, le=1)
    emboss_dy: float = Field(2, ge=0, le=6)
    # year standing alone, centred
    year_cx: float = Field(540, ge=0, le=1080)
    year_cy: float = Field(560, ge=520, le=600)               # ink centre y
    year_width: float = Field(0.38, ge=0.35, le=0.40)         # ink width / frame width (within the px range)
    year_px_min: float = Field(220, ge=120, le=400)
    year_px_max: float = Field(250, ge=120, le=400)
    year_weight: int = Field(450, ge=400, le=450)
    year_dark_weight: int = Field(700, ge=400, le=900)
    year_dark_colour: str = "#F7F2E6"
    time_px: float = Field(36, ge=24, le=60)                  # optional time under the year (Cinzel)
    time_tracking: float = Field(0.14, ge=0, le=0.3)
    time_alpha: float = Field(0.75, ge=0.3, le=1)
    time_gap: float = Field(24, ge=0, le=80)
    # date heading (cards): Cinzel caps between two rules with diamonds
    heading_px: float = Field(42, ge=40, le=44)
    heading_tracking: float = Field(0.14, ge=0, le=0.3)
    heading_weight: int = Field(600, ge=400, le=900)
    heading_baseline_y: float = Field(384, ge=360, le=400)
    heading_ink: str = "#5B3A23"                              # light backgrounds
    heading_light: str = "#F3EAD8"                            # photos and dark backgrounds (shadow 0 2px 6px)
    heading_shadow_alpha: float = Field(0.40, ge=0, le=0.6)
    rule_px: float = Field(1.5, gt=0, le=4)
    rule_len: float = Field(120, ge=40, le=240)
    rule_gap: float = Field(16, ge=0, le=60)                  # text to diamond, diamond to rule
    diamond_px: float = Field(7, ge=3, le=16)
    heading_fade_s: float = Field(0.3, ge=0, le=2)
    rule_draw_s: float = Field(0.4, ge=0, le=2)
    # pin labels above the pin on a leader
    label_px: float = Field(54, ge=52, le=56)                 # paper: EB Garamond Italic
    label_weight: int = Field(600, ge=400, le=800)
    label_dark_px: float = Field(48, ge=40, le=60)            # dark imagery: Inter Bold
    leader_len: float = Field(48, ge=40, le=60)
    leader_gap: float = Field(12, ge=0, le=24)                # head to leader and leader to label
    # hand annotations
    note_px: float = Field(56, ge=40, le=72)
    note_ink: str = "#2E4E7A"                                 # paper
    note_light: str = "#FFFFFF"                               # dark imagery
    note_write_s: float = Field(0.5, ge=0.2, le=2)

    @model_validator(mode="after")
    def _hex(self):
        for c in (self.ink, self.emboss_colour, self.year_dark_colour, self.heading_ink, self.heading_light, self.note_ink,
                  self.note_light):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                raise ValueError(f"print colour {c!r} must be #rrggbb")
        if self.title_px_min > self.title_px:
            raise ValueError("print.title_px_min must not exceed print.title_px")
        if self.year_px_min > self.year_px_max:
            raise ValueError("print.year_px_min must not exceed print.year_px_max")
        return self


class SoundKind(_Strict):
    """Mix settings of one SFX kind: gain and how a cue sits on its event."""
    gain_db: float = Field(ge=-60, le=0)
    align: Literal["onset", "peak"] = "onset"         # onset = file start on the event; peak = loudest sample on it
    max_s: float = Field(2.0, gt=0, le=10)            # cue length cap (short fade-out at the cap)


class SoundProfile(_Strict):
    """Automatic SFX cues from render events and keyframed music ducking under the voiceover."""
    duck_db: float = Field(-12.0, ge=-40, lt=0)              # music gain while the voiceover speaks
    duck_attack_s: float = Field(0.15, ge=0.01, le=2)       # ramp down before the first word of a phrase
    duck_release_s: float = Field(0.4, ge=0.01, le=3)      # ramp back up after the last word
    duck_merge_s: float = Field(0.5, ge=0, le=3)           # word gaps up to this long stay ducked
    fade_in_s: float = Field(0.3, ge=0, le=5)
    fade_out_s: float = Field(1.0, ge=0, le=5)
    events: dict[str, str]                            # render event -> SFX kind
    kinds: dict[str, SoundKind]

    @model_validator(mode="after")
    def _map(self):
        for ev, kind in self.events.items():
            if ev not in SOUND_EVENTS:
                raise ValueError(f"sound.events: unknown event '{ev}' (known: {', '.join(SOUND_EVENTS)})")
            if kind not in self.kinds:
                raise ValueError(f"sound.events.{ev} = '{kind}' has no [sound.kinds.{kind}]")
        from .timeline import SFX_KINDS
        for kind in self.kinds:
            if kind not in SFX_KINDS:
                raise ValueError(f"sound kind '{kind}' unknown (known: {', '.join(SFX_KINDS)})")
        return self


class Resources(_Strict):
    """[resources]: shared worker budget of a build (video_engine/resources.py). Machine setting: not in the hash."""
    workers: int = Field(0, ge=0, le=64)    # 0 = auto from CPU count and RAM


class FlagsProfile(_Strict):
    alpha: float = Field(0.85, ge=0, le=1)
    reveal_s: float = Field(0.45, ge=0.2, le=1.0)
    edge_px: float = Field(3, ge=2, le=7)
    glow_px: float = Field(6, ge=3, le=14)
    # auto: per region, cover unless cropping would cut more than auto_max_loss of the flag (narrow/tall regions
    # such as Myanmar on the globe), then contain (hyperframes.flag_fit). A flag's own `fit` still wins.
    fit: Literal["auto", "cover", "contain", "stretch"] = "auto"
    auto_max_loss: float = Field(0.35, ge=0, le=0.9)
    pin_height_pct: float = Field(4.5, ge=3, le=7)


class FxGrid(_Strict):
    colour: str = "#2F6FD6"
    bg: str = "#0B2A5B"
    cell_pct: float = Field(4.0, gt=0, le=50)       # cell size as a share of the frame height
    line_px: float = Field(2, ge=1, le=20)          # at 1080 wide
    alpha: float = Field(0.5, ge=0, le=1)
    graticule_spacing_deg: float = Field(30, ge=5, le=90)
    graticule_colour: str = "#FFFFFF"                # light lines: the grid blue disappears on dark ocean
    graticule_alpha: float = Field(0.35, ge=0, le=1)


class FxStars(_Strict):
    count: int = Field(400, ge=0, le=5000)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)   # None = project id + segment index
    twinkle: float = Field(0.15, ge=0, le=1)
    parallax: float = Field(0.2, ge=0, le=1)


class FxHalo(_Strict):
    colour: str = "#7EC8FF"
    width_pct: float = Field(3.0, gt=0, le=20)
    alpha: float = Field(0.6, ge=0, le=1)


class FxTimezone(_Strict):
    alpha: float = Field(0.25, ge=0, le=1)
    at_frame: int = Field(0, ge=0)
    colour: str = "#7EC8FF"                          # band colour when the band sets none


class FxHatch(_Strict):
    angle: float = Field(45, ge=0, le=180)
    spacing_px: float = Field(10, ge=2, le=100)
    line_px: float = Field(2, ge=1, le=20)
    colour: str = "#FFFFFF"
    alpha: float = Field(0.6, ge=0, le=1)


class FxBubble(_Strict):
    style: Literal["light", "dark"] = "light"
    font_px: float = Field(44, ge=20, le=96)         # at 1920 high
    pop_frames: int = Field(5, ge=1, le=15)          # overlay motion token
    max_width_pct: float = Field(0.62, ge=0.3, le=0.9)
    max_bottom_pct: float = Field(0.72, ge=0.5, le=0.9)   # never in the caption band
    gap_px: float = Field(28, ge=4, le=120)


class FxFlagCharacter(_Strict):
    """Flag-icon characters (P15): cloth wave, eyes and blink, soft shadow. Lengths are at 1920 px height."""
    wave_amplitude: float = Field(0.035, ge=0, le=0.12)   # share of the character height
    wave_hz: float = Field(0.8, gt=0, le=4.0)             # wave cycles per second
    slices: int = Field(14, ge=4, le=40)                  # vertical cloth strips
    eye_pct: float = Field(17, gt=0, le=40)               # eye diameter as a share of the character height
    blink_period_s: float = Field(3.2, gt=0.5, le=12.0)   # one blink per period
    blink_frames: int = Field(5, ge=1, le=20)
    shadow_px: float = Field(14, ge=0, le=60)


class FxCutaway(_Strict):
    """Cross-section diagram (P16)."""
    reveal_s: float = Field(0.6, ge=0.05, le=5.0)         # one layer reveal; layers stagger by 0.6 of it
    label_px: float = Field(38, ge=12, le=120)            # at 1920 px height
    line_px: float = Field(2, ge=1, le=10)
    height_ratio: float = Field(0.55, gt=0, le=1.5)       # section height / section width
    inset_border_px: float = Field(5, ge=1, le=20)        # thin white photo frame
    inset_gap_px: float = Field(28, ge=4, le=160)


class FxGeoid(_Strict):
    """Exaggerated Earth model (P18)."""
    exaggeration: float = Field(0.08, ge=0, le=0.5)
    colour_ramp: list[str] = Field(default_factory=lambda: ["#2B5BD7", "#3FB27F", "#F2D04B", "#D9483B"])
    rotate_deg_s: float = Field(12, ge=-360, le=360)
    harmonics: int = Field(6, ge=1, le=16)
    fade_s: float = Field(0.5, ge=0, le=3.0)


class FxParticles(_Strict):
    """Deterministic particle accents (P19)."""
    duration_s: float = Field(2.0, ge=0.2, le=10.0)
    max_count: int = Field(180, ge=12, le=600)            # per emitter
    gravity: float = Field(0.22, ge=0, le=1.0)            # fireworks: share of frame height per second^2
    fireworks_colours: list[str] = Field(default_factory=lambda: ["#FFD84A", "#FF7A3D", "#FF4D6D", "#7ED0FF", "#FFFFFF"])
    fire_colours: list[str] = Field(default_factory=lambda: ["#FFE07A", "#FF9A3D", "#E8452B"])


class FxProfile(_Strict):
    """[fx]: optional per-scene visual components (P12-P19). Absent scene fields keep the engine byte-identical."""
    grid: FxGrid = FxGrid()
    stars: FxStars = FxStars()
    halo: FxHalo = FxHalo()
    timezone: FxTimezone = FxTimezone()
    hatch: FxHatch = FxHatch()
    bubble: FxBubble = FxBubble()
    flag_character: FxFlagCharacter = FxFlagCharacter()
    cutaway: FxCutaway = FxCutaway()
    geoid: FxGeoid = FxGeoid()
    particles: FxParticles = FxParticles()


class ProfileData(_Strict):
    description: str = ""
    caption_style: dict = {}
    audio: AudioProfile
    draft: DraftProfile
    theme: Theme
    gates: StyleGates = StyleGates()
    watermark: Watermark | None = None
    sound: SoundProfile | None = None
    legibility: Legibility | None = None
    layout: LayoutProfile | None = None     # beats layout of map scenes (video_engine/layout.py); None = off
    print: PrintStyle | None = None         # printed typography (PrintStyle); None = theme typography
    camera: CameraProfile = CameraProfile()
    motion: MotionProfile = MotionProfile()
    transitions: TransitionsProfile = TransitionsProfile()
    render: RenderProfile = RenderProfile()   # encode / capture quality
    grade: Grade | None = None              # full-frame grade of every segment without its own (lut: profiles-relative)
    resources: Resources = Resources()
    flags: FlagsProfile = FlagsProfile()
    fx: FxProfile = FxProfile()


@dataclass(frozen=True)
class Profile:
    name: str
    data: ProfileData
    blake3: str          # hash of the merged (effective) profile

    @property
    def audio(self) -> AudioProfile:
        return self.data.audio

    @property
    def caption_style(self) -> dict:
        return self.data.caption_style

    @property
    def gates(self) -> StyleGates:
        return self.data.gates

    @property
    def draft(self) -> DraftProfile:
        return self.data.draft

    @property
    def theme(self) -> Theme:
        return self.data.theme

    @property
    def watermark(self) -> Watermark | None:
        return self.data.watermark

    @property
    def sound(self) -> SoundProfile | None:
        return self.data.sound

    @property
    def legibility(self) -> Legibility | None:
        return self.data.legibility

    @property
    def layout(self) -> LayoutProfile | None:
        return self.data.layout

    @property
    def print(self) -> PrintStyle | None:
        return self.data.print

    @property
    def camera(self) -> CameraProfile:
        return self.data.camera

    @property
    def motion(self) -> MotionProfile:
        return self.data.motion

    @property
    def grade(self) -> Grade | None:
        return self.data.grade

    @property
    def transitions(self) -> TransitionsProfile:
        return self.data.transitions

    @property
    def render(self) -> RenderProfile:
        return self.data.render

    @property
    def fx(self) -> FxProfile:
        return self.data.fx

    def render_values(self, renderer: str) -> dict:
        """Profile values that reach a segment renderer ("hf" or "native"); a segment cache key includes them."""
        if renderer != "hf":
            return {}
        out = json.loads(self.data.theme.model_dump_json(exclude={"entities", "palette"}))
        if (cp := self.data.camera.scene()) is not None:
            out["camera_pulse"] = cp
        out["motion"] = self.data.motion.render_values()
        return out

    def report(self) -> dict:
        return {"name": self.name, "blake3": self.blake3}


@dataclass(frozen=True)
class Loudness:
    """Effective loudness parameters of one timeline (project audio_chain value, else profile value)."""
    target_lufs: float
    true_peak: float
    lra: float
    tolerance_lu: float


def merge(base: dict, over: dict) -> dict:
    """Key-by-key merge; nested tables merge recursively, every other value replaces the base value."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def path_of(name: str) -> Path:
    return config.PROFILES_DIR / f"{name}.toml"


def names() -> list[str]:
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.toml"))


def _check_python_keys(name: str, cs: dict) -> None:
    if "auto_place" in cs and not isinstance(cs["auto_place"], bool):
        raise EngineError(f"profile '{name}': caption_style.auto_place must be true or false")
    if "forbid_box" in cs and not isinstance(cs["forbid_box"], bool):
        raise EngineError(f"profile '{name}': caption_style.forbid_box must be true or false")
    ab = cs.get("ab_sizes")
    if ab is not None and (not isinstance(ab, list) or not ab
                           or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in ab)):
        raise EngineError(f"profile '{name}': caption_style.ab_sizes must be a non-empty list of positive integers")
    if cs.get("forbid_box") and cs.get("box_bg") is True:
        raise EngineError(f"profile '{name}': caption_style.forbid_box = true but box_bg = true; captions never use a box")
    c = cs.get("clearance")
    if c is not None and (isinstance(c, bool) or not isinstance(c, (int, float)) or c < 0):
        raise EngineError(f"profile '{name}': caption_style.clearance must be a number >= 0")
    y = cs.get("fixed_y")
    if y is not None and (isinstance(y, bool) or not isinstance(y, (int, float)) or not 0 < y < 1920):
        raise EngineError(f"profile '{name}': caption_style.fixed_y must be a number in 0..1920 (px at 1920 height)")
    b = cs.get("place_band")
    if b is not None and (not isinstance(b, list) or len(b) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in b)
                          or not 0 < b[0] <= b[1] < 1):
        raise EngineError(f"profile '{name}': caption_style.place_band must be [lo, hi] with 0 < lo <= hi < 1 (fractions of H)")
    m = cs.get("max_bottom_y")
    if m is not None and (isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 < m <= 1920):
        raise EngineError(f"profile '{name}': caption_style.max_bottom_y must be a number in 0..1920 (px at 1920 height)")


def _check_safety(name: str, d: ProfileData, base: ProfileData) -> None:
    if d.audio.true_peak > base.audio.true_peak:
        raise EngineError(f"profile '{name}': audio.true_peak {d.audio.true_peak} is looser than the global limit "
                          f"{base.audio.true_peak} dBTP")
    if d.audio.tolerance_lu > base.audio.tolerance_lu:
        raise EngineError(f"profile '{name}': audio.tolerance_lu {d.audio.tolerance_lu} is looser than the global "
                          f"limit {base.audio.tolerance_lu} LU")


@lru_cache(maxsize=32)
def _parse(name: str, texts: tuple[str, ...]) -> Profile:
    """texts: default.toml (and the channel file) contents; the cache key is the file content, not a timestamp."""
    def toml(i: int) -> dict:
        try:
            return tomllib.loads(texts[i])
        except tomllib.TOMLDecodeError as e:
            raise EngineError(f"profile {path_of(DEFAULT if i == 0 else name)}: {e}") from e
    raw = toml(0) if name == DEFAULT else merge(toml(0), toml(1))
    try:
        data = ProfileData.model_validate(raw)
        base = data if name == DEFAULT else ProfileData.model_validate(toml(0))
    except ValidationError as e:
        raise EngineError(f"profile '{name}': {e}") from e
    _check_python_keys(name, data.caption_style)
    _check_safety(name, data, base)
    dump = json.loads(data.model_dump_json())
    if dump.get("print") is None:
        dump.pop("print", None)    # profiles without [print] keep their hash
    if dump.get("layout") is None:
        dump.pop("layout", None)   # profiles without [layout] keep their hash
    elif dump["layout"].get("fly") is None:
        dump["layout"].pop("fly", None)   # [layout] without [layout.fly] keeps its hash
    for k, cls in (("camera", CameraProfile), ("motion", MotionProfile), ("transitions", TransitionsProfile),
                   ("render", RenderProfile), ("flags", FlagsProfile), ("fx", FxProfile)):
        if dump.get(k) == json.loads(cls().model_dump_json()):
            dump.pop(k)            # profiles without [camera] / [motion] / [transitions] / [render] / [fx] values keep their hash
    if dump.get("grade") is None:
        dump.pop("grade", None)    # profiles without [grade] keep their hash
    if dump["gates"].get("camera") is None:
        dump["gates"].pop("camera", None)
    elif dump["gates"]["camera"].get("min_drift_per_s") is None:
        dump["gates"]["camera"].pop("min_drift_per_s", None)   # P01 tables without P02 keep their hash
    if not dump["gates"].get("require"):
        dump["gates"].pop("require", None)   # profiles without required style gates keep their hash
    if not dump["gates"].get("advisory"):
        dump["gates"].pop("advisory", None)  # profiles without advisory gates keep their hash
    if dump["gates"].get("anti_slop") == json.loads(AntiSlopGate().model_dump_json()):
        dump["gates"].pop("anti_slop", None)   # default anti-slop levels add nothing to the hash
    for k in ("cta", "ending_keyword", "duration_exact", "geo"):
        if dump["gates"].get(k) is None:
            dump["gates"].pop(k, None)       # absent new gate tables add nothing to the hash
    dump.pop("resources", None)   # the worker budget is a machine setting; stage keys carry the thread counts
    return Profile(name, data, blake3_json(dump))


def load(name: str | None = None) -> Profile:
    name = name or DEFAULT
    if not NAME_RE.match(name):
        raise EngineError(f"profile name {name!r} invalid (lowercase letters, digits, '_' and '-')")
    files = [path_of(DEFAULT)] + ([path_of(name)] if name != DEFAULT else [])
    texts = []
    for f in files:
        try:
            texts.append(f.read_text())
        except FileNotFoundError:
            known = ", ".join(names()) or "none"
            raise EngineError(f"profile '{name}' not found: {f} (known: {known})") from None
        except OSError as e:
            raise EngineError(f"profile {f}: {e}") from e
    return _parse(name, tuple(texts))


def of(tl: "Timeline") -> Profile:
    return load(tl.profile)


def caption_style(tl: "Timeline") -> dict:
    """Effective caption style: profile caption_style with the project's caption_style keys on top. A profile that
    sets forbid_box rejects box_bg = true from either the profile or a timeline override."""
    st = {**of(tl).caption_style, **tl.caption_style}
    if st.get("forbid_box") and st.get("box_bg") is True:
        raise EngineError("caption_style.forbid_box = true but box_bg = true (timeline override); captions never use "
                          "a box in this channel")
    return st


def loudness(tl: "Timeline") -> Loudness:
    ac, a = tl.audio_chain, of(tl).audio
    pick = lambda v, d: d if v is None else v   # noqa: E731
    return Loudness(pick(ac.target_lufs, a.target_lufs), pick(ac.true_peak, a.true_peak), pick(ac.lra, a.lra),
                    a.tolerance_lu)
