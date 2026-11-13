"""Timeline 3D globe, backdrop, cutaway, inset, particle, and flow FX models."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import ConfigDict, Field, model_validator

from .timeline_common import HEX_RE, Strict
from .timeline_map import Flag, TILT_MAX

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
    `min` to colour_high at `max`) and the counter text together. The fill rises over `fill_s` from `at_frame`,
    starting at `from` when that is set, otherwise at the previous thermometer's `value` when `continue_from` is
    set, otherwise at `min`. The start is fixed from the timeline, so the same scene always draws the same frames.
    `note` is shown under the label: give "ilustratif" (or the source) whenever the value is not a sourced reading."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: float
    min: float = 0.0
    max: float = 50.0
    from_value: Optional[float] = Field(None, alias="from", exclude_if=lambda v: v is None)
    continue_from: bool = Field(False, exclude_if=lambda v: v is False)
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
        if self.from_value is not None and not self.min <= self.from_value <= self.max:
            raise ValueError(f"thermometer: from {self.from_value} must be within {self.min}..{self.max}")
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
