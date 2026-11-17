"""Compatibility alias for video_engine.gates.tools."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.gates.tools")
_sys.modules[__name__] = _module
