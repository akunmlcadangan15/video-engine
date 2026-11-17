"""Compatibility alias for video_engine.timeline.fx."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.timeline.fx")
_sys.modules[__name__] = _module
