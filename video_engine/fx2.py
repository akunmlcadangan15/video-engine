"""Compatibility re-exports for per-scene visual effects (now unified in video_engine/fx.py)."""
from __future__ import annotations

from .fx import (
    FX2_JS as JS,
    _emitter,
    _fire,
    _fireworks,
    _harmonics,
    _lcg,
    _seed,
    cutaway_block,
    flag_characters,
    geoid_block,
    geoid_height,
    inset_blocks,
    inset_name,
    particle_blocks,
    particle_state,
    scene_block,
    sources,
)

__all__ = [
    "JS",
    "_emitter",
    "_fire",
    "_fireworks",
    "_harmonics",
    "_lcg",
    "_seed",
    "cutaway_block",
    "flag_characters",
    "geoid_block",
    "geoid_height",
    "inset_blocks",
    "inset_name",
    "particle_blocks",
    "particle_state",
    "scene_block",
    "sources",
]
