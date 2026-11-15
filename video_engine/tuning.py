"""Build-tuning environment variables: one validated parser per variable.

No engine imports (and so no native extension): the Modal host side (modal_closure, modal_cli) validates and forwards
these values with the same rules the engine applies inside the container. Every parser takes an optional mapping
(os.environ when None), returns None when the variable is unset or blank, and raises ValueError naming the variable
on an invalid value.
"""
from __future__ import annotations

import os
from typing import Mapping

X264_THREADS_ENV = "VIDEO_ENGINE_X264_THREADS"
HF_WORKERS_ENV = "VIDEO_ENGINE_HF_WORKERS"
HF_CAPTURE_ENV = "VIDEO_ENGINE_HF_CAPTURE"
DOM_WORKERS_ENV = "VIDEO_ENGINE_DOM_WORKERS"

X264_THREADS_MIN, X264_THREADS_MAX = 1, 16
HF_CAPTURE_MODES = ("screenshot", "beginframe")

# Variables a Modal build forwards from the host into the container environment, in this order.
FORWARDED = (X264_THREADS_ENV, HF_WORKERS_ENV, HF_CAPTURE_ENV, DOM_WORKERS_ENV)


def _raw(environ: Mapping[str, str] | None, name: str) -> str:
    env = os.environ if environ is None else environ
    return (env.get(name) or "").strip()


def _int(environ: Mapping[str, str] | None, name: str, lo: int, hi: int | None = None) -> int | None:
    raw = _raw(environ, name)
    if not raw:
        return None
    bounds = f"{lo}..{hi}" if hi is not None else f">= {lo}"
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer {bounds}, got {raw!r}") from None
    if n < lo or (hi is not None and n > hi):
        raise ValueError(f"{name} must be an integer {bounds}, got {n}")
    return n


def x264_threads(environ: Mapping[str, str] | None = None) -> int | None:
    """VIDEO_ENGINE_X264_THREADS: libx264 frame threads of every engine encode (native segments, HF normalise/grade
    pass, lossless grade, and the encode inside the HyperFrames CLI), integer 1..16."""
    return _int(environ, X264_THREADS_ENV, X264_THREADS_MIN, X264_THREADS_MAX)


def hf_workers(environ: Mapping[str, str] | None = None) -> int | None:
    """VIDEO_ENGINE_HF_WORKERS: pinned HyperFrames Chrome capture workers per HF segment, integer >= 1."""
    return _int(environ, HF_WORKERS_ENV, 1)


def dom_workers(environ: Mapping[str, str] | None = None) -> int | None:
    """VIDEO_ENGINE_DOM_WORKERS: ceiling of concurrent DOM probes, integer >= 1."""
    return _int(environ, DOM_WORKERS_ENV, 1)


def hf_capture(environ: Mapping[str, str] | None = None) -> str | None:
    """VIDEO_ENGINE_HF_CAPTURE: requested HyperFrames capture mode, one of HF_CAPTURE_MODES (case-insensitive)."""
    raw = _raw(environ, HF_CAPTURE_ENV).lower()
    if not raw:
        return None
    if raw not in HF_CAPTURE_MODES:
        raise ValueError(f"{HF_CAPTURE_ENV}={raw!r} is not one of {', '.join(HF_CAPTURE_MODES)}")
    return raw


_PARSERS = {X264_THREADS_ENV: x264_threads, HF_WORKERS_ENV: hf_workers, HF_CAPTURE_ENV: hf_capture,
            DOM_WORKERS_ENV: dom_workers}


def forwarded(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The FORWARDED variables set in `environ`, validated and normalised (integers without padding, capture mode
    lower-case). Raises ValueError on the first invalid one."""
    out: dict[str, str] = {}
    for name in FORWARDED:
        value = _PARSERS[name](environ)
        if value is not None:
            out[name] = str(value)
    return out


def validate(environ: Mapping[str, str] | None = None) -> None:
    """Parse every tuning variable (FORWARDED); ValueError naming the first invalid one."""
    forwarded(environ)


def x264_threads_or_none(environ: Mapping[str, str] | None = None) -> int | None:
    """x264_threads, or None for an invalid value: the import-time read of config, which must not raise so that
    `engine --help` and `engine doctor` run. Every build entry point calls validate() before the value is used."""
    try:
        return x264_threads(environ)
    except ValueError:
        return None
