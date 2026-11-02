"""Supported platforms and the platform-dependent names the toolchain needs (tiny on purpose: config.py imports it).

Keys: "linux-x64" (Linux x86_64) and "darwin-arm64" (macOS Apple Silicon). Anything else is an EngineError.
"""
from __future__ import annotations

import platform as _platform
import sys
import tempfile
from pathlib import Path

# key -> (@puppeteer/browsers platform id, suffix of the archive folder `<product>-<suffix>`)
_CHROME = {"linux-x64": ("linux", "linux64"), "darwin-arm64": ("mac_arm", "mac-arm64")}
SUPPORTED = tuple(_CHROME)


def _key_or_none(system: str | None, machine: str | None) -> str | None:
    system = sys.platform if system is None else system
    machine = (_platform.machine() if machine is None else machine).lower()
    if system.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "darwin-arm64"
    return None


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """"linux-x64" or "darwin-arm64" for sys.platform / platform.machine() (or the given values), else EngineError."""
    if key := _key_or_none(system, machine):
        return key
    from .util import EngineError   # lazy: util imports config, and config imports this module
    raise EngineError(f"unsupported platform {system or sys.platform}/{machine or _platform.machine()}; "
                      f"supported: {', '.join(SUPPORTED)} (on macOS use a native arm64 Python, not one under Rosetta)")


def supported_key() -> str | None:
    """Platform key of this machine, or None when unsupported (never raises; for import-time use in config)."""
    return _key_or_none(None, None)


def chrome_relpath(product: str, version: str, key: str | None = None) -> Path:
    """Binary inside the @puppeteer/browsers install dir <vendor>/chrome/<product>:
    <platform>-<version>/<product>-<archive suffix>/<product>."""
    plat, suffix = _CHROME[key or platform_key()]
    return Path(f"{plat}-{version}") / f"{product}-{suffix}" / product


def default_shm_root(system: str | None = None) -> Path:
    """Default scratch root: /dev/shm/video_engine on Linux (tmpfs), <tempdir>/video_engine elsewhere (macOS has no /dev/shm)."""
    system = sys.platform if system is None else system
    return Path("/dev/shm/video_engine") if system.startswith("linux") else Path(tempfile.gettempdir()) / "video_engine"
