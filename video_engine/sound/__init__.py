"""Sound sourcing: music and SFX from Epidemic Sound through an agent file handoff, kept in a content-addressed cache.

Modules:
  cache.py     cache under <engine cache>/sound/; `resolve(kind, profile)` and `resolve_music(brief, profile)` ->
               ResolvedSound (the mixer's resolver adapter); credit lines; orphan cleanup
  request.py   `ensure_sounds`: sounds missing for the profile's required source -> request bundle
               <project>/out/sound_request/ + AwaitingAgent (CLI exit code 3); response.json there -> imported
  epidemic.py  pure helpers for the agent that answers a request with the Epidemic Sound connector
  __main__.py  `python -m video_engine.sound PROJECT [--resume] [--credits]`
Gate A02 (audio source) is in video_engine/gates/audio_source.py. Contract: docs/SOUND.md. The engine makes no
network call for sounds and holds no key.
"""
from __future__ import annotations

from ..timeline import SFX_KINDS

SOURCES = ("epidemic", "local-test")
EPIDEMIC = "epidemic"
LOCAL_TEST = "local-test"
BRIEF_KEYS = ("mood", "energy", "bpm_min", "bpm_max", "duration_s")
PROVENANCE_KEYS = ("source", "id", "title", "artist", "credit")
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

__all__ = ["SFX_KINDS", "SOURCES", "EPIDEMIC", "LOCAL_TEST", "BRIEF_KEYS", "PROVENANCE_KEYS", "AUDIO_EXTS",
           "required_source"]


def required_source(profile) -> str | None:
    """Profile `[audio] require_source` (None = any source)."""
    return profile.audio.require_source
