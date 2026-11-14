"""master_timeline.json data contract. All times are integer frames; sec_to_frame() is the ONLY seconds->frames
conversion in the engine (delegates to the Rust crate so Python and Rust can never disagree)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, ValidationError, model_validator

from . import config
from .grade import Grade
from .timeline_audio import SFX_KINDS, SfxKind, SfxCue, ManualSfx, MusicBrief, Stt, AudioChain
from .timeline_common import NATIVE, HYPERFRAMES, HEX_RE, IMAGE_EXT, VisualType, sec_to_frame, Strict, Settings, Credit, CaptionFix, MAX_FRAME, _LEGACY_MODEL_KEY, _LEGACY_SOURCE
from .timeline_fx import GLOBE_ALT_MIN, GLOBE_ALT_MAX, GlobeKey, Globe, GridBackdrop, StarsBackdrop, Backdrop, GlobeHalo, TimezoneBand, Timezones, BubbleAnchor, Bubble, FlagCharacter, CutawayLayer, Cutaway, InsetAnchor, Inset, Geoid, Particle, Thermometer, Flow
from .timeline_map import Hatch, CameraMove, Flag, Pin, Card, RegionChange, Region, Border, MapMorph, MapText, Route, Figure, ZOOM_MAX, ZOOM_LIMIT, TILT_MAX, ROLL_MAX, CameraKey, AreaLabel, Note, PROP_EXT, MODEL_EXT, PROP_DEPTH_MAX, PropMotion, PropIdle, PropShadow, PropModel, Prop, LAYER_EXT, LAYER_DEPTH_MIN, ParallaxLayer, Beat
from .timeline_scene import GsapScene, CameraKey3D, Camera3DPreset, CAMERA3D_FIELDS, SplitView
from .util import EngineError, atomic_write_text

__all__ = ['NATIVE', 'HYPERFRAMES', 'HEX_RE', 'IMAGE_EXT', 'VisualType', 'sec_to_frame', 'Strict', 'Settings', 'Credit', 'CaptionFix', 'MAX_FRAME', '_LEGACY_MODEL_KEY', '_LEGACY_SOURCE', 'SFX_KINDS', 'SfxKind', 'SfxCue', 'ManualSfx', 'MusicBrief', 'Stt', 'AudioChain', 'Hatch', 'CameraMove', 'Flag', 'Pin', 'Card', 'RegionChange', 'Region', 'Border', 'MapMorph', 'MapText', 'Route', 'Figure', 'ZOOM_MAX', 'ZOOM_LIMIT', 'TILT_MAX', 'ROLL_MAX', 'CameraKey', 'AreaLabel', 'Note', 'PROP_EXT', 'MODEL_EXT', 'PROP_DEPTH_MAX', 'PropMotion', 'PropIdle', 'PropShadow', 'PropModel', 'Prop', 'LAYER_EXT', 'LAYER_DEPTH_MIN', 'ParallaxLayer', 'Beat', 'GLOBE_ALT_MIN', 'GLOBE_ALT_MAX', 'GlobeKey', 'Globe', 'GridBackdrop', 'StarsBackdrop', 'Backdrop', 'GlobeHalo', 'TimezoneBand', 'Timezones', 'BubbleAnchor', 'Bubble', 'FlagCharacter', 'CutawayLayer', 'Cutaway', 'InsetAnchor', 'Inset', 'Geoid', 'Particle', 'Thermometer', 'Flow', 'GsapScene', 'CameraKey3D', 'Camera3DPreset', 'CAMERA3D_FIELDS', 'SplitView', 'Overlay', 'SegmentIntent', 'Segment', 'CaptionWord', 'CaptionsMeta', 'MIN_WAIVER_REASON', 'GateWaiver', 'ProjectGates', 'Timeline', 'load', 'save']

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
    ornaments: Optional[bool] = Field(None, exclude_if=lambda v: v is None)   # heading: None = profile [print]
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
        if self.ornaments is not None and self.kind != "heading":
            raise ValueError(f"overlay '{self.id}': ornaments applies to kind 'heading' only")
        if self.start_frame is not None and self.end_frame is not None and self.end_frame <= self.start_frame:
            raise ValueError(f"overlay '{self.id}': end_frame {self.end_frame} must be > start_frame {self.start_frame}")
        return self

    @property
    def drawn(self) -> bool:
        return self.kind != "reserve"


class SegmentIntent(Strict):
    """Local creative expectations, never an exemption from render/decode integrity. `shot` is the aspect-agnostic
    shot intent (video_engine/framing.py): the engine fills open camera fields from its per-orientation framing."""
    motion: Optional[Literal["static", "moving"]] = None
    negative_space: bool = Field(False, strict=True)
    reason: str = Field(min_length=1, max_length=500)
    shot: Optional[Literal["establish", "track", "reveal", "compare", "journey", "hold", "reaction", "contrast"]] = \
        Field(None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _intent(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("intent.reason must not be blank")
        if self.motion is None and not self.negative_space and self.shot is None:
            raise ValueError("intent needs motion, intentional negative_space or a shot")
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
        from .gate_registry import KNOWN_GATE_IDS, LINT_GATE_IDS
        bad = [g for g in self.require if g not in LINT_GATE_IDS]
        if bad:
            raise ValueError(f"gates.require: unknown style gate(s) {bad} (known: {', '.join(LINT_GATE_IDS)})")
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
        from . import profiles, units
        from .util import EngineError
        try:
            orient = units.orientation_of(self.settings.width, self.settings.height)
            profiles.check_orientation(profiles.load(self.profile), self.settings.width, self.settings.height)
            prof = profiles.load(self.profile, self.profile_overrides, orientation=orient)
            mz = prof.camera.max_zoom
        except EngineError as e:
            raise ValueError(str(e)) from e
        for s in self.segments[1:]:
            if s.transition_in == "page_turn" and not prof.transitions.page_turn:
                raise ValueError(f"segment {s.seg_index}: transition_in 'page_turn' is not allowed by the profile "
                                 "([transitions] page_turn = false)")
        try:
            profiles.check_timeline_caption_style(self, prof)   # keys, types, geometry: the profile's own check
        except EngineError as e:
            raise ValueError(str(e)) from e
        for s in self.segments:
            sc = s.gsap_scene_config
            if sc is None:
                continue
            mode = sc.camera_mode or prof.camera.mode
            if sc.camera3d_preset is not None:
                from . import camera_full3d
                p = sc.camera3d_preset
                try:
                    keys = [CameraKey3D(**k) for k in camera_full3d.preset_keys(
                        p.name, s.n_frames, self.settings.fps, target=p.target, height=p.height, fov=p.fov, **p.options)]
                except ValueError as e:
                    raise ValueError(f"segment {s.seg_index}: {e}") from e
                if sc.camera3d and sc.camera3d != keys:
                    raise ValueError(f"segment {s.seg_index}: give camera3d keys or camera3d_preset, not both")
                sc.camera3d = keys
            if mode == "3d" and not sc.camera3d:
                raise ValueError(f"segment {s.seg_index}: camera mode '3d' needs gsap_scene_config.camera3d keys "
                                 "or camera3d_preset")
            if sc.camera3d and mode != "3d":
                raise ValueError(f"segment {s.seg_index}: camera3d keys need camera mode '3d' (set "
                                 "gsap_scene_config.camera_mode = \"3d\" or the profile [camera] mode)")
            if sc.camera3d and sc.camera3d[-1].at_frame >= s.n_frames:
                raise ValueError(f"segment {s.seg_index}: camera3d key at_frame {sc.camera3d[-1].at_frame} is past the "
                                 f"segment ({s.n_frames} frames)")
        try:
            from . import framing
            framing.apply(self)
        except (EngineError, ValueError) as e:
            raise ValueError(str(e)) from e
        total = self.segments[-1].end_frame
        for i, cue in enumerate(self.audio_chain.sfx):
            frame = cue.frame(self.settings.fps)
            if frame >= total:
                raise ValueError(f"audio_chain.sfx[{i}]: frame {frame} is past the timeline ({total} frames)")
        for s in self.segments:
            sc = s.gsap_scene_config
            if sc is not None and sc.thermometers:
                from . import components
                try:
                    components.thermo_starts(sc.thermometers)
                except ValueError as e:
                    raise ValueError(f"segment {s.seg_index}: {e}") from e
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
    atomic_write_text(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
