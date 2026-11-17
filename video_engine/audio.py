"""Compatibility alias for video_engine.sound.audio."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.sound.audio")
_sys.modules[__name__] = _module
