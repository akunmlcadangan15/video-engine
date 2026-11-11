"""`engine doctor`: checks tools on the machine and, with `--install`, fills in anything missing.

Default policy (`auto`): use what is already installed (portable discovery: env, npm globals, PATH,
platform dirs, user toolchain, then vendor/). `--install` downloads the latest release of a missing
tool into `<user-data>/video-engine/toolchain`, not the repo lockfile. Opt into the lockfile with
`VIDEO_ENGINE_HYPERFRAMES_VERSION=pinned` or `engine toolchain use pinned` (installs into vendor/).

ffmpeg is never vendored by `--install`; a build needs major version >= 7 and the listed filters/encoders.
`hyperframes-active` is the HyperFrames the build renders with (hfversion.py). FAIL only when it is not
installed. WARN only when the policy is `pinned` and the installed version is not the pin.
"""
from __future__ import annotations

import hashlib
import json
import os
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
    """{"version", "url", "sha256"} of the node tarball for platform `key` (default: the current platform)."""
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
    path: str = ""
    source: str = ""
    searched: tuple[str, ...] = ()

    def line(self) -> str:
        tag = "FAIL" if not self.ok else "WARN" if self.warn else "OK"
        msg = f"[{tag}] {self.name}: want {self.want}, got {self.got}"
        if self.path:
            loc = f" ({self.source})" if self.source else ""
            msg += f"\n     path: {self.path}{loc}"
        elif not self.ok and self.searched:
            msg += "\n     searched: " + ", ".join(self.searched)
        return msg


def _pkg_version(d: Path) -> str:
    try:
        return json.loads((d / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "missing"


def check(vendor: Path | None = None, run_binaries: bool = True, *, machine: bool = True,
          environ: dict | None = None, home: Path | None = None, system: str | None = None,
          confine: Path | None = None) -> list[Check]:
    """Vendor pin checks, then (when `machine` is true) the portable resolver so a tool
    present on PATH or in a global npm root is not reported as missing."""
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
        # npm ships in the official tarball (bin/npm -> lib/node_modules/npm). A tree with only the node
        # binary cannot run `doctor --install`'s npm ci; treat it as a failed node install.
        if not (x["node"].parent / "npm").exists():
            got = f"{got}, npm missing"
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
    # Vendor-only checks must not consult the host PATH or global npm roots.
    active_env = environ
    if not machine:
        active_env = {k: v for k, v in (environ or os.environ).items() if k.startswith("VIDEO_ENGINE_")}
        active_env.setdefault("PATH", "")
        active_env.setdefault("NODE_PATH", "")
        active_env.setdefault("HOME", str(vendor.parent))
        active_env.setdefault("USERPROFILE", str(vendor.parent))
    out.append(check_active(vendor, environ=active_env))
    ch = x["chrome"]
    out.append(Check("chrome", ch.is_file() and bool(ch.stat().st_mode & 0o111), f"{p['chrome']['product']}@{p['chrome']['version']}",
                     "present" if ch.is_file() else "missing"))
    if run_binaries:
        out.append(check_ffmpeg())
        out.append(check_ffmpeg_version())
    if machine:
        from . import hfversion
        policy = hfversion.setting(vendor, environ)[0]
        out = _merge_machine_discovery(out, vendor, environ=environ, home=home, system=system, confine=confine,
                                       warn_if_not_pin=(policy == "pinned"))
        if policy != "pinned":
            for c in out:
                if c.name in {"node", "hyperframes", "puppeteer-core", "three", "chrome", "hyperframes-cli"} and c.ok and c.warn and "differs from the pin" in c.got:
                    c.warn = False
    return out


def check_active(vendor: Path | None = None, environ: dict | None = None) -> Check:
    """The HyperFrames the build renders with (hfversion.resolve): FAIL when not installed (or the setting is
    invalid). WARN only when the policy is `pinned` and the installed version is not the pin."""
    from . import hfversion
    vendor = vendor or config.VENDOR
    pin = pins(vendor)["npm"]["hyperframes"]
    try:
        a = hfversion.resolve(vendor=vendor, environ=environ)
    except EngineError as e:
        return Check("hyperframes-active", False, f"valid {hfversion.ENV}", str(e))
    if not a.installed:
        return Check("hyperframes-active", False, f"{a.spec} installed", f"missing at {a.root}")
    warn = a.spec == "pinned" and a.version != pin
    return Check("hyperframes-active", True, f"{a.spec} (pin {pin})", a.label(), warn=warn)


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


def install(vendor: Path | None = None, tmp: Path | None = None, *, latest: bool | None = None) -> list[Check]:
    """Install only the components whose check fails. Default (`latest=True`, the `auto` policy): newest
    releases into the user toolchain directory. `latest=False` or the `pinned` policy: lockfile pins into vendor/."""
    from . import hfversion
    vendor = vendor or config.VENDOR
    platform_info.platform_key()   # unsupported platform: EngineError before anything is downloaded
    tmp = tmp or config.workdir("doctor")
    if latest is None:
        latest = hfversion.setting(vendor)[0] != "pinned"
    if latest:
        return install_latest(vendor=vendor, tmp=tmp)
    p = pins(vendor)
    status = {c.name: c.ok and not c.warn for c in check(vendor, machine=False)}
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




def latest_node_release(key: str | None = None) -> dict:
    """Newest official Node.js release (no pre-release). Prefers the latest LTS, else the newest current.
    Returns {version, url, sha256} for platform `key` (default: the current platform)."""
    key = key or platform_info.platform_key()
    plat = {"linux-x64": "linux-x64", "darwin-arm64": "darwin-arm64"}.get(key)
    if not plat:
        raise EngineError(f"no official Node tarball mapping for platform {key}")
    with urllib.request.urlopen("https://nodejs.org/dist/index.json", timeout=60) as r:
        data = json.loads(r.read().decode())
    stable = [d for d in data if d.get("version") and "-" not in d["version"]]
    lts = [d for d in stable if d.get("lts")]
    pick = lts[0] if lts else (stable[0] if stable else None)
    if not pick:
        raise EngineError("nodejs.org dist index has no stable release")
    ver = pick["version"]  # e.g. v22.23.5
    name = f"node-{ver}-{plat}.tar.xz"
    url = f"https://nodejs.org/dist/{ver}/{name}"
    with urllib.request.urlopen(f"https://nodejs.org/dist/{ver}/SHASUMS256.txt", timeout=60) as r:
        sums = r.read().decode()
    sha = next((line.split()[0] for line in sums.splitlines() if line.endswith(name) or line.endswith("  " + name)), None)
    if not sha:
        raise EngineError(f"no SHA-256 in SHASUMS256.txt for {name}")
    return {"version": ver, "url": url, "sha256": sha}


def install_latest_node(dest: Path, tmp: Path, key: str | None = None) -> Path:
    """Unpack the latest official Node tarball into dest/node (same layout as vendor/node)."""
    pin = latest_node_release(key)
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
    (src,) = [d for d in stage.iterdir() if not d.name.startswith("._")]
    dst = dest / "node"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.move(str(src), dst)
    (dst / ".version").write_text(pin["version"] + "\n")
    tar.unlink(missing_ok=True)
    return dst / "bin" / "node"


def _any_npm(vendor: Path, tmp: Path) -> tuple[Path, Path, dict]:
    """(node, npm, env) from PATH, the user toolchain, or vendor/node."""
    from . import discover
    node_res = discover.resolve_node(vendor=vendor, environ=os.environ)
    node = node_res.path if node_res.found else vendor / "node" / "bin" / "node"
    npm = (node.parent / "npm") if node else None
    if npm is None or not Path(npm).exists():
        found = shutil.which("npm")
        npm = Path(found) if found else vendor / "node" / "bin" / "npm"
    if not Path(npm).exists():
        raise EngineError("npm is missing; install Node first (`engine doctor --install`)")
    env = dict(os.environ)
    env["PATH"] = f"{Path(node).parent}:{env.get('PATH', '')}"
    env["npm_config_cache"] = str(tmp / "npm-cache")
    env["npm_config_update_notifier"] = "false"
    return Path(node), Path(npm), env


def install_latest_npm_packages(dest: Path, vendor: Path, tmp: Path, packages: list[str]) -> None:
    """`npm install <pkg>@latest` into dest/npm (not lockfile-pinned)."""
    d = dest / "npm"
    d.mkdir(parents=True, exist_ok=True)
    deps = {name: "latest" for name in packages}
    existing = d / "package.json"
    if existing.is_file():
        try:
            prev = json.loads(existing.read_text())
            deps = {**prev.get("dependencies", {}), **deps}
        except (OSError, ValueError):
            pass
    (d / "package.json").write_text(json.dumps({"name": "video-engine-toolchain", "private": True,
                                                "dependencies": deps}, indent=2) + "\n")
    node, npm, env = _any_npm(vendor, tmp)
    subprocess.run([npm, "install", "--no-audit", "--no-fund", "--ignore-scripts"],
                   cwd=d, env=env, check=True)


def install_latest_chrome(dest: Path, vendor: Path, tmp: Path) -> None:
    """Install the latest chrome-headless-shell via @puppeteer/browsers into dest/chrome."""
    node, npm, env = _any_npm(vendor, tmp)
    pkg = dest / "npm" / "node_modules" / "@puppeteer" / "browsers"
    if not (pkg / "package.json").is_file():
        pkg = vendor / "npm" / "node_modules" / "@puppeteer" / "browsers"
    if not (pkg / "package.json").is_file():
        subprocess.run([npm, "install", "@puppeteer/browsers@latest", "--no-audit", "--no-fund",
                        "--ignore-scripts"], cwd=dest / "npm" if (dest / "npm").is_dir() else dest,
                       env=env, check=True)
        pkg = dest / "npm" / "node_modules" / "@puppeteer" / "browsers"
    try:
        b = json.loads((pkg / "package.json").read_text())["bin"]
        cli = pkg / (b if isinstance(b, str) else next(iter(b.values())))
    except (OSError, ValueError, KeyError):
        cli = pkg / "lib" / "cjs" / "main-cli.js"
    if not cli.is_file():
        raise EngineError(f"@puppeteer/browsers CLI missing at {cli}")
    subprocess.run([node, cli, "install", "chrome-headless-shell@latest", "--path", dest / "chrome"],
                   env=env, check=True)


def install_latest(vendor: Path | None = None, tmp: Path | None = None,
                   environ: dict | None = None, home: Path | None = None) -> list[Check]:
    """Install the newest release of each missing tool into the user toolchain directory."""
    from . import discover
    vendor = vendor or config.VENDOR
    tmp = tmp or config.workdir("doctor")
    env = environ if environ is not None else os.environ
    dest = discover.toolchain_home(env, home=home)
    dest.mkdir(parents=True, exist_ok=True)
    disc = discover.resolve_all(vendor=vendor, environ=env, home=home)
    by = {r.name: r for r in disc.tools}
    if not by.get("node") or not by["node"].found:
        install_latest_node(dest, tmp)
        disc = discover.resolve_all(vendor=vendor, environ=env, home=home)
        by = {r.name: r for r in disc.tools}
    missing_pkg = [n for n in ("hyperframes", "puppeteer-core", "three") if not (by.get(n) and by[n].found)]
    if missing_pkg:
        install_latest_npm_packages(dest, vendor, tmp, missing_pkg)
    if not (by.get("chrome") and by["chrome"].found):
        install_latest_chrome(dest, vendor, tmp)
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



def _resolved_version(name: str, path: Path | None, detail: str = "") -> str:
    """Version string of the resolved tool (the binary or package actually selected)."""
    if detail and not detail.startswith("present"):
        return detail
    if path is None:
        return detail or "missing"
    try:
        if name == "node" and path.is_file():
            return (subprocess.run([str(path), "-v"], capture_output=True, text=True, timeout=15).stdout.strip()
                    or "present")
        if name in {"hyperframes", "puppeteer-core", "three"}:
            root = path if (path / "package.json").is_file() else (path if path.is_dir() else path.parent.parent)
            v = _pkg_version(root)
            return v if v != "missing" else (detail or "present")
    except (OSError, subprocess.SubprocessError):
        pass
    return detail or "present"


def _merge_machine_discovery(out: list[Check], vendor: Path, *, environ: dict | None, home: Path | None,
                             system: str | None, confine: Path | None, warn_if_not_pin: bool = False) -> list[Check]:
    """If a vendor pin check failed but the portable resolver found the tool, report it as found.
    When `warn_if_not_pin` (the `pinned` policy), a machine hit that is not the vendor pin is a WARN."""
    from . import discover
    env = environ if environ is not None else __import__("os").environ
    disc = discover.resolve_all(vendor=vendor, environ=env, system=system, home=home, confine=confine)
    by = {c.name: c for c in out}
    # Map resolver names to doctor check names
    aliases = {"puppeteer-core": "puppeteer-core", "hyperframes": "hyperframes", "ffprobe": "ffprobe",
               "ffmpeg": "ffmpeg", "python": "python", "modal": "modal", "node": "node", "chrome": "chrome",
               "three": "three"}
    merged = list(out)
    seen = {c.name for c in merged}
    for res in disc.tools:
        name = aliases.get(res.name, res.name)
        extra_kw = dict(path=str(res.path) if res.path else "", source=res.source, searched=res.searched)
        if name in by and res.found:
            c = by[name]
            was_ok = c.ok
            c.ok = True
            c.path = extra_kw["path"]
            c.source = res.source
            c.searched = res.searched
            c.got = _resolved_version(name, res.path, res.detail or "")
            if not warn_if_not_pin and name in {"node", "hyperframes", "puppeteer-core", "three", "chrome"}:
                c.want = "present"
                c.warn = False
            elif not was_ok:
                c.warn = warn_if_not_pin
        elif name not in seen:
            optional = name in {"python", "modal"}
            ok = res.found or optional
            merged.append(Check(name, ok, "present", res.detail or ("present" if res.found else "missing"),
                                warn=optional and not res.found,
                                path=extra_kw["path"], source=res.source if res.found else "",
                                searched=res.searched))
            seen.add(name)
        elif name in by and not res.found and not by[name].ok and not by[name].searched:
            by[name].searched = res.searched
    for res in disc.assets:
        if res.name in seen:
            continue
        merged.append(Check(res.name, True, "present", "present" if res.found else "missing",
                            warn=not res.found,
                            path=str(res.path) if res.path else "", source=res.source if res.found else "",
                            searched=res.searched))
        seen.add(res.name)
    return merged
