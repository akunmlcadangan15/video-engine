"""Compatibility alias for video_engine.layout.printstyle."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.layout.printstyle")
_sys.modules[__name__] = _module
