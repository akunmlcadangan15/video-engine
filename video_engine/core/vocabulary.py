"""Shared scalar vocabulary for timeline and profile contracts."""
from typing import Literal
NATIVE = "NATIVE_RUST_FFMPEG"
HYPERFRAMES = "HYPERFRAMES_GSAP"
HEX_RE = r"#[0-9a-fA-F]{6}"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
VisualType = Literal["map", "photo", "video", "diagram", "archive", "illustration"]

SFX_KINDS = ("pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser", "rumble")
SfxKind = Literal["pop", "tick", "whoosh", "click", "beep", "counter_tick", "impact", "riser", "rumble"]
