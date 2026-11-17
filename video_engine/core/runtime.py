"""Single lazy native runtime; thread configuration precedes extension initialization."""
from __future__ import annotations

import importlib
import os
from threading import RLock

_lock = RLock()
_native = None


def native():
    """Load Rust once, or report an actionable engine error without algorithm fallbacks."""
    global _native
    with _lock:
        if _native is None:
            from video_engine.core import config
            from video_engine.core.util import EngineError
            os.environ.setdefault("RAYON_NUM_THREADS", str(config.RUST_THREADS))
            try:
                module = importlib.import_module("hypoverum_core_rs")
            except ImportError as exc:
                raise EngineError("native operation requires hypoverum_core_rs; install the Rust extension") from exc
            config.RUST_THREADS_ACTIVE = module.set_threads(config.RUST_THREADS)
            _native = module
        return _native


class _NativeProxy:
    def __getattr__(self, name):
        return getattr(native(), name)


core = _NativeProxy()
