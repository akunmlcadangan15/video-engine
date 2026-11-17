"""Compatibility alias for video_engine.sound.pauses."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.sound.pauses")
_sys.modules[__name__] = _module
