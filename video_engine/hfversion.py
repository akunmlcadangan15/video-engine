"""Which HyperFrames CLI the engine renders with (the HyperFrames version is selectable; the rest of the toolchain,
node, Chrome and puppeteer-core for the probes, stays pinned in vendor/).

Setting, first one found wins:
  1. env VIDEO_ENGINE_HYPERFRAMES_VERSION (e.g. in engine.env)
  2. vendor/hyperframes.select (one line, written by `engine toolchain use SPEC`)
  3. "pinned"
Values (SPEC):
  pinned   npm.hyperframes in vendor/VERSIONS.json, installed from vendor/npm/package-lock.json (`engine doctor
           --install`); the default
  X.Y.Z    exact version; the pinned one resolves to vendor/npm, any other to vendor/hf/X.Y.Z (installed by
           `engine toolchain use X.Y.Z`, not lockfile-pinned)
  latest   newest version installed (pin or vendor/hf/*); `engine toolchain use latest` installs the newest npm release
  system   the `hyperframes` CLI on PATH (global npm install), run with the vendored node
Nothing resolves over the network at build time. The resolved version and source go into the segment cache key
(toolchain.hf_identity) and into build_report.json (manifest.toolchain).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config
from .util import EngineError

ENV = "VIDEO_ENGINE_HYPERFRAMES_VERSION"
SELECT_FILE = "hyperframes.select"
KEYWORDS = ("pinned", "latest", "system")
_EXACT = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class Active:
    spec: str          # the setting as written ("pinned", "latest", "system" or X.Y.Z)
    setting_from: str  # "env", "file" or "default"
    version: str       # resolved package version, "missing" when the package is not installed
    source: str        # "pinned" (vendor/npm), "vendor" (vendor/hf/X) or "system" (global CLI)
    root: Path         # package dir (…/node_modules/hyperframes)

    @property
    def cli(self) -> Path:
        return self.root / "bin" / "hyperframes.mjs"

    @property
    def installed(self) -> bool:
        return self.cli.is_file()

    def label(self) -> str:
        return f"{self.version} ({self.source}, setting {self.spec!r} from {self.setting_from})"


def parse_spec(text: str | None) -> str:
    """Normalise a setting: "" / None -> "pinned"; keywords case-insensitive; "v0.9.13" -> "0.9.13"; anything else
    raises EngineError."""
    s = (text or "").strip()
    if not s:
        return "pinned"
    if s.lower() in KEYWORDS:
        return s.lower()
    s = s.removeprefix("v")
    if _EXACT.match(s):
        return s
    raise EngineError(f"HyperFrames version {text!r}: want pinned, latest, system or an exact version X.Y.Z")


def version_key(v: str) -> tuple:
    """Sort key for X.Y.Z[-pre]: numeric parts, a pre-release sorts before its release."""
    core, _, pre = v.partition("-")
    return tuple(int(x) for x in core.split(".")) + ((0, pre) if pre else (1, ""))


def pinned_version(vendor: Path | None = None) -> str:
    """npm.hyperframes of vendor/VERSIONS.json ("missing" when the file has no such pin)."""
    try:
        return json.loads(((vendor or config.VENDOR) / "VERSIONS.json").read_text())["npm"]["hyperframes"]
    except (OSError, ValueError, KeyError, TypeError):
        return "missing"


def pinned_root(vendor: Path | None = None) -> Path:
    return (vendor or config.VENDOR) / "npm" / "node_modules" / "hyperframes"


def vendor_root(version: str, vendor: Path | None = None) -> Path:
    return (vendor or config.VENDOR) / "hf" / version / "node_modules" / "hyperframes"


def installed_versions(vendor: Path | None = None) -> list[str]:
    """Versions under vendor/hf/ with a CLI, sorted oldest first (the pinned tree is not listed)."""
    d = (vendor or config.VENDOR) / "hf"
    out = [p.name for p in d.iterdir() if _EXACT.match(p.name) and (vendor_root(p.name, vendor) / "bin" / "hyperframes.mjs").is_file()] \
        if d.is_dir() else []
    return sorted(out, key=version_key)


def system_root() -> Path | None:
    """Package dir of the `hyperframes` on PATH (its bin is a symlink into …/node_modules/hyperframes/bin)."""
    exe = shutil.which("hyperframes")
    if not exe:
        return None
    root = Path(exe).resolve().parent.parent
    return root if (root / "package.json").is_file() else None


def _pkg_version(root: Path | None) -> str:
    try:
        return json.loads((root / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "missing"


def setting(vendor: Path | None = None, environ: dict | None = None) -> tuple[str, str]:
    """(spec, where it came from)."""
    env = os.environ if environ is None else environ
    if (v := env.get(ENV, "").strip()):
        return parse_spec(v), "env"
    f = (vendor or config.VENDOR) / SELECT_FILE
    if f.is_file() and (v := f.read_text().strip()):
        return parse_spec(v), "file"
    return "pinned", "default"


def resolve(spec: str | None = None, vendor: Path | None = None, environ: dict | None = None) -> Active:
    """The HyperFrames package a SPEC (default: the current setting) points at. Does not install anything."""
    vendor = vendor or config.VENDOR
    if spec is None:
        spec, src = setting(vendor, environ)
    else:
        spec, src = parse_spec(spec), "argument"
    pin = pinned_version(vendor)
    if spec == "system":
        root = system_root() or Path("/nonexistent/hyperframes")
        return Active(spec, src, _pkg_version(root), "system", root)
    if spec == "latest":
        cands = installed_versions(vendor)
        if _pkg_version(pinned_root(vendor)) == pin:
            cands.append(pin)
        v = max(cands, key=version_key) if cands else pin
    else:
        v = pin if spec == "pinned" else spec
    if v == pin:
        return Active(spec, src, _pkg_version(pinned_root(vendor)), "pinned", pinned_root(vendor))
    root = vendor_root(v, vendor)
    return Active(spec, src, _pkg_version(root), "vendor", root)


def active() -> Active:
    return resolve()


def require() -> Active:
    """The active HyperFrames, or EngineError naming the fix when it is not installed."""
    a = active()
    if not a.installed:
        fix = "install it globally (npm i -g hyperframes)" if a.source == "system" else \
            "run `engine doctor --install`" if a.source == "pinned" else f"run `engine toolchain use {a.spec}`"
        raise EngineError(f"HyperFrames {a.label()} is not installed at {a.root}; {fix}")
    return a
