"""Compatibility alias for video_engine.cloud.launch."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.cloud.launch")
_sys.modules[__name__] = _module
