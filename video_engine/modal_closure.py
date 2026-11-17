"""Compatibility alias for video_engine.cloud.closure."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.cloud.closure")
_sys.modules[__name__] = _module
