"""Identity of the HyperFrames render toolchain, for the HyperFrames segment cache key.

`hf_identity()` = BLAKE3 over the content of every toolchain file a segment's pixels depend on:
  pins      vendor/VERSIONS.json
  node      the node binary (config.NODE)
  chrome    every file of the browser's own install dir (browser_dir: config.CHROME resolved, in vendor/chrome or
            next to Chromium `*.pak` files); a browser without one (a PATH launcher or wrapper script) is its
            resolved executable plus its `--version` output (browser_exe), never the shared directory it sits in
  npm       every file of vendor/npm/node_modules (hyperframes, puppeteer-core and their resolved dependencies)
  hf        the active HyperFrames (hfversion.active): "<source>:<version>" and every file of its package dir, so
            switching VIDEO_ENGINE_HYPERFRAMES_VERSION (pinned / X.Y.Z / latest / system) never reuses a segment
            rendered by another HyperFrames
  gsap      vendor/js/gsap.min.js
  template  every file of video_engine/template/
  probe     video_engine/hf/dom_probe.mjs (the DOM probe that measures the inks of legibility segments)
  ffmpeg    `-version` output of config.FFMPEG and of the ffmpeg the HyperFrames CLI encodes with
A tree hash is BLAKE3 of the sorted (path relative to the tree root, file content hash) list, so the identity does
not depend on where the checkout lives. A missing file or tree hashes as "missing".

Speed: the identity is computed once per process per set of toolchain paths. Tree content hashes are memoised in
the engine cache (`toolchain/<stat key>.json`), keyed by BLAKE3 of the tree's absolute root and every file's
(relative path, size, mtime_ns, inode, device); any change of a file's stat rehashes the tree.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import cache, config
from .util import blake3_files, blake3_json

MISSING = "missing"


def _files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    out = []
    for d, _, names in os.walk(root):
        out += [Path(d) / n for n in names]
    return sorted(p for p in out if p.is_file())


def tree_hash(root: Path) -> str:
    """Content hash of a file or of every file under a directory (paths relative to `root`)."""
    root = Path(root)
    if not root.exists():
        return MISSING
    files = _files(root)
    rel = [str(p.relative_to(root)) if p != root else p.name for p in files]
    stats = [(r, st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev) for r, st in zip(rel, (p.stat() for p in files))]
    memo = blake3_json({"root": str(root.resolve()), "files": stats})
    if (hit := cache.get_json("toolchain", memo)) is not None:
        return hit
    h = blake3_json(list(zip(rel, blake3_files(files))))
    cache.put_json("toolchain", memo, h)
    return h


@functools.lru_cache(maxsize=8)
def x264_pix_fmts(exe: str) -> frozenset[str]:
    """Pixel formats the libx264 encoder of an ffmpeg binary accepts (empty when it has none)."""
    try:
        out = subprocess.run([exe, "-hide_banner", "-h", "encoder=libx264"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    line = next((x for x in out.splitlines() if "pixel formats" in x.lower()), "")
    return frozenset(line.split(":", 1)[1].split()) if ":" in line else frozenset()


def ffmpeg_version(exe: str) -> str:
    """`-version` output of an ffmpeg binary, memoised per (resolved path, size, mtime_ns)."""
    path = shutil.which(str(exe)) or str(exe)
    try:
        st = os.stat(path)
    except OSError:
        return MISSING
    return _ffmpeg_version(path, st.st_size, st.st_mtime_ns)


@functools.lru_cache(maxsize=16)
def _ffmpeg_version(path: str, size: int, mtime_ns: int) -> str:
    try:
        return subprocess.run([path, "-version"], capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return MISSING


def _shared_dirs() -> set[Path]:
    """Directories that hold many unrelated programs: every PATH entry, resolved."""
    out = set()
    for d in filter(None, os.environ.get("PATH", "").split(os.pathsep)):
        try:
            out.add(Path(d).resolve())
        except (OSError, RuntimeError):
            continue
    return out


def browser_dir(exe: Path) -> Path | None:
    """The browser's own install directory, or None. `exe` is resolved through symlinks. Its directory is the
    browser's own when it lies in the engine's vendor/chrome, or when Chromium resource packs (`*.pak`) sit next to
    the binary, as in chrome-headless-shell and Chrome for Testing archives (user toolchain, Puppeteer's and
    Playwright's browser caches), /opt/google/chrome and distribution Chromium dirs. A `bin`/`sbin` directory or a
    PATH entry never is: what sits there is a launcher or wrapper script next to unrelated programs."""
    try:
        real = Path(exe).resolve()
    except (OSError, RuntimeError):
        return None
    d = real.parent
    if not real.is_file() or d.name in ("bin", "sbin") or d in _shared_dirs():
        return None
    try:
        if d.is_relative_to((config.VENDOR / "chrome").resolve()) or next(d.glob("*.pak"), None) is not None:
            return d
    except (OSError, RuntimeError):
        pass
    return None


def browser(exe: Path) -> Path | tuple[str, str]:
    """The browser part of the HyperFrames identity: its install directory (hashed as a tree), else
    ("exe", the executable as configured), hashed as the resolved file plus its `--version` output (browser_exe)."""
    return browser_dir(exe) or ("exe", str(exe))


def browser_version(exe: str) -> str:
    """`--version` output of a browser executable, memoised per (resolved path, size, mtime_ns). A wrapper script
    answers for the browser it starts. Not run on Windows, where chrome.exe opens a window instead of printing."""
    if sys.platform == "win32":
        return ""
    try:
        st = os.stat(exe)
    except OSError:
        return MISSING
    return _browser_version(str(Path(exe).resolve()), str(exe), st.st_size, st.st_mtime_ns)


@functools.lru_cache(maxsize=16)
def _browser_version(_real: str, exe: str, size: int, mtime_ns: int) -> str:
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return MISSING


def browser_exe(exe: str) -> str:
    """Identity of a browser without an install directory of its own (a PATH entry, a wrapper script, an app
    launcher): content of the resolved executable and its `--version` output; MISSING when it does not exist."""
    real = Path(exe).resolve()
    if not real.is_file():
        return MISSING
    return blake3_json({"exe": tree_hash(real), "version": browser_version(exe)})


def hf_paths() -> tuple:
    """Toolchain locations the HyperFrames render uses (config paths and the active HyperFrames, read at call time)."""
    from . import hfversion
    hf = hfversion.active()
    return (config.VENDOR / "VERSIONS.json", config.NODE, browser(config.CHROME), config.PUPPETEER.parent,
            config.GSAP_JS, config.TEMPLATE_DIR, config.PKG / "hf" / "dom_probe.mjs", hf.root,
            f"{hf.source}:{hf.version}",
            tuple(dict.fromkeys([str(config.FFMPEG), config.hf_env()["HYPERFRAMES_FFMPEG_PATH"]])))


def hf_identity() -> str:
    return _identity(hf_paths())


@functools.lru_cache(maxsize=None)
def _identity(paths: tuple) -> str:
    *trees, hf_tag, ffmpegs = paths
    names = ("pins", "node", "chrome", "npm", "gsap", "template", "probe", "hf_tree")
    return blake3_json({**{n: browser_exe(p[1]) if isinstance(p, tuple) else tree_hash(p) for n, p in zip(names, trees)},
                        "hf": hf_tag, "ffmpeg": [ffmpeg_version(x) for x in ffmpegs]})
