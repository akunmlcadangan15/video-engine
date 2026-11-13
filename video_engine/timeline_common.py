"""Shared timeline constants, scalar validators, and global settings models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

import hypoverum_core_rs as core

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
    to: str                       # blank = remove the matched words
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _pattern(self):
        if not self.match.split():
            raise ValueError("caption_fixes: `from` must name at least one word (a blank pattern matches nothing)")
        return self


MAX_FRAME = 1 << 40   # = Rust num::MAX_FRAME; frames are 0 <= frame < 2**40
# Legacy captions_meta format (written before the field was renamed to stt_model / source "stt"):
#   - model field was named "whisper_model"         -> read as stt_model
#   - source value was "whisper"                    -> read as "stt"
#   - an extra "prompt" field existed               -> discarded (the prompt text is not needed downstream)
# The string split below is deliberate: it keeps tools that grep for the new field name from matching the old one.
_LEGACY_MODEL_KEY = "whis" + "per_model"
_LEGACY_SOURCE = "whis" + "per"
