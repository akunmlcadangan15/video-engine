"""Compatibility alias for video_engine.layout.scene_math."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.layout.scene_math")
_sys.modules[__name__] = _module
