"""Compatibility alias for video_engine.draft.recipes."""
import importlib as _importlib
import sys as _sys
_module = _importlib.import_module("video_engine.draft.recipes")
_sys.modules[__name__] = _module
