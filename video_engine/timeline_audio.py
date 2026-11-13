"""Timeline audio, STT, SFX, and music brief models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from .ai import STT_DEFAULTS
from .timeline_common import CaptionFix, Strict, _LEGACY_MODEL_KEY, sec_to_frame

SFX_KINDS = ("pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser", "rumble")
SfxKind = Literal["pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser", "rumble"]


class SfxCue(Strict):
    """One sound effect of the mix, generated from a render event (sfx.cues): the sound `asset_id` of `kind` starts
    at `time_s` (absolute seconds) at `gain_db`. `trim_s` is cut from the file start (cue placed before 0 s);
    the cue stops at `max_s` with a short fade."""
    time_s: float = Field(ge=0)
    kind: str = Field(min_length=1, max_length=32)   # an SFX_KINDS id, or "file" for a path cue
    gain_db: float = Field(ge=-60, le=0)
    asset_id: str = Field(min_length=1)   # source asset id of the resolved file (provenance `id`)
    event: str                            # render event: pin, region, route, whip, label, title, figure, counter_tick
    frame: int = Field(ge=0)              # absolute frame of the render event
    trim_s: float = Field(0.0, ge=0)
    max_s: float = Field(2.0, gt=0, le=10)

    @model_validator(mode="after")
    def _kind(self):
        if self.kind not in (*SFX_KINDS, "file"):
            raise ValueError(f"sfx kind '{self.kind}' unknown (known: {', '.join(SFX_KINDS)}, file)")
        return self


class ManualSfx(Strict):
    """A cue placed by the timeline, not by a render event.

    Exactly one of `kind` (an SFX taxonomy id, resolved like an automatic cue) or `asset` (a project-relative
    audio file). Exactly one of `at_frame` (absolute frame) or `time_s` (absolute seconds). `gain_db` is the
    cue's own level. `id` is the mix asset id (default: the kind, or the file stem). `max_s` caps the cue
    (default: the profile kind cap when `kind` is set and the profile defines it, else 2 s)."""
    kind: Optional[SfxKind] = None
    asset: Optional[str] = None
    id: Optional[str] = Field(None, min_length=1, max_length=80)
    at_frame: Optional[int] = Field(None, ge=0)
    time_s: Optional[float] = Field(None, ge=0)
    gain_db: float = Field(-12.0, ge=-60, le=0)
    max_s: Optional[float] = Field(None, gt=0, le=10)

    @model_validator(mode="after")
    def _one(self):
        if (self.kind is None) == (self.asset is None):
            raise ValueError("sfx cue needs exactly one of kind or asset")
        if (self.at_frame is None) == (self.time_s is None):
            raise ValueError("sfx cue needs exactly one of at_frame or time_s")
        return self

    def frame(self, fps: int) -> int:
        if self.at_frame is not None:
            return self.at_frame
        assert self.time_s is not None
        return sec_to_frame(self.time_s, fps)

    def seconds(self, fps: int) -> float:
        return round(self.time_s if self.time_s is not None else self.frame(fps) / fps, 4)


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
    model: str = str(STT_DEFAULTS["model"])
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
    sfx: list[ManualSfx] = []   # timeline-placed cues (kind or asset, time, gain); mixed with automatic cues

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in (_LEGACY_MODEL_KEY, "initial_prompt")}
        return data
