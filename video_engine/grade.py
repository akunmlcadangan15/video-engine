"""Compatibility alias for video_engine.render.grade."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.render.grade")
_sys.modules[__name__] = _module
