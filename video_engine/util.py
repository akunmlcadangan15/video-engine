"""Small shared helpers: subprocess, ffprobe, hashing, timing."""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import hypoverum_core_rs as core

from . import config


class EngineError(RuntimeError):
    tried: list[tuple[str, str]]


class _StageMonitor(Protocol):
    def begin(self, name: str) -> None: ...
    def end(self, name: str) -> object: ...


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """'#rrggbb' -> (r, g, b) each 0-255. The one place that parses hex colours."""
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb) -> str:
    """(r, g, b) 0-255 (floats allowed, clamped) -> '#rrggbb'. The one place that formats hex colours."""
    return "#%02x%02x%02x" % tuple(int(round(min(255.0, max(0.0, float(v))))) for v in rgb)


def run(cmd: list, *, timeout: float | None = None, env: dict | None = None, cwd=None, quiet: bool = True) -> str:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout)[-2000:]
        raise EngineError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return p.stdout


@lru_cache(maxsize=256)
def _probe(path: str, mtime: float) -> dict:
    out = run([config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    return json.loads(out)


def probe(path: str | Path) -> dict:
    p = Path(path)
    return _probe(str(p), p.stat().st_mtime)


def video_info(path: str | Path) -> dict:
    d = probe(path)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise EngineError(f"{path}: no video stream")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    dur = float(v.get("duration") or d["format"].get("duration") or 0)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "fps": fps, "duration": dur, "codec": v.get("codec_name"),
            "pix_fmt": v.get("pix_fmt"), "r_frame_rate": v.get("r_frame_rate")}


# Source colour (decode): ffprobe color_space -> swscale in_color_matrix. Untagged sources follow the H.273/ffmpeg
# convention by size: HD and larger are BT.709, SD is BT.601 (swscale alone assumes BT.601 for every untagged file,
# which turned untagged HD stock footage ~18 levels darker in saturated reds).
_MATRIX = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020", "bt2020c": "bt2020",
           "fcc": "fcc", "smpte240m": "smpte240m"}
HDR_TRANSFERS = {"smpte2084": "PQ (HDR10)", "arib-std-b67": "HLG"}


def source_matrix(stream: dict) -> str:
    """swscale in_color_matrix of a YUV video stream (ffprobe stream dict): its tag, else bt709 from 1280x720 up."""
    m = _MATRIX.get(stream.get("color_space") or "")
    if m:
        return m
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    return "bt709" if max(w, h) >= 1280 or min(w, h) >= 720 else "bt601"


def decode_colour(path: str | Path) -> str:
    """scale-filter options naming the YUV matrix and range of the first video stream of `path` (empty for RGB or
    palette sources, where they do not apply). Raises EngineError for an HDR (PQ/HLG) source: the engine is SDR
    BT.709 end to end and does not tone-map."""
    d = probe(path)
    v = next((s for s in d["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise EngineError(f"{path}: no video stream")
    trc = v.get("color_transfer") or ""
    if trc in HDR_TRANSFERS:
        raise EngineError(f"{path}: HDR source ({HDR_TRANSFERS[trc]}); convert it to SDR BT.709 before use "
                          f"(e.g. a tone-mapped export from the editing app)")
    pix = v.get("pix_fmt") or ""
    if not pix.startswith(("yuv", "nv", "p0", "p2", "p4")):
        return ""
    full = v.get("color_range") == "pc" or pix.startswith("yuvj")
    return f"in_color_matrix={source_matrix(v)}:in_range={'full' if full else 'limited'}"


def count_frames(path: str | Path) -> int:
    """Exact decoded video frame count (packet count; cheap, exact for our CFR h264 outputs)."""
    out = run([config.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)])
    return int(out.strip().split(",")[0])


RACY_NS = 2_000_000_000   # a file changed this recently may change again inside one timestamp tick
MEMO_MAX = 4096           # entries of each per-file memo below (oldest dropped first)


def file_identity(path: str | Path) -> tuple | None:
    """(resolved path, size, mtime_ns, ctime_ns, inode, device) of a file: the key of every per-file memo, so a file
    is read again whenever any of them changes. None when the file is missing or changed within RACY_NS, while its
    timestamps cannot yet tell two writes apart; a caller then reads the file and memoises nothing."""
    try:
        p = Path(path).resolve()
        st = p.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if time.time_ns() - max(st.st_mtime_ns, st.st_ctime_ns) < RACY_NS:
        return None
    return str(p), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev


def memo_put(memo: dict, key, value) -> None:
    """Store `value` in a per-file memo bounded to MEMO_MAX entries (the oldest entry goes first)."""
    if len(memo) >= MEMO_MAX:
        memo.pop(next(iter(memo)), None)
    memo[key] = value


_BLAKE3: dict[tuple, str] = {}   # file_identity -> BLAKE3 of the content


def blake3_files(paths: list) -> list[str]:
    """BLAKE3 of each file's content (Rust, in parallel). A file is hashed once per process while its file_identity
    holds, so the cache keys that name the same source several times per build read it once; the values are the
    plain content hashes."""
    if isinstance(paths, (str, bytes)) or hasattr(paths, "__fspath__"):
        return core.blake3_files(paths)          # raises its "expects an iterable of paths" TypeError
    paths = list(paths)
    ids = [file_identity(p) for p in paths]
    out: list = [_BLAKE3.get(i) if i is not None else None for i in ids]
    todo = [k for k, h in enumerate(out) if h is None]
    if todo:
        for k, h in zip(todo, core.blake3_files([paths[k] for k in todo])):
            out[k] = h
            if ids[k] is not None:
                memo_put(_BLAKE3, ids[k], h)
    return out


AST_DIGEST_TAG = "ast-v1"   # bump if the normalisation in `_ast_digest` changes (every code-derived cache key changes)


def _drop_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]


@lru_cache(maxsize=256)
def _source_digest(path: str, mtime_ns: int, size: int) -> str:
    """Digest of one source file. A Python file is hashed as its syntax tree without docstrings, so comments, blank
    lines, formatting and docstrings do not change it while every change of behaviour does. Any other file, and
    Python that does not parse, is hashed byte for byte. `mtime_ns` and `size` only key the memo."""
    data = Path(path).read_bytes()
    if path.endswith(".py"):
        try:
            tree = ast.parse(data)
        except (SyntaxError, ValueError):
            return core.blake3_bytes(data)
        _drop_docstrings(tree)
        return core.blake3_bytes(f"{AST_DIGEST_TAG}:{ast.dump(tree)}".encode())
    return core.blake3_bytes(data)


def source_digest(path: str | Path) -> str:
    p = Path(path)
    st = p.stat()
    return _source_digest(str(p), st.st_mtime_ns, st.st_size)


def code_hash(files) -> str:
    """Digest of the engine source files that shape a cached product (part of its cache key). Python files count by
    behaviour, not by text (see `_source_digest`): editing a comment, a docstring or the formatting of a keyed module
    does not discard cached renders. The tree dump depends on the Python minor version, so caches are not shared
    between Python versions."""
    return blake3_json([source_digest(f) for f in files])


def blake3_json(obj) -> str:
    return core.blake3_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so a reader sees one whole version, never a half-written file: a temp file in the same
    directory, then os.replace. The mode of an existing file is kept (0644 for a new one)."""
    import os
    import tempfile
    p = Path(path)
    fd, name = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".part")   # unique per writer
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, p.stat().st_mode & 0o777 if p.exists() else 0o644)
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def frame_jpeg(path: str | Path, t_s: float, px: int, quality: int = 5) -> bytes | None:
    """One JPEG frame of a video at `t_s` seconds, at most `px` wide (never upscaled); None when ffmpeg cannot
    decode one there. The one frame grab for model thumbnails and sampled frames."""
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{t_s:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                        f"scale='min({px},iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", str(quality), "-"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def image_jpeg(path: str | Path, px: int, quality: int = 90, box: bool = False) -> bytes:
    """A still image file as JPEG bytes, in process (Pillow): `px` wide with an even height (like ffmpeg
    `scale=px:-2`), or with `box` fitted inside a `px` square and never upscaled. The one image downscale for model
    thumbnails and review frames; a frame of a video is `frame_jpeg`."""
    from io import BytesIO

    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None            # project maps are trusted inputs and may exceed Pillow's bomb guard
    with Image.open(path) as im:
        im = im.convert("RGB")
        if box:
            im.thumbnail((px, px))
        else:
            im = im.resize((px, max(2, round(im.height * px / im.width / 2) * 2)), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def wav_16k_mono(path: str | Path, what: str = "audio decode") -> bytes:
    """16 kHz mono WAV bytes of any audio file via ffmpeg (speech-to-text uploads, model audio input).
    EngineError `<what> failed for '<path>': <ffmpeg message>` when it cannot be decoded."""
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "wav", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        tail = (r.stderr or b"").decode(errors="replace").strip()[-600:]
        raise EngineError(f"{what} failed for '{path}': {tail or f'exit {r.returncode}'}")
    return r.stdout


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@dataclass
class Timer:
    """Wall-time per stage; rows end up in timing.tsv. With a resources.Monitor, every stage also gets a
    resource row (wall, CPU, peak RSS by kind) in `resources`."""
    run_label: str = ""
    rows: list = field(default_factory=list)
    monitor: _StageMonitor | None = None
    resources: list = field(default_factory=list)

    @contextmanager
    def stage(self, name: str, note: str = ""):
        t0 = time.perf_counter()
        box = {"note": note}
        print(f"[build] {name} …", file=sys.stderr, flush=True)
        if self.monitor is not None:
            self.monitor.begin(name)
        try:
            yield box
        finally:
            dt = round(time.perf_counter() - t0, 3)
            self.rows.append((self.run_label, name, dt, box["note"]))
            print(f"[build] {name} {dt:.1f}s {str(box['note'])[:160]}", file=sys.stderr, flush=True)
            if self.monitor is not None:
                self.resources.append(self.monitor.end(name))

    def add(self, name: str, seconds: float, note: str = ""):
        self.rows.append((self.run_label, name, round(seconds, 3), note))

    def write_tsv(self, path: Path, append: bool = True):
        new = not path.exists() or not append
        with open(path, "a" if append else "w") as f:
            if new:
                f.write("run\tstage\twall_s\tnote\n")
            for r in self.rows:
                f.write("\t".join(str(x) for x in r) + "\n")
