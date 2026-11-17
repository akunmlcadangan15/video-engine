"""Compatibility alias for video_engine.sound.mixer."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.sound.mixer")
_sys.modules[__name__] = _module
