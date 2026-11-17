"""Compatibility alias for video_engine.cloud.cli."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.cloud.cli")
_sys.modules[__name__] = _module
