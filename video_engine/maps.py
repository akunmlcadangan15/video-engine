"""Compatibility alias for video_engine.render.maps."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.render.maps")
_sys.modules[__name__] = _module
