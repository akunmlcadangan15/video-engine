"""Compatibility alias for video_engine.core.projection."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.core.projection")
_sys.modules[__name__] = _module
