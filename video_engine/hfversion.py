"""Which HyperFrames CLI the engine renders with.

Setting, first one found wins:
  1. env VIDEO_ENGINE_HYPERFRAMES_VERSION (e.g. in engine.env)
  2. vendor/hyperframes.select (one line, written by `engine toolchain use SPEC`)
  3. "auto" (the default): use a HyperFrames already on the machine (portable discovery: env, npm globals,
     PATH, user toolchain, then the vendor tree). `engine doctor --install` installs the latest npm release
     only when none is found. "pinned" is opt-in.
Values (SPEC):
  auto     machine-first discovery (the default when unset)
  pinned   npm.hyperframes in vendor/VERSIONS.json, installed from vendor/npm/package-lock.json
  X.Y.Z    exact version; the pinned one resolves to vendor/npm, any other to vendor/hf/X.Y.Z (installed by
           `engine toolchain use X.Y.Z`, not lockfile-pinned)
  latest   newest version already installed (machine, pin, or vendor/hf/*); `engine toolchain use latest`
           also installs the newest npm release
  system   a HyperFrames found outside vendor (PATH / global npm), never the vendor tree
Nothing resolves over the network at build time. The resolved version and source go into the segment cache key
(toolchain.hf_identity) and into build_report.json (manifest.toolchain).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .util import EngineError

ENV = "VIDEO_ENGINE_HYPERFRAMES_VERSION"
SELECT_FILE = "hyperframes.select"
KEYWORDS = ("auto", "pinned", "latest", "system")
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
    """Normalise a setting: "" / None -> "auto"; keywords case-insensitive; "v0.9.13" -> "0.9.13"; anything else
    raises EngineError."""
    s = (text or "").strip()
    if not s:
        return "auto"
    if s.lower() in KEYWORDS:
        return s.lower()
    s = s.removeprefix("v")
    if _EXACT.match(s):
        return s
    raise EngineError(f"HyperFrames version {text!r}: want auto, pinned, latest, system or an exact version X.Y.Z")


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


def system_root(vendor: Path | None = None, environ: dict | None = None) -> Path | None:
    """Package dir of a HyperFrames install found outside vendor: PATH CLI, global npm roots, or env override."""
    from . import config, discover
    env = os.environ if environ is None else environ
    vend = vendor or config.VENDOR
    exe = discover.which("hyperframes", env)
    if exe and (exe.resolve().parent.parent / "package.json").is_file():
        return exe.resolve().parent.parent
    r = discover.resolve_hyperframes(vendor=vend, environ=env, home=_home_from_environ(environ),
                                     prefer_vendor=False)
    if r.found and r.path is not None and r.source not in {"vendor", "pinned"}:
        root = _package_root(r.path)
        if (root / "package.json").is_file():
            return root
    return None


def _pkg_version(root: Path | None) -> str:
    if root is None:
        return "missing"
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
    return "auto", "default"


def _package_root(path: Path) -> Path:
    """Directory that contains package.json for a CLI file or a package dir."""
    if path.is_file():
        for cand in (path.parent.parent, path.parent):
            if (cand / "package.json").is_file():
                return cand
        return path.parent
    return path


def _home_from_environ(environ: dict | None) -> Path | None:
    """When an environ dict is passed in, do not fall back to the process home (keeps tests hermetic)."""
    if environ is None:
        return None
    if environ.get("HOME"):
        return Path(environ["HOME"])
    if environ.get("USERPROFILE"):
        return Path(environ["USERPROFILE"])
    return Path("/nonexistent")


def _resolve_auto(spec: str, src: str, vendor: Path, environ: dict | None) -> Active:
    """Machine-first: whatever discovery finds (globals, PATH, user toolchain, then vendor)."""
    from . import discover
    env = os.environ if environ is None else environ
    r = discover.resolve_hyperframes(vendor=vendor, environ=env, home=_home_from_environ(environ),
                                     prefer_vendor=False)
    if r.found and r.path is not None:
        root = _package_root(r.path)
        source = "pinned" if root == pinned_root(vendor) else r.source
        return Active(spec, src, _pkg_version(root), source, root)
    return Active(spec, src, "missing", "missing", Path("/nonexistent/hyperframes"))


def resolve(spec: str | None = None, vendor: Path | None = None, environ: dict | None = None) -> Active:
    """The HyperFrames package a SPEC (default: the current setting) points at. Does not install anything."""
    vendor = vendor or config.VENDOR
    if spec is None:
        spec, src = setting(vendor, environ)
    else:
        spec, src = parse_spec(spec), "argument"
    pin = pinned_version(vendor)
    if spec == "auto":
        return _resolve_auto(spec, src, vendor, environ)
    if spec == "system":
        root = system_root(vendor, environ) or Path("/nonexistent/hyperframes")
        return Active(spec, src, _pkg_version(root), "system", root)
    if spec == "latest":
        cands = installed_versions(vendor)
        if _pkg_version(pinned_root(vendor)) == pin:
            cands.append(pin)
        machine = system_root(vendor, environ)
        mv = _pkg_version(machine) if machine else "missing"
        if mv != "missing":
            cands.append(mv)
        v = max(cands, key=version_key) if cands else pin
        if machine and mv == v:
            return Active(spec, src, mv, "system", machine)
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
            "run `engine doctor --install`" if a.spec in ("auto", "pinned") or a.source == "pinned" else \
            f"run `engine toolchain use {a.spec}`"
        raise EngineError(f"HyperFrames {a.label()} is not installed at {a.root}; {fix}")
    return a
