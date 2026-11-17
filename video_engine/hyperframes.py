"""Compatibility alias for video_engine.render.hyperframes."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.render.hyperframes")
_sys.modules[__name__] = _module
