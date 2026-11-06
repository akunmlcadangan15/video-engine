"""`engine doctor`: checks and installs the pinned HyperFrames toolchain inside the engine's vendor/ directory.

Components (pins in vendor/VERSIONS.json):
- platform: linux-x64 or darwin-arm64 (platform_info.py); anything else fails the first check.
- node: official tarball for the platform (Linux x64 or macOS arm64), SHA-256 verified, unpacked to vendor/node.
- npm: hyperframes, puppeteer-core and three (models.py) from vendor/npm/package-lock.json via `npm ci` (vendored node), vendor/npm/node_modules.
  A hyperframes in vendor/npm that differs from the pin is a WARN, not a FAIL.
- hyperframes-active: the HyperFrames the build renders with (hfversion.py: VIDEO_ENGINE_HYPERFRAMES_VERSION or
  `engine toolchain use`); FAIL only when it is not installed, WARN when it is not the pinned version.
- chrome: chrome-headless-shell via @puppeteer/browsers (dependency of puppeteer-core), vendor/chrome.
- ffmpeg: system package, not vendored; version 7.x with the filters and encoders a build needs.

The engine resolves every toolchain path from vendor/ (config.py); no other location is read.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, platform_info
from .util import EngineError


def pins(vendor: Path | None = None) -> dict:
    return json.loads(((vendor or config.VENDOR) / "VERSIONS.json").read_text())


def node_pin(p: dict, key: str | None = None) -> dict:
    """{"version", "url", "sha256"} of the node tarball for platform `key` (default: this machine)."""
    key = key or platform_info.platform_key()
    try:
        return {"version": p["node"]["version"], **p["node"]["platforms"][key]}
    except KeyError:
        raise EngineError(f"vendor/VERSIONS.json has no node pin for platform {key}") from None


def paths(vendor: Path, p: dict, key: str | None = None) -> dict[str, Path]:
    c = p["chrome"]
    return {
        "node": vendor / "node" / "bin" / "node",
        "hf_cli": vendor / "npm" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs",
        "puppeteer": vendor / "npm" / "node_modules" / "puppeteer-core",
        "chrome": vendor / "chrome" / c["product"] / platform_info.chrome_relpath(c["product"], c["version"], key),
    }


@dataclass
class Check:
    name: str
    ok: bool
    want: str
    got: str
    warn: bool = False   # ok for `engine doctor`'s exit code, printed as WARN

    def line(self) -> str:
        tag = "FAIL" if not self.ok else "WARN" if self.warn else "OK"
        return f"[{tag}] {self.name}: want {self.want}, got {self.got}"


def _pkg_version(d: Path) -> str:
    try:
        return json.loads((d / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "missing"


def check(vendor: Path | None = None, run_binaries: bool = True) -> list[Check]:
    vendor = vendor or config.VENDOR
    p = pins(vendor)
    plat = check_platform()
    if not plat.ok:
        return [plat]   # no toolchain path or download is defined for this platform
    x = paths(vendor, p)
    out = [plat]
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
        if name == "hyperframes" and g != "missing" and not dist_missing and g != want:
            out.append(Check(name, True, want, f"{g} (differs from the pin)", warn=True))
            continue
        out.append(Check(name, ok, want, f"{g} (dist missing)" if dist_missing else g))
    cli = x["hf_cli"]
    out.append(Check("hyperframes-cli", cli.is_file(), "bin/hyperframes.mjs",
                     "present" if cli.is_file() else "missing"))
    out.append(check_active(vendor))
    ch = x["chrome"]
    out.append(Check("chrome", ch.is_file() and bool(ch.stat().st_mode & 0o111), f"{p['chrome']['product']}@{p['chrome']['version']}",
                     "present" if ch.is_file() else "missing"))
    if run_binaries:
        out.append(check_ffmpeg())
        out.append(check_ffmpeg_version())
    return out


def check_active(vendor: Path | None = None) -> Check:
    """The HyperFrames the build renders with (hfversion.resolve): FAIL when not installed (or the setting is
    invalid), WARN when it is not the pinned version."""
    from . import hfversion
    vendor = vendor or config.VENDOR
    pin = pins(vendor)["npm"]["hyperframes"]
    try:
        a = hfversion.resolve(vendor=vendor)
    except EngineError as e:
        return Check("hyperframes-active", False, f"valid {hfversion.ENV}", str(e))
    if not a.installed:
        return Check("hyperframes-active", False, f"{a.spec} installed", f"missing at {a.root}")
    return Check("hyperframes-active", True, f"{a.spec} (pin {pin})", a.label(), warn=a.version != pin)


def check_platform() -> Check:
    want = " or ".join(platform_info.SUPPORTED)
    try:
        return Check("platform", True, want, platform_info.platform_key())
    except EngineError as e:
        return Check("platform", False, want, str(e).split(";")[0])


# ffmpeg is a system package (not vendored, not installed by --install); a build needs these from config.FFMPEG.
FFMPEG_FILTERS = ("subtitles", "loudnorm", "sidechaincompress")
FFMPEG_ENCODERS = ("libx264", "aac")


def _ffmpeg_names(flag: str) -> set[str]:
    """Names listed by `ffmpeg -hide_banner <flag>` (-filters / -encoders): the second column of each row."""
    try:
        out = subprocess.run([config.FFMPEG, "-hide_banner", flag], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {p[1] for p in (line.split() for line in out.splitlines()) if len(p) > 1}


def check_ffmpeg() -> Check:
    filters, encoders = _ffmpeg_names("-filters"), _ffmpeg_names("-encoders")
    missing = [f for f in FFMPEG_FILTERS if f not in filters] + [e for e in FFMPEG_ENCODERS if e not in encoders]
    want = f"{config.FFMPEG} with {', '.join(FFMPEG_FILTERS + FFMPEG_ENCODERS)} (system package)"
    return Check("ffmpeg", not missing, want, f"missing {', '.join(missing)}" if missing else "present")


# The engine is built against ffmpeg 7.x and kept forward-compatible: newer majors are accepted as they land so the
# newest ffmpeg is usable. This is a floor, not a capped range; the filter/encoder check runs separately.
FFMPEG_MAJOR_MIN = 7


def parse_ffmpeg_major(text: str) -> int | None:
    """Major version from `ffmpeg -version` output: "ffmpeg version 7.1.1 ...", "n7.1-5-g...", "7.0.git"; None for
    builds without a release number ("N-12345-gabcdef" from git master)."""
    m = re.search(r"\bff(?:mpeg|probe) version [nN]?(\d+)", text or "")
    return int(m.group(1)) if m else None


def check_ffmpeg_version(text: str | None = None) -> Check:
    """FAIL unless config.FFMPEG is ffmpeg major >= FFMPEG_MAJOR_MIN (newer majors are accepted). `text` is the
    `-version` output (default: run it)."""
    if text is None:
        try:
            text = subprocess.run([config.FFMPEG, "-version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            text = ""
    first = next(iter(text.splitlines()), "").split(" Copyright")[0].removeprefix("ffmpeg version ").strip()
    major = parse_ffmpeg_major(text)
    got = first if major is not None else (f"unparseable ({first[:60]})" if first else "unrunnable")
    ok = major is not None and major >= FFMPEG_MAJOR_MIN
    return Check("ffmpeg-version", ok, f"{FFMPEG_MAJOR_MIN}.x or newer", got)


def _sha256(f: Path) -> str:
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def install_node(vendor: Path, p: dict, tmp: Path) -> None:
    pin = node_pin(p)
    tmp.mkdir(parents=True, exist_ok=True)
    tar = tmp / Path(pin["url"]).name
    urllib.request.urlretrieve(pin["url"], tar)
    got = _sha256(tar)
    if got != pin["sha256"]:
        tar.unlink(missing_ok=True)
        raise EngineError(f"node tarball sha256 mismatch: want {pin['sha256']}, got {got}")
    stage = tmp / "node_stage"
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(tar) as tf:
        tf.extractall(stage, filter="tar")
    # One top-level dir whose name carries the platform (node-vX-linux-x64, node-vX-darwin-arm64); "._*" are macOS metadata.
    (src,) = [d for d in stage.iterdir() if not d.name.startswith("._")]
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
    platform_info.platform_key()   # unsupported platform: EngineError before anything is downloaded
    tmp = tmp or config.workdir("doctor")
    p = pins(vendor)
    status = {c.name: c.ok and not c.warn for c in check(vendor)}
    if not status["node"]:
        install_node(vendor, p, tmp)
    if not all(status[n] for n in p["npm"]) or not status["hyperframes-cli"]:
        install_npm(vendor, tmp)
    if not status["chrome"]:
        install_chrome(vendor, p, tmp)
    from . import hfversion
    try:
        a = hfversion.resolve(vendor=vendor)
        if a.source == "vendor" and not a.installed:
            install_hf_version(vendor, a.root.parent.parent.name, tmp)
    except EngineError:
        pass   # an invalid setting stays a FAIL of hyperframes-active
    shutil.rmtree(tmp, ignore_errors=True)
    return check(vendor)


def npm_latest(vendor: Path | None = None, tmp: Path | None = None) -> str:
    """Newest hyperframes version on the npm registry (`npm view hyperframes version`, vendored npm)."""
    from . import hfversion
    vendor = vendor or config.VENDOR
    tmp = tmp or config.workdir("doctor")
    r = subprocess.run([vendor / "node" / "bin" / "npm", "view", "hyperframes", "version"], capture_output=True,
                       text=True, env=_node_env(vendor, tmp), timeout=120, check=False)
    v = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
    if r.returncode or hfversion.parse_spec(v or "?") in hfversion.KEYWORDS:
        raise EngineError(f"npm view hyperframes version failed: {(r.stderr or r.stdout).strip()[:300]}")
    return v


def install_hf_version(vendor: Path, version: str, tmp: Path) -> Path:
    """Install hyperframes@VERSION (and its own dependencies) into vendor/hf/VERSION with the vendored npm. Not
    lockfile-pinned like vendor/npm. Install scripts are skipped as for vendor/npm (esbuild ships its binary as an
    optional dependency, so no postinstall is needed); the CLI and esbuild binary are checked after the install."""
    from . import hfversion
    version = hfversion.parse_spec(version)
    if version in hfversion.KEYWORDS:
        raise EngineError(f"install_hf_version needs an exact version, got {version!r}")
    d = vendor / "hf" / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps({"name": "video-engine-hf", "private": True,
                                                 "dependencies": {"hyperframes": version}}, indent=2) + "\n")
    subprocess.run([vendor / "node" / "bin" / "npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"],
                   cwd=d, env=_node_env(vendor, tmp), check=True)
    root = hfversion.vendor_root(version, vendor)
    got = _pkg_version(root)
    if got != version or not (root / "bin" / "hyperframes.mjs").is_file() or not (root / "dist").is_dir():
        raise EngineError(f"hyperframes@{version} install in {d} incomplete (got {got})")
    esb = d / "node_modules" / "esbuild" / "bin" / "esbuild"
    if esb.is_file() and subprocess.run([esb, "--version"], capture_output=True, check=False).returncode:
        raise EngineError(f"esbuild in {d} does not run; reinstall without --ignore-scripts")
    return root
