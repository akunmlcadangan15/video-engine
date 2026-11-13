"""Timeline composite HyperFrames scene and 3D camera keyframe models."""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import Field, model_serializer, model_validator

from .projection import MERC_LAT_MAX
from .timeline_common import HEX_RE, Strict
from .timeline_fx import (
    Backdrop,
    Bubble,
    Cutaway,
    FlagCharacter,
    Flow,
    Geoid,
    Globe,
    Inset,
    Particle,
    Thermometer,
    Timezones,
)
from .timeline_map import (
    Beat,
    Border,
    CameraKey,
    Card,
    Figure,
    MapMorph,
    MapText,
    ParallaxLayer,
    Pin,
    Prop,
    Region,
    Route,
)

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
    camera_mode: Optional[Literal["planar", "3d"]] = Field(None, exclude_if=lambda v: v is None)   # None = profile
                                                    # [camera] mode
    camera3d: Optional[list[CameraKey3D]] = Field(None, exclude_if=lambda v: v is None)   # full 3D camera keys
                                                    # (camera mode "3d"; video_engine/camera_full3d.py)
    camera3d_preset: Optional[Camera3DPreset] = Field(None, exclude_if=lambda v: v is None)   # a named 3D move
                                                    # in place of camera3d keys (the keys are derived, not stored)
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
    heading_ornaments: Optional[bool] = Field(None, exclude_if=lambda v: v is None)   # None = profile [print]
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

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        d = handler(self)
        if self.camera3d_preset is not None and isinstance(d, dict):   # derived keys are not part of the document
            d.pop("camera3d", None)
        return d

    @model_validator(mode="after")
    def _camera3d(self):
        k = self.camera3d or []
        if not k and self.camera3d_preset is None:
            return self
        if k and k[0].at_frame != 0:
            raise ValueError(f"camera3d[0].at_frame is {k[0].at_frame}; the first key is the opening view at frame 0")
        for i in range(1, len(k)):
            if k[i].at_frame <= k[i - 1].at_frame + k[i - 1].hold:
                raise ValueError(f"camera3d[{i}].at_frame {k[i].at_frame} must be > camera3d[{i - 1}].at_frame "
                                 f"{k[i - 1].at_frame} + hold {k[i - 1].hold}")
        bad = sorted(f for f in self.model_fields_set - CAMERA3D_FIELDS
                     if getattr(self, f) not in (None, [], False) and f in type(self).model_fields
                     and getattr(self, f) != type(self).model_fields[f].default)
        if bad:
            raise ValueError(f"3D camera scenes take only pins, cards, title and screen figures; remove {', '.join(bad)}")
        for f in self.figures or []:
            if f.anchor == "map":
                raise ValueError("3D camera scenes take screen-anchored figures only (anchor 'map' rides the planar camera)")
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


class CameraKey3D(Strict):
    """One view of the full 3D camera (gsap_scene_config.camera3d; video_engine/camera_full3d.py). `eye` and `target`
    are [u, v, h]: a normalised map point (v down) and the height above the map in map widths."""
    at_frame: int = Field(ge=0)
    eye: Optional[list[float]] = Field(None, min_length=3, max_length=3, exclude_if=lambda v: v is None)
    polar: Optional[list[float]] = Field(None, min_length=3, max_length=3, exclude_if=lambda v: v is None)
                                                       # the eye as [distance (map widths), tilt (deg off the
                                                       # vertical), azimuth (deg, 0 = south of the target)] about
                                                       # the target, instead of `eye`
    target: list[float] = Field([0.5, 0.5, 0.0], min_length=2, max_length=3)
    roll: float = Field(0.0, ge=-180.0, le=180.0)      # deg, the map turns clockwise on screen
    fov: float = Field(50.0, ge=10.0, le=120.0)        # vertical field of view (deg)
    move: Literal["glide", "linear"] = "glide"         # progress of the leg into this key
    hold: int = Field(0, ge=0, le=90)                  # frames the camera stays on this view after at_frame

    @model_validator(mode="after")
    def _k(self):
        if (self.eye is None) == (self.polar is None):
            raise ValueError(f"camera3d key at_frame {self.at_frame}: give exactly one of eye and polar")
        if not all(math.isfinite(v) for v in (self.eye or self.polar) + self.target):
            raise ValueError(f"camera3d key at_frame {self.at_frame}: eye and target must be finite")
        if len(self.target) == 2:
            self.target = [*self.target, 0.0]
        if self.polar is not None:
            if not (self.polar[0] > 0 and 0 <= self.polar[1] < 90):
                raise ValueError(f"camera3d key at_frame {self.at_frame}: polar needs distance > 0 and tilt in "
                                 f"[0, 90), got {self.polar[0]:g} and {self.polar[1]:g}")
            return self
        if self.eye[2] <= 0:
            raise ValueError(f"camera3d key at_frame {self.at_frame}: eye height {self.eye[2]:g} must be above the "
                             "map (> 0)")
        if math.dist(self.eye, self.target) < 1e-6:
            raise ValueError(f"camera3d key at_frame {self.at_frame}: eye and target coincide")
        return self


class Camera3DPreset(Strict):
    """A named move of the full 3D camera (camera_full3d.PRESETS): the timeline expands it into the scene's
    `camera3d` keys over the whole segment."""
    name: Literal["push_in", "orbit", "crash_zoom", "vertigo"]
    target: list[float] = Field([0.5, 0.5], min_length=2, max_length=2)   # normalised map point the camera looks at
    height: float = Field(0.6, gt=0.0, le=8.0)         # start distance from the target (map widths)
    fov: float = Field(50.0, ge=10.0, le=120.0)        # start vertical field of view (deg)
    options: dict[str, float] = {}                     # the preset's own parameters (camera_full3d.PRESETS)


# Scene fields a 3D-camera scene supports (screen-anchored or pin-anchored); every other optional table is refused.
CAMERA3D_FIELDS = {"pins", "cards", "title", "title_entity", "title_at_frame", "figures", "camera3d", "camera_mode",
                   "camera3d_preset",
                   "map_asset", "map_asset_id", "map_region", "template", "camera_preset", "camera_intensity",
                   "map_projection"}


class SplitView(Strict):
    """Split screen: the frame holds two panels of equal height stacked top / bottom with a `divider_px` line between
    them; each panel is rendered by HyperFrames at the panel size with its own map and camera, both on the segment's
    frame clock (same n_frames and fps), and the engine stacks them (hyperframes.render)."""
    second: GsapScene
    divider_px: int = Field(8, ge=2, le=24, multiple_of=2)
    divider_colour: str = Field("#ffffff", pattern=HEX_RE)


GsapScene.model_rebuild()
