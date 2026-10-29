"""Identity of the HyperFrames render toolchain, for the HyperFrames segment cache key.

`hf_identity()` = BLAKE3 over the content of every toolchain file a segment's pixels depend on:
  pins      vendor/VERSIONS.json
  node      the node binary (config.NODE)
  chrome    every file of the headless Chrome install dir (config.CHROME's directory)
  npm       every file of vendor/npm/node_modules (hyperframes, puppeteer-core and their resolved dependencies)
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


def hf_paths() -> tuple:
    """Toolchain locations the HyperFrames render uses (config paths, read at call time)."""
    return (config.VENDOR / "VERSIONS.json", config.NODE, config.CHROME.parent, config.PUPPETEER.parent,
            config.GSAP_JS, config.TEMPLATE_DIR, config.PKG / "hf" / "dom_probe.mjs",
            tuple(dict.fromkeys([str(config.FFMPEG), config.hf_env()["HYPERFRAMES_FFMPEG_PATH"]])))


def hf_identity() -> str:
    return _identity(hf_paths())


@functools.lru_cache(maxsize=None)
def _identity(paths: tuple) -> str:
    *trees, ffmpegs = paths
    names = ("pins", "node", "chrome", "npm", "gsap", "template", "probe")
    return blake3_json({**{n: tree_hash(p) for n, p in zip(names, trees)},
                        "ffmpeg": [ffmpeg_version(x) for x in ffmpegs]})
