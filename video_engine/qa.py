"""Compatibility alias for video_engine.gates.qa."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.gates.qa")
_sys.modules[__name__] = _module
