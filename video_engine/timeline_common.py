"""Compatibility alias for video_engine.timeline.common."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.timeline.common")
_sys.modules[__name__] = _module
