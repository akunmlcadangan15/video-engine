"""Compatibility alias for video_engine.profiles.schema."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.profiles.schema")
_sys.modules[__name__] = _module
