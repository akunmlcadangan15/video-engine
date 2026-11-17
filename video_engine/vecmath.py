"""Compatibility alias for video_engine.core.vecmath."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.core.vecmath")
_sys.modules[__name__] = _module
