"""Portable resolution of external tools and asset library roots.

Search order for a tool (a step is skipped when it does not apply). The default
prefers anything already on the machine; the repo pin in vendor/ is a fallback, not a lock.
Pass `prefer_vendor=True` to search vendor immediately after the env override (the
`pinned` toolchain policy).

1. Explicit override (`VIDEO_ENGINE_<NAME>` or a documented sibling variable).
2. Global npm package roots (`NODE_PATH`, `npm root -g` when a callback is given, then
   platform-typical global npm directories).
3. `PATH` (`shutil.which`, with `PATHEXT` on Windows).
4. Platform-standard cache and data directories (XDG on Linux, `~/Library` on macOS,
   `%LOCALAPPDATA%` / `%APPDATA%` on Windows), the engine user-toolchain directory
   (`<user-data>/video-engine/toolchain`), Puppeteer's browser cache, and common
   install locations.
5. The engine's vendor directory (`VIDEO_ENGINE_VENDOR`, else `<repo>/vendor`).

Asset roots are a configurable list (`VIDEO_ENGINE_ASSET_ROOTS`, plus the existing
per-library variables). Generic defaults live under the user data directory, not a
machine-specific folder. Machine-specific paths belong in a local `engine.env`.

Every public function is side-effect free given `environ`, `system` and `home`:
tests pass a fake tree and a platform string; they consult only that tree, not the host home, PATH, or config.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_Env = Mapping[str, str]


def _is_under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def is_file(path: Path, confine: Path | None = None) -> bool:
    """Like Path.is_file, but if `confine` is set the path must sit inside it (hermetic tests)."""
    if confine is not None and not _is_under(path, confine):
        return False
    return path.is_file()


def is_dir(path: Path, confine: Path | None = None) -> bool:
    if confine is not None and not _is_under(path, confine):
        return False
    return path.is_dir()


def exists(path: Path, confine: Path | None = None) -> bool:
    return is_file(path, confine) or is_dir(path, confine)


def platform_family(system: str | None = None) -> str:
    """'linux', 'darwin' or 'win32' from a `sys.platform`-style string."""
    s = (system if system is not None else sys.platform).lower()
    if s.startswith("linux"):
        return "linux"
    if s.startswith("darwin"):
        return "darwin"
    if s.startswith("win"):
        return "win32"
    return s


def _home(environ: _Env, home: Path | None = None) -> Path:
    if home is not None:
        return Path(home)
    h = environ.get("HOME") or environ.get("USERPROFILE")
    return Path(h) if h else Path.home()


def user_data_dir(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """User data directory: $XDG_DATA_HOME or ~/.local/share; ~/Library/Application Support; %LOCALAPPDATA%."""
    env, fam, h = environ or os.environ, platform_family(system), _home(environ or os.environ, home)
    if fam == "linux":
        return Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else h / ".local" / "share"
    if fam == "darwin":
        return h / "Library" / "Application Support"
    return Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else h / "AppData" / "Local"


def user_cache_dir(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """User cache directory: $XDG_CACHE_HOME or ~/.cache; ~/Library/Caches; %LOCALAPPDATA%."""
    env, fam, h = environ or os.environ, platform_family(system), _home(environ or os.environ, home)
    if fam == "linux":
        return Path(env["XDG_CACHE_HOME"]) if env.get("XDG_CACHE_HOME") else h / ".cache"
    if fam == "darwin":
        return h / "Library" / "Caches"
    return Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else h / "AppData" / "Local"


def user_config_dir(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """User config directory: $XDG_CONFIG_HOME or ~/.config; ~/Library/Application Support; %APPDATA%."""
    env, fam, h = environ or os.environ, platform_family(system), _home(environ or os.environ, home)
    if fam == "linux":
        return Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else h / ".config"
    if fam == "darwin":
        return h / "Library" / "Application Support"
    return Path(env["APPDATA"]) if env.get("APPDATA") else h / "AppData" / "Roaming"


def user_bin_dir(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """User-writable bin directory: ~/.local/bin on Unix; %LOCALAPPDATA%\\Programs on Windows."""
    fam, h = platform_family(system), _home(environ or os.environ, home)
    if fam == "win32":
        data = user_data_dir(environ, system, home)
        return data / "Programs"
    return h / ".local" / "bin"


def default_workspace(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """Default shared-library folder: `<user-data>/video-engine`. Override with VIDEO_ENGINE_WORKSPACE."""
    env = environ or os.environ
    if env.get("VIDEO_ENGINE_WORKSPACE"):
        return Path(env["VIDEO_ENGINE_WORKSPACE"])
    return user_data_dir(env, system, home) / "video-engine"


def toolchain_home(environ: _Env | None = None, system: str | None = None, home: Path | None = None) -> Path:
    """Directory `engine doctor --install` writes latest tool builds into (not the repo pin tree)."""
    return default_workspace(environ, system, home) / "toolchain"


# ---------------------------------------------------------------------------
# Env file (local, untracked machine config)
# ---------------------------------------------------------------------------

def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=value lines (`export KEY=value` accepted). Quotes are stripped. No command substitution."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def env_file_candidates(environ: _Env | None = None, system: str | None = None, home: Path | None = None,
                        cwd: Path | None = None) -> list[Path]:
    """Places a local engine.env may live, first existing wins: $VIDEO_ENGINE_ENV_FILE, ./engine.env,
    `<user-config>/video-engine/engine.env`."""
    env = environ or os.environ
    out: list[Path] = []
    if env.get("VIDEO_ENGINE_ENV_FILE"):
        out.append(Path(env["VIDEO_ENGINE_ENV_FILE"]))
    out.append((cwd if cwd is not None else Path.cwd()) / "engine.env")
    out.append(user_config_dir(env, system, home) / "video-engine" / "engine.env")
    return out


def load_engine_env(environ: _Env | None = None, system: str | None = None, home: Path | None = None,
                    cwd: Path | None = None) -> dict[str, str]:
    """Variables from the first candidate file that exists. An explicit VIDEO_ENGINE_ENV_FILE that is
    missing yields an empty dict here (wrappers may still treat that as an error)."""
    env = dict(environ or os.environ)
    explicit = env.get("VIDEO_ENGINE_ENV_FILE")
    for path in env_file_candidates(env, system, home, cwd):
        if explicit and path == Path(explicit) and not path.is_file():
            return {}
        if path.is_file():
            try:
                return parse_env_file(path.read_text(encoding="utf-8"))
            except OSError:
                return {}
        if explicit and path == Path(explicit):
            return {}
    return {}


# ---------------------------------------------------------------------------
# PATH lookup
# ---------------------------------------------------------------------------

def path_entries(environ: _Env, system: str | None = None) -> list[Path]:
    raw = environ.get("PATH") or environ.get("Path") or ""
    sep = ";" if platform_family(system) == "win32" else (os.pathsep if system is None else ":")
    if system is None:
        sep = os.pathsep
    return [Path(p) for p in raw.split(sep) if p]


def _pathext(environ: _Env, system: str | None) -> list[str]:
    if platform_family(system) != "win32":
        return [""]
    ext = environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return [""] + [e if e.startswith(".") else f".{e}" for e in ext.split(";") if e]


def which(name: str, environ: _Env | None = None, system: str | None = None) -> Path | None:
    """Locate an executable on PATH. Uses shutil.which when `system` matches this interpreter,
    otherwise walks PATH (and PATHEXT on Windows) so tests can mock another OS."""
    env = environ or os.environ
    # An explicit PATH (even empty) is the whole search path: do not fall back to this process's PATH.
    if "PATH" in env:
        path_arg = env["PATH"]
        path_given = True
    elif "Path" in env:
        path_arg = env["Path"]
        path_given = True
    else:
        path_arg, path_given = None, False
    if system is None or platform_family(system) == platform_family(sys.platform):
        found = shutil.which(name, path=path_arg) if path_given else shutil.which(name)
        return Path(found) if found else None
    exts = _pathext(env, system)
    # Windows PATHEXT is compared case-insensitively; also try the lower-case form so mocked
    # tests on a case-sensitive filesystem still find `ffmpeg.exe`.
    expanded: list[str] = []
    for e in exts:
        for variant in (e, e.lower(), e.upper()):
            if variant not in expanded:
                expanded.append(variant)
    base = name if Path(name).suffix else name
    for folder in path_entries(env, system):
        for ext in expanded:
            cand = folder / (base if ext == "" or base.lower().endswith(ext.lower()) else base + ext)
            if cand.is_file():
                return cand
    return None


# ---------------------------------------------------------------------------
# npm global roots
# ---------------------------------------------------------------------------

NpmRootFn = Callable[[], Path | None]


def default_npm_global_dirs(environ: _Env, system: str | None, home: Path | None = None) -> list[Path]:
    """Typical global npm directories for the platform, in search order. No subprocess."""
    fam, h = platform_family(system), _home(environ, home)
    out: list[Path] = []
    # NODE_PATH is a list of node_modules directories (or their parents; both are tried later).
    sep = ";" if fam == "win32" else ":"
    if system is None:
        sep = os.pathsep
    for raw in (environ.get("NODE_PATH") or "").split(sep):
        if raw:
            out.append(Path(raw))
    if fam == "win32":
        appdata = Path(environ["APPDATA"]) if environ.get("APPDATA") else h / "AppData" / "Roaming"
        local = Path(environ["LOCALAPPDATA"]) if environ.get("LOCALAPPDATA") else h / "AppData" / "Local"
        pf = Path(environ.get("ProgramFiles") or environ.get("PROGRAMFILES") or "C:/Program Files")
        pf86 = Path(environ.get("ProgramFiles(x86)") or r"C:/Program Files (x86)")
        out.extend([
            appdata / "npm" / "node_modules",
            local / "npm" / "node_modules",
            pf / "nodejs" / "node_modules",
            pf86 / "nodejs" / "node_modules",
        ])
    else:
        out.extend([
            h / ".local" / "lib" / "node_modules",
            Path("/usr/local/lib/node_modules"),
            Path("/usr/lib/node_modules"),
        ])
        if fam == "darwin":
            out.extend([
                Path("/opt/homebrew/lib/node_modules"),
                Path("/usr/local/opt/node/lib/node_modules"),
            ])
        if fam == "linux":
            out.append(Path("/usr/lib/node_modules"))  # already listed; keep list stable
    return out


def npm_global_roots(environ: _Env | None = None, system: str | None = None, home: Path | None = None,
                     npm_root_g: Path | NpmRootFn | None = None) -> list[Path]:
    """Deduplicated npm global `node_modules` directories. `npm_root_g` is a path or a zero-arg callback
    (production may pass the result of `npm root -g`); it is tried first after NODE_PATH."""
    env = environ or os.environ
    roots: list[Path] = []
    extra = npm_root_g() if callable(npm_root_g) else npm_root_g
    if extra is not None:
        roots.append(Path(extra))
    roots.extend(default_npm_global_dirs(env, system, home))
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = r.resolve() if r.exists() else r
        except OSError:
            key = r
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def npm_package(root: Path, name: str, confine: Path | None = None) -> Path | None:
    """`root` may be a `node_modules` dir or its parent; return the package dir if it looks installed."""
    candidates = [root / name, root / "node_modules" / name]
    if root.name == name and is_file(root / "package.json", confine):
        candidates.insert(0, root)
    for c in candidates:
        if is_file(c / "package.json", confine):
            return c
    return None


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Resolution:
    """One tool or asset root after search."""
    name: str
    found: bool
    path: Path | None
    source: str  # "env:VIDEO_ENGINE_NODE", "vendor", "npm_global", "PATH", "wellknown", "workspace", "missing"
    searched: tuple[str, ...] = ()
    detail: str = ""
    kind: str = "tool"  # "tool" | "asset"


def _ok(name: str, path: Path, source: str, searched: Sequence[str], detail: str = "", kind: str = "tool") -> Resolution:
    return Resolution(name, True, path, source, tuple(searched), detail, kind)


def _miss(name: str, searched: Sequence[str], detail: str = "", kind: str = "tool") -> Resolution:
    return Resolution(name, False, None, "missing", tuple(searched), detail, kind)


# ---------------------------------------------------------------------------
# Vendor layout helpers
# ---------------------------------------------------------------------------

def vendor_dir(environ: _Env, engine_root: Path) -> Path:
    return Path(environ["VIDEO_ENGINE_VENDOR"]) if environ.get("VIDEO_ENGINE_VENDOR") else engine_root / "vendor"


def _node_bin(vendor: Path, system: str | None) -> Path:
    name = "node.exe" if platform_family(system) == "win32" else "node"
    return vendor / "node" / "bin" / name


# ---------------------------------------------------------------------------
# Tool resolvers
# ---------------------------------------------------------------------------

_NODE_NAMES = {"win32": ("node.exe", "node"), "linux": ("node",), "darwin": ("node",)}
_FFMPEG_NAMES = {"win32": ("ffmpeg.exe", "ffmpeg"), "linux": ("ffmpeg",), "darwin": ("ffmpeg",)}
_FFPROBE_NAMES = {"win32": ("ffprobe.exe", "ffprobe"), "linux": ("ffprobe",), "darwin": ("ffprobe",)}
_MODAL_NAMES = {"win32": ("modal.exe", "modal"), "linux": ("modal",), "darwin": ("modal",)}


def _exe_names(table: dict[str, tuple[str, ...]], system: str | None) -> tuple[str, ...]:
    return table.get(platform_family(system), ("ffmpeg",))


def wellknown_chrome(environ: _Env, system: str | None, home: Path | None) -> list[Path]:
    """Common Chrome / Chromium binary locations for the platform (may not exist)."""
    fam, h = platform_family(system), _home(environ, home)
    if fam == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            h / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
    if fam == "win32":
        local = Path(environ["LOCALAPPDATA"]) if environ.get("LOCALAPPDATA") else h / "AppData" / "Local"
        pf = Path(environ.get("ProgramFiles") or environ.get("PROGRAMFILES") or "C:/Program Files")
        pf86 = Path(environ.get("ProgramFiles(x86)") or r"C:/Program Files (x86)")
        rel = Path("Google") / "Chrome" / "Application" / "chrome.exe"
        return [local / rel, pf / rel, pf86 / rel, pf / "Chromium" / "Application" / "chrome.exe"]
    # linux and other Unix
    return [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/snap/bin/chromium"),
        Path("/opt/google/chrome/chrome"),
        Path("/usr/bin/chrome"),
    ]


def puppeteer_cache_bins(cache: Path, product: str = "chrome-headless-shell") -> list[Path]:
    """Executables under a Puppeteer browser cache (any installed revision)."""
    found: list[Path] = []
    root = cache / "puppeteer"
    if not root.is_dir():
        # some versions use the cache dir itself
        root = cache
    if not root.is_dir():
        return found
    names = {product, "chrome-headless-shell", "chrome", "chrome.exe", "chrome-headless-shell.exe"}
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.name in names and os.access(p, os.X_OK) or (p.is_file() and p.suffix.lower() == ".exe" and p.stem in {"chrome", "chrome-headless-shell"}):
                if p.name in names:
                    found.append(p)
    except OSError:
        return found
    return found


def wellknown_ffmpeg(environ: _Env, system: str | None, home: Path | None, probe: bool = False) -> list[Path]:
    fam = platform_family(system)
    name = "ffprobe" if probe else "ffmpeg"
    exe = f"{name}.exe" if fam == "win32" else name
    if fam == "win32":
        pf = Path(environ.get("ProgramFiles") or environ.get("PROGRAMFILES") or "C:/Program Files")
        return [pf / "ffmpeg" / "bin" / exe]
    out = [
        user_bin_dir(environ, system, home) / exe,
        toolchain_home(environ, system, home) / "ffmpeg" / "bin" / exe,
        Path(f"/usr/bin/{name}"),
        Path(f"/usr/local/bin/{name}"),
    ]
    if fam == "darwin":
        out.extend([Path(f"/opt/homebrew/bin/{name}"), Path(f"/usr/local/opt/ffmpeg/bin/{name}")])
    return out


def wellknown_node(environ: _Env, system: str | None, home: Path | None) -> list[Path]:
    fam = platform_family(system)
    if fam == "win32":
        pf = Path(environ.get("ProgramFiles") or environ.get("PROGRAMFILES") or "C:/Program Files")
        return [pf / "nodejs" / "node.exe"]
    out = [
        toolchain_home(environ, system, home) / "node" / "bin" / ("node.exe" if fam == "win32" else "node"),
        user_bin_dir(environ, system, home) / ("node.exe" if fam == "win32" else "node"),
        Path("/usr/bin/node"),
        Path("/usr/local/bin/node"),
    ]
    if fam == "darwin":
        out.extend([Path("/opt/homebrew/bin/node"), Path("/usr/local/opt/node/bin/node")])
    return out


def _first_file(candidates: Iterable[Path], confine: Path | None = None) -> Path | None:
    for p in candidates:
        if is_file(p, confine):
            return p
    return None


def _first_dir(candidates: Iterable[Path], confine: Path | None = None) -> Path | None:
    for p in candidates:
        if is_dir(p, confine):
            return p
    return None


def resolve_node(*, vendor: Path, environ: _Env, system: str | None = None, home: Path | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    searched: list[str] = []
    env_var = "VIDEO_ENGINE_NODE"
    searched.append(f"env:{env_var}")
    if environ.get(env_var):
        p = Path(environ[env_var])
        if is_file(p, confine):
            return _ok("node", p, f"env:{env_var}", searched)

    def _vendor() -> Resolution | None:
        vend = _node_bin(vendor, system)
        searched.append(f"vendor:{vend}")
        if is_file(vend, confine):
            return _ok("node", vend, "vendor", searched)
        return None

    def _machine() -> Resolution | None:
        for n in _exe_names(_NODE_NAMES, system):
            searched.append(f"PATH:{n}")
            hit = which(n, environ, system)
            if hit is not None and is_file(hit, confine):
                return _ok("node", hit, "PATH", searched)
        home_node = toolchain_home(environ, system, home)
        for p in wellknown_node(environ, system, home):
            searched.append(f"wellknown:{p}")
            if is_file(p, confine):
                try:
                    src = "user_toolchain" if p.resolve().is_relative_to(home_node.resolve()) else "wellknown"
                except (OSError, ValueError):
                    src = "wellknown"
                return _ok("node", p, src, searched)
        return None

    order = (_vendor, _machine) if prefer_vendor else (_machine, _vendor)
    for step in order:
        if (hit := step()) is not None:
            return hit
    return _miss("node", searched)


def resolve_ffmpeg(*, environ: _Env, system: str | None = None, home: Path | None = None,
                   probe: bool = False,
                        confine: Path | None = None) -> Resolution:
    name = "ffprobe" if probe else "ffmpeg"
    env_var = "VIDEO_ENGINE_FFPROBE" if probe else "VIDEO_ENGINE_FFMPEG"
    searched = [f"env:{env_var}"]
    if environ.get(env_var):
        raw = environ[env_var]
        p = Path(raw)
        if is_file(p, confine):
            return _ok(name, p, f"env:{env_var}", searched)
        hit = which(raw, environ, system)
        if hit is not None and is_file(hit, confine):
            return _ok(name, hit, f"env:{env_var}", searched)
        searched.append(f"PATH-from-env:{raw}")
    for n in _exe_names(_FFPROBE_NAMES if probe else _FFMPEG_NAMES, system):
        searched.append(f"PATH:{n}")
        hit = which(n, environ, system)
        if hit is not None and is_file(hit, confine):
            return _ok(name, hit, "PATH", searched)
    for p in wellknown_ffmpeg(environ, system, home, probe=probe):
        searched.append(f"wellknown:{p}")
        if is_file(p, confine):
            return _ok(name, p, "wellknown", searched)
    return _miss(name, searched)


def resolve_modal(*, environ: _Env, system: str | None = None, home: Path | None = None,
                        confine: Path | None = None) -> Resolution:
    searched = ["env:VIDEO_ENGINE_MODAL"]
    if environ.get("VIDEO_ENGINE_MODAL"):
        p = Path(environ["VIDEO_ENGINE_MODAL"])
        if is_file(p, confine):
            return _ok("modal", p, "env:VIDEO_ENGINE_MODAL", searched)
        hit = which(environ["VIDEO_ENGINE_MODAL"], environ, system)
        if hit is not None and is_file(hit, confine):
            return _ok("modal", hit, "env:VIDEO_ENGINE_MODAL", searched)
    for n in _exe_names(_MODAL_NAMES, system):
        searched.append(f"PATH:{n}")
        hit = which(n, environ, system)
        if hit is not None and is_file(hit, confine):
            return _ok("modal", hit, "PATH", searched)
    bindir = user_bin_dir(environ, system, home)
    for n in _exe_names(_MODAL_NAMES, system):
        cand = bindir / n
        searched.append(f"wellknown:{cand}")
        if is_file(cand, confine):
            return _ok("modal", cand, "wellknown", searched)
    return _miss("modal", searched)


def resolve_npm_package(name: str, *, vendor: Path, environ: _Env, system: str | None = None,
                        home: Path | None = None, env_var: str | None = None,
                        npm_root_g: Path | NpmRootFn | None = None,
                        cli_name: str | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    """Locate an npm package directory (package.json present)."""
    var = env_var or f"VIDEO_ENGINE_{name.upper().replace('-', '_')}"
    searched = [f"env:{var}"]
    if environ.get(var):
        p = Path(environ[var])
        got = npm_package(p, name, confine) or (p if is_file(p / "package.json", confine) or is_dir(p / "bin", confine) else None)
        if got is not None:
            return _ok(name, got, f"env:{var}", searched)
        if is_file(p, confine) and cli_name:  # a CLI path was given
            return _ok(name, p, f"env:{var}", searched)

    def _vendor() -> Resolution | None:
        vend = vendor / "npm" / "node_modules" / name
        searched.append(f"vendor:{vend}")
        if is_file(vend / "package.json", confine):
            return _ok(name, vend, "vendor", searched)
        return None

    def _machine() -> Resolution | None:
        for root in npm_global_roots(environ, system, home, npm_root_g):
            searched.append(f"npm_global:{root}")
            got = npm_package(root, name, confine)
            if got is not None:
                return _ok(name, got, "npm_global", searched)
        if cli_name:
            searched.append(f"PATH:{cli_name}")
            hit = which(cli_name, environ, system)
            if hit is not None and is_file(hit, confine):
                pkg = hit.resolve().parent.parent if hit.is_file() else None
                if pkg is not None and is_file(pkg / "package.json", confine):
                    return _ok(name, pkg, "PATH", searched)
                return _ok(name, hit, "PATH", searched)
        home_nm = toolchain_home(environ, system, home) / "npm" / "node_modules"
        searched.append(f"user_toolchain:{home_nm / name}")
        got = npm_package(home_nm, name, confine)
        if got is not None:
            return _ok(name, got, "user_toolchain", searched)
        cache = user_cache_dir(environ, system, home) / name
        searched.append(f"wellknown:{cache}")
        if is_file(cache / "package.json", confine):
            return _ok(name, cache, "wellknown", searched)
        return None

    order = (_vendor, _machine) if prefer_vendor else (_machine, _vendor)
    for step in order:
        if (hit := step()) is not None:
            return hit
    return _miss(name, searched)


def resolve_hyperframes(*, vendor: Path, environ: _Env, system: str | None = None, home: Path | None = None,
                        npm_root_g: Path | NpmRootFn | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    return resolve_npm_package("hyperframes", vendor=vendor, environ=environ, system=system, home=home,
                               env_var="VIDEO_ENGINE_HYPERFRAMES", npm_root_g=npm_root_g, cli_name="hyperframes",
                               confine=confine, prefer_vendor=prefer_vendor)


def resolve_puppeteer(*, vendor: Path, environ: _Env, system: str | None = None, home: Path | None = None,
                      npm_root_g: Path | NpmRootFn | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    return resolve_npm_package("puppeteer-core", vendor=vendor, environ=environ, system=system, home=home,
                               env_var="VIDEO_ENGINE_PUPPETEER", npm_root_g=npm_root_g, confine=confine,
                               prefer_vendor=prefer_vendor)


def resolve_three(*, vendor: Path, environ: _Env, system: str | None = None, home: Path | None = None,
                  npm_root_g: Path | NpmRootFn | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    return resolve_npm_package("three", vendor=vendor, environ=environ, system=system, home=home,
                               env_var="VIDEO_ENGINE_THREE", npm_root_g=npm_root_g, confine=confine,
                               prefer_vendor=prefer_vendor)


def _vendor_chrome(vendor: Path, system: str | None) -> Path | None:
    """Pinned chrome-headless-shell path when VERSIONS.json and platform_info can name it; else any binary under vendor/chrome."""
    try:
        from . import platform_info
        pins_path = vendor / "VERSIONS.json"
        if not pins_path.is_file():
            return None
        import json
        pins = json.loads(pins_path.read_text())
        ch = pins["chrome"]
        key = None
        # Map sys.platform-style system to a pin key when possible; tests may pass linux/darwin/win32.
        fam = platform_family(system)
        if fam == "linux":
            key = "linux-x64" if "linux-x64" in (pins.get("node") or {}).get("platforms", {}) else None
        elif fam == "darwin":
            key = "darwin-arm64" if "darwin-arm64" in (pins.get("node") or {}).get("platforms", {}) else None
        if key and key in platform_info._CHROME:
            return vendor / "chrome" / ch["product"] / platform_info.chrome_relpath(ch["product"], ch["version"], key)
    except (OSError, KeyError, ValueError, TypeError, ImportError):
        pass
    return None


def resolve_chrome(*, vendor: Path, environ: _Env, system: str | None = None, home: Path | None = None,
                        confine: Path | None = None, prefer_vendor: bool = False) -> Resolution:
    searched = ["env:VIDEO_ENGINE_CHROME"]
    if environ.get("VIDEO_ENGINE_CHROME"):
        p = Path(environ["VIDEO_ENGINE_CHROME"])
        if is_file(p, confine):
            return _ok("chrome", p, "env:VIDEO_ENGINE_CHROME", searched)
        hit = which(environ["VIDEO_ENGINE_CHROME"], environ, system)
        if hit is not None and is_file(hit, confine):
            return _ok("chrome", hit, "env:VIDEO_ENGINE_CHROME", searched)

    def _under(root: Path, source: str) -> Resolution | None:
        if not is_dir(root, confine):
            return None
        for n in ("chrome-headless-shell", "chrome-headless-shell.exe", "chrome", "chrome.exe"):
            try:
                hits = list(root.rglob(n))
            except OSError:
                hits = []
            for h in hits:
                if is_file(h, confine):
                    return _ok("chrome", h, source, searched)
        return None

    def _vendor() -> Resolution | None:
        vend = _vendor_chrome(vendor, system)
        if vend is not None:
            searched.append(f"vendor:{vend}")
            if is_file(vend, confine):
                return _ok("chrome", vend, "vendor", searched)
            return None
        searched.append(f"vendor:{vendor / 'chrome'}")
        return _under(vendor / "chrome", "vendor")

    def _headless_machine() -> Resolution | None:
        cache = user_cache_dir(environ, system, home)
        searched.append(f"wellknown:{cache / 'puppeteer'}")
        for bin_path in puppeteer_cache_bins(cache):
            if is_file(bin_path, confine):
                return _ok("chrome", bin_path, "wellknown", searched)
        home_ch = toolchain_home(environ, system, home) / "chrome"
        searched.append(f"user_toolchain:{home_ch}")
        return _under(home_ch, "user_toolchain")

    def _full_chrome() -> Resolution | None:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "chrome.exe"):
            searched.append(f"PATH:{n}")
            hit = which(n, environ, system)
            if hit is not None and is_file(hit, confine):
                return _ok("chrome", hit, "PATH", searched)
        for p in wellknown_chrome(environ, system, home):
            searched.append(f"wellknown:{p}")
            if is_file(p, confine):
                return _ok("chrome", p, "wellknown", searched)
        return None

    # Prefer chrome-headless-shell (machine cache, user toolchain, then vendor) over a full GUI Chrome,
    # so a render machine with both does not silently switch to /usr/bin/google-chrome.
    if prefer_vendor:
        steps = (_vendor, _headless_machine, _full_chrome)
    else:
        steps = (_headless_machine, _vendor, _full_chrome)
    for step in steps:
        if (found := step()) is not None:
            return found
    return _miss("chrome", searched)


def resolve_python(*, environ: _Env, system: str | None = None, home: Path | None = None,
                   require_pillow: bool = True,
                   pillow_ok: Callable[[Path], bool] | None = None,
                        confine: Path | None = None) -> Resolution:
    """Python interpreter, preferring one that can import Pillow when `require_pillow` is true."""
    searched = ["env:VIDEO_ENGINE_PYTHON", "env:PYTHON", "env:VIRTUAL_ENV"]
    candidates: list[tuple[str, Path]] = []
    if environ.get("VIDEO_ENGINE_PYTHON"):
        candidates.append(("env:VIDEO_ENGINE_PYTHON", Path(environ["VIDEO_ENGINE_PYTHON"])))
    if environ.get("PYTHON"):
        p = Path(environ["PYTHON"])
        candidates.append(("env:PYTHON", p if p.name else Path("python3")))
        if not p.suffix and not p.is_file():
            hit = which(environ["PYTHON"], environ, system)
            if hit is not None and is_file(hit, confine):
                candidates.append(("env:PYTHON", hit))
    if environ.get("VIRTUAL_ENV"):
        venv = Path(environ["VIRTUAL_ENV"])
        for n in ("python.exe", "python"):
            candidates.append(("env:VIRTUAL_ENV", venv / "Scripts" / n if platform_family(system) == "win32" else venv / "bin" / "python"))
            break
        if platform_family(system) == "win32":
            candidates.append(("env:VIRTUAL_ENV", venv / "Scripts" / "python.exe"))
        else:
            candidates.append(("env:VIRTUAL_ENV", venv / "bin" / "python"))
    # The running interpreter (skipped in mocked-OS tests when pillow_ok is provided and path is this one — still valid)
    candidates.append(("sys.executable", Path(sys.executable)))
    for n in ("python3", "python", "python.exe"):
        searched.append(f"PATH:{n}")
        hit = which(n, environ, system)
        if hit is not None and is_file(hit, confine):
            candidates.append(("PATH", hit))

    def _has_pillow(p: Path) -> bool:
        if pillow_ok is not None:
            return pillow_ok(p)
        if require_pillow and p.resolve() == Path(sys.executable).resolve():
            try:
                import PIL  # noqa: F401
                return True
            except ImportError:
                return False
        # Do not spawn unknown interpreters in the default predicate; existence is enough when
        # require_pillow is false, and the callback covers tests.
        return not require_pillow

    seen: set[Path] = set()
    for source, p in candidates:
        searched.append(f"{source}:{p}")
        if not is_file(p, confine) or p in seen:
            continue
        seen.add(p)
        if not require_pillow or _has_pillow(p):
            return _ok("python", p, source, searched, detail="pillow" if require_pillow else "")
    return _miss("python", searched, detail="Pillow required" if require_pillow else "")


# ---------------------------------------------------------------------------
# Asset roots
# ---------------------------------------------------------------------------

def _split_roots(raw: str, system: str | None) -> list[Path]:
    sep = ";" if platform_family(system) == "win32" else ":"
    if system is None:
        sep = os.pathsep
    return [Path(p) for p in raw.split(sep) if p]


def resolve_asset_root(name: str, env_var: str, default_child: str, *, environ: _Env,
                       system: str | None = None, home: Path | None = None,
                        confine: Path | None = None) -> Resolution:
    """One named library root: env override, else `<workspace>/<default_child>`."""
    searched = [f"env:{env_var}"]
    if environ.get(env_var):
        p = Path(environ[env_var])
        src = f"env:{env_var}"
        return Resolution(name, exists(p, confine), p, src if exists(p, confine) else "missing", tuple(searched + [str(p)]),
                          kind="asset")
    ws = default_workspace(environ, system, home)
    p = ws / default_child
    searched.append(f"workspace:{p}")
    return Resolution(name, exists(p, confine), p if exists(p, confine) else p, "workspace" if exists(p, confine) else "missing",
                      tuple(searched), kind="asset")


def asset_search_roots(*, environ: _Env, system: str | None = None, home: Path | None = None,
                       project: Path | None = None,
                        confine: Path | None = None) -> list[Resolution]:
    """Ordered asset search roots: optional project dir, VIDEO_ENGINE_ASSET_ROOTS, then standard libraries."""
    out: list[Resolution] = []
    if project is not None:
        out.append(Resolution("project", exists(project, confine), project, "project", ("project",), kind="asset"))
    if environ.get("VIDEO_ENGINE_ASSET_ROOTS"):
        for i, p in enumerate(_split_roots(environ["VIDEO_ENGINE_ASSET_ROOTS"], system)):
            out.append(Resolution(f"asset_root_{i}", exists(p, confine), p,
                                  "env:VIDEO_ENGINE_ASSET_ROOTS" if exists(p, confine) else "missing",
                                  ("env:VIDEO_ENGINE_ASSET_ROOTS",), kind="asset"))
    # Dedicated libraries (existing variables). Defaults sit under the portable workspace.
    specs = (
        ("geo-library", "VIDEO_ENGINE_GEO_LIBRARY", "geo-library"),
        ("open-assets", "VIDEO_ENGINE_OPEN_ASSETS", "open-assets"),
        ("downloads", "VIDEO_ENGINE_DOWNLOAD_DIR", "downloads"),
    )
    for name, var, child in specs:
        out.append(resolve_asset_root(name, var, child, environ=environ, system=system, home=home, confine=confine))
    # STOCK_DIRS is a list (path-separated). Default: <workspace>/asset-cache
    if environ.get("VIDEO_ENGINE_STOCK_DIRS"):
        for i, p in enumerate(_split_roots(environ["VIDEO_ENGINE_STOCK_DIRS"], system)):
            out.append(Resolution(f"stock_{i}", exists(p, confine), p,
                                  "env:VIDEO_ENGINE_STOCK_DIRS" if exists(p, confine) else "missing",
                                  ("env:VIDEO_ENGINE_STOCK_DIRS",), kind="asset"))
    else:
        out.append(resolve_asset_root("stock", "VIDEO_ENGINE_STOCK_DIRS", "asset-cache",
                                      environ=environ, system=system, home=home, confine=confine))
    return out


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

@dataclass
class Discovery:
    tools: list[Resolution] = field(default_factory=list)
    assets: list[Resolution] = field(default_factory=list)

    def by_name(self) -> dict[str, Resolution]:
        return {r.name: r for r in (*self.tools, *self.assets)}


def resolve_all(*, vendor: Path, environ: _Env | None = None, system: str | None = None,
                home: Path | None = None, npm_root_g: Path | NpmRootFn | None = None,
                project: Path | None = None, engine_root: Path | None = None,
                        confine: Path | None = None) -> Discovery:
    """Resolve every tool and asset root the engine uses. Safe to call from tests with a fake tree."""
    env = environ or os.environ
    vend = vendor if vendor is not None else vendor_dir(env, engine_root or Path("."))
    tools = [
        resolve_node(vendor=vend, environ=env, system=system, home=home, confine=confine),
        resolve_hyperframes(vendor=vend, environ=env, system=system, home=home, npm_root_g=npm_root_g, confine=confine),
        resolve_puppeteer(vendor=vend, environ=env, system=system, home=home, npm_root_g=npm_root_g, confine=confine),
        resolve_three(vendor=vend, environ=env, system=system, home=home, npm_root_g=npm_root_g, confine=confine),
        resolve_chrome(vendor=vend, environ=env, system=system, home=home, confine=confine),
        resolve_ffmpeg(environ=env, system=system, home=home, probe=False, confine=confine),
        resolve_ffmpeg(environ=env, system=system, home=home, probe=True, confine=confine),
        resolve_python(environ=env, system=system, home=home, require_pillow=True,
                       pillow_ok=(lambda p: False) if system is not None and platform_family(system) != platform_family(sys.platform) else None,
                       confine=confine),
        resolve_modal(environ=env, system=system, home=home, confine=confine),
    ]
    assets = asset_search_roots(environ=env, system=system, home=home, project=project, confine=confine)
    return Discovery(tools, assets)
