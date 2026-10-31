"""`engine doctor`: checks and installs the pinned HyperFrames toolchain inside the engine's vendor/ directory.

Components (pins in vendor/VERSIONS.json):
- node: official Linux x64 tarball, SHA-256 verified, unpacked to vendor/node.
- npm: hyperframes, puppeteer-core and three (models.py) from vendor/npm/package-lock.json via `npm ci` (vendored node), vendor/npm/node_modules.
- chrome: chrome-headless-shell via @puppeteer/browsers (dependency of puppeteer-core), vendor/chrome.

The engine resolves every toolchain path from vendor/ (config.py); no other location is read.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config
from .util import EngineError


def pins(vendor: Path | None = None) -> dict:
    return json.loads(((vendor or config.VENDOR) / "VERSIONS.json").read_text())


def paths(vendor: Path, p: dict) -> dict[str, Path]:
    c = p["chrome"]
    return {
        "node": vendor / "node" / "bin" / "node",
        "hf_cli": vendor / "npm" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs",
        "puppeteer": vendor / "npm" / "node_modules" / "puppeteer-core",
        "chrome": vendor / "chrome" / c["product"] / f"linux-{c['version']}" / f"{c['product']}-linux64" / c["product"],
    }


@dataclass
class Check:
    name: str
    ok: bool
    want: str
    got: str

    def line(self) -> str:
        return f"[{'OK' if self.ok else 'FAIL'}] {self.name}: want {self.want}, got {self.got}"


def _pkg_version(d: Path) -> str:
    try:
        return json.loads((d / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "missing"


def check(vendor: Path | None = None, run_binaries: bool = True) -> list[Check]:
    vendor = vendor or config.VENDOR
    p = pins(vendor)
    x = paths(vendor, p)
    out = []
    got = "missing"
    if x["node"].is_file():
        got = (subprocess.run([x["node"], "-v"], capture_output=True, text=True).stdout.strip() or "unrunnable") \
            if run_binaries else (vendor / "node" / ".version").read_text().strip() if (vendor / "node" / ".version").is_file() else "present"
    out.append(Check("node", got == p["node"]["version"], p["node"]["version"], got))
    nm = vendor / "npm" / "node_modules"
    for name, want in p["npm"].items():
        g = _pkg_version(nm / name)
        dist_missing = name == "hyperframes" and g == want and not (nm / name / "dist").is_dir()
        ok = g == want and not dist_missing
        out.append(Check(name, ok, want, f"{g} (dist missing)" if dist_missing else g))
    cli = x["hf_cli"]
    out.append(Check("hyperframes-cli", cli.is_file(), "bin/hyperframes.mjs",
                     "present" if cli.is_file() else "missing"))
    ch = x["chrome"]
    out.append(Check("chrome", ch.is_file() and bool(ch.stat().st_mode & 0o111), f"{p['chrome']['product']}@{p['chrome']['version']}",
                     "present" if ch.is_file() else "missing"))
    return out


def _sha256(f: Path) -> str:
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def install_node(vendor: Path, p: dict, tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    tar = tmp / Path(p["node"]["url"]).name
    urllib.request.urlretrieve(p["node"]["url"], tar)
    got = _sha256(tar)
    if got != p["node"]["sha256"]:
        tar.unlink(missing_ok=True)
        raise EngineError(f"node tarball sha256 mismatch: want {p['node']['sha256']}, got {got}")
    stage = tmp / "node_stage"
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(tar) as tf:
        tf.extractall(stage, filter="tar")
    (src,) = list(stage.iterdir())
    dst = vendor / "node"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.move(str(src), dst)
    (dst / ".version").write_text(p["node"]["version"] + "\n")
    tar.unlink(missing_ok=True)


def _node_env(vendor: Path, tmp: Path) -> dict:
    import os
    env = dict(os.environ)
    env["PATH"] = f"{vendor / 'node' / 'bin'}:{env.get('PATH', '')}"
    env["npm_config_cache"] = str(tmp / "npm-cache")
    env["npm_config_update_notifier"] = "false"
    return env


def install_npm(vendor: Path, tmp: Path) -> None:
    d = vendor / "npm"
    npm = vendor / "node" / "bin" / "npm"
    cmd = "ci" if (d / "package-lock.json").is_file() else "install"
    subprocess.run([npm, cmd, "--no-audit", "--no-fund", "--ignore-scripts"], cwd=d, env=_node_env(vendor, tmp), check=True)


def install_chrome(vendor: Path, p: dict, tmp: Path) -> None:
    pkg = vendor / "npm" / "node_modules" / "@puppeteer" / "browsers"
    try:
        b = json.loads((pkg / "package.json").read_text())["bin"]
        cli = pkg / (b if isinstance(b, str) else next(iter(b.values())))
    except (OSError, ValueError, KeyError):
        cli = pkg / "package.json"
    if not cli.is_file():
        raise EngineError(f"@puppeteer/browsers CLI missing at {cli}; install npm first")
    c = p["chrome"]
    subprocess.run([vendor / "node" / "bin" / "node", cli, "install", f"{c['product']}@{c['version']}",
                    "--path", vendor / "chrome"], env=_node_env(vendor, tmp), check=True)


def install(vendor: Path | None = None, tmp: Path | None = None) -> list[Check]:
    """Install only the components whose check fails. Order: node -> npm -> chrome."""
    vendor = vendor or config.VENDOR
    tmp = tmp or config.workdir("doctor")
    p = pins(vendor)
    status = {c.name: c.ok for c in check(vendor)}
    if not status["node"]:
        install_node(vendor, p, tmp)
    if not all(status[n] for n in p["npm"]) or not status["hyperframes-cli"]:
        install_npm(vendor, tmp)
    if not status["chrome"]:
        install_chrome(vendor, p, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return check(vendor)
