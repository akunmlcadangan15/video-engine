"""Compatibility alias for video_engine.sound.sfx."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.sound.sfx")
_sys.modules[__name__] = _module
