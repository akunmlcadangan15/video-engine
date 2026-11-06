"""3D model sprites: owned glTF / GLB / OBJ models rendered to deterministic RGBA PNG stills or turntable sequences.

The model is rendered with three.js (vendor/npm, pinned) in the vendored headless Chrome on SwiftShader (software
WebGL, no GPU) by hf/model_render.mjs + hf/model_page.html: the model is centred on its bounding box and fitted by its
bounding sphere (radius x MARGIN) into a square canvas of size x SS px, so every rotation fits; camera at `pitch`
degrees above the horizon (orthographic for fov 0, else perspective of `fov` degrees); the model turns about its
vertical axis to `yaw` degrees, frame k of a turntable at yaw + turn x k / frames; a hemisphere light and one key
light. Each canvas frame is reduced SS:1 with Pillow LANCZOS (premultiplied alpha).

Cache: kind "models", key = BLAKE3 of the render parameters, RENDER_VERSION, the content of the model and every file
it references (glTF buffers and images, OBJ material libraries and their textures), the page and driver scripts and
the HyperFrames toolchain identity (node, Chrome, npm tree including three.js). Frames are <key>_<k>.png, the manifest
<key>.json. A sequence is also composed into one sprite sheet <key>_sheet.png (sheet(): row-major cells of size x size,
SHEET_COLS(n) columns), the single image a props layer shows a turntable from.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from . import cache, config, toolchain
from .util import EngineError, blake3_files, blake3_json, run

MODEL_EXT = {".glb", ".gltf", ".obj"}
RENDER_VERSION = 1
SS = 2                 # supersampling factor of the canvas
MARGIN = 1.02          # bounding-sphere radius factor (a little transparent border)
FRAMES_MAX = 240       # longest turntable sequence
PAGE = config.PKG / "hf" / "model_page.html"
DRIVER = config.PKG / "hf" / "model_render.mjs"
THREE = config.VENDOR / "npm" / "node_modules" / "three"
LIGHT = {"ambient": 1.6, "key": 2.2}


def deps(src: Path) -> list[Path]:
    """The model file and every local file it references (sorted, existing only)."""
    return sorted(p for p in refs(src) if p.is_file())


def missing(src: Path) -> list[str]:
    """Files model `src` references that do not exist (sorted)."""
    return sorted(str(p) for p in refs(src) if not p.is_file())


def _safe_ref(base: Path, candidate: Path, src: Path, raw_ref: str) -> Path:
    """Ensure a model's external buffer, material, or texture URI stays inside the model's directory."""
    if not candidate.resolve().is_relative_to(base):
        raise EngineError(f"model '{src}': referenced path '{raw_ref}' escapes model directory")
    return candidate


def refs(src: Path) -> set[Path]:
    """The model file and every local file it references (validated to stay within src.parent)."""
    src = Path(src)
    base = src.resolve().parent
    out = {src}
    ext = src.suffix.lower()
    if ext == ".gltf":
        try:
            g = json.loads(src.read_text())
        except (OSError, ValueError) as e:
            raise EngineError(f"model '{src}': not a readable glTF ({e})") from e
        for item in g.get("buffers", []) + g.get("images", []):
            u = item.get("uri")
            if u and not u.startswith("data:"):
                out.add(_safe_ref(base, src.parent / unquote(u), src, u))
    elif ext == ".obj":
        libs = re.findall(r"^\s*mtllib\s+(.+?)\s*$", src.read_text(errors="replace"), re.M) or [src.stem + ".mtl"]
        for lib in libs:
            m = _safe_ref(base, src.parent / lib, src, lib)
            if m.is_file():
                out.add(m)
                for t in re.findall(r"^\s*(?:map_\w+|bump|disp|norm)\s+(?:-\S+\s+\S+\s+)*(.+?)\s*$",
                                    m.read_text(errors="replace"), re.M):
                    out.add(_safe_ref(base, m.parent / t, src, t))
    return out


def params(yaw: float = 30.0, pitch: float = 20.0, fov: float = 0.0, size: int = 512, frames: int = 1,
           turn: float = 0.0) -> dict:
    if not 1 <= frames <= FRAMES_MAX:
        raise EngineError(f"model frames {frames} outside 1..{FRAMES_MAX}")
    return {"yaw": float(yaw), "pitch": float(pitch), "fov": float(fov), "size": int(size), "frames": int(frames),
            "turn": float(turn), "ss": SS, "margin": MARGIN, **LIGHT}


def key(src: Path, p: dict) -> str:
    src = Path(src)
    if not src.is_file():
        raise EngineError(f"model '{src}' not found")
    files = deps(src)
    return blake3_json({"v": RENDER_VERSION, "p": p, "files": [[str(f.relative_to(src.parent)), h]
                                                              for f, h in zip(files, blake3_files(files))],
                        "scripts": blake3_files([PAGE, DRIVER]), "tool": toolchain.hf_identity()})


def cache_names(src: Path, **kw) -> set[str]:
    """File names under the cache kind `models` of the sprite of model `src` with params(**kw)."""
    p = params(**kw)
    k = key(Path(src).resolve(), p)
    return {f"{k}.json", *(f"{k}_{i:04d}.png" for i in range(p["frames"]))} | ({f"{k}_sheet.png"} if p["frames"] > 1 else set())


def cached(k: str) -> list[Path] | None:
    m = cache.get_json("models", k)
    if not m:
        return None
    out = [cache.get_file("models", f"{k}_{i:04d}", ".png") for i in range(m["frames"])]
    return None if any(f is None for f in out) else out


def render(src: Path, **kw) -> list[Path]:
    """Sprite frames (cache paths) of model `src` with params(**kw); renders on a cache miss."""
    src = Path(src).resolve()
    p = params(**kw)
    if src.suffix.lower() not in MODEL_EXT:
        raise EngineError(f"model '{src}': extension must be one of {sorted(MODEL_EXT)}")
    k = key(src, p)
    if (hit := cached(k)) is not None:
        return hit
    wd = config.workdir("models")
    try:
        stage = wd / "stage"
        stage.mkdir()
        shutil.copyfile(PAGE, stage / "model_page.html")
        (stage / "three").symlink_to(THREE)
        root = Path(os.path.commonpath([f.resolve().parent for f in deps(src)]))
        (stage / "model").symlink_to(root)
        env = config.hf_env()
        env["ENGINE_PUPPETEER"] = str(config.PUPPETEER)
        env["ENGINE_CHROME"] = str(config.CHROME)
        from .hyperframes import serve
        raw = wd / "raw"
        with serve(stage) as url:
            run([config.NODE, DRIVER, url + "/model_page.html", json.dumps({**p, "file": src.relative_to(root).as_posix()}), raw], env=env,
                timeout=120 + 2 * p["frames"])
        out = []
        for i in range(p["frames"]):
            f = raw / f"{i:04d}.png"
            if not f.is_file():
                raise EngineError(f"model '{src}': frame {i} not rendered")
            with Image.open(f) as im:
                im = im.convert("RGBA").resize((p["size"], p["size"]), Image.LANCZOS)
            if i == 0 and im.getchannel("A").getbbox() is None:
                gone = missing(src)
                raise EngineError(f"model '{src}': renders fully transparent" +
                                  (f"; missing referenced files: {', '.join(gone)}" if gone else ""))
            dst = wd / f"{i:04d}.png"
            im.save(dst, format="PNG", optimize=False, compress_level=6)
            out.append(cache.put_file("models", f"{k}_{i:04d}", dst, ".png"))
        cache.put_json("models", k, {"frames": p["frames"], "src": str(src)})
        return out
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def sheet_cols(n: int) -> int:
    return math.ceil(math.sqrt(n))


def sheet(src: Path, **kw) -> tuple[Path, int, int]:
    """(path, frames, columns) of the sprite of model `src` with params(**kw): the single frame for frames = 1, else the
    sprite sheet of the sequence (cached as <key>_sheet.png)."""
    fs = render(src, **kw)
    if len(fs) == 1:
        return fs[0], 1, 1
    k = key(Path(src).resolve(), params(**kw))
    n, cols = len(fs), sheet_cols(len(fs))
    if (hit := cache.get_file("models", f"{k}_sheet", ".png")) is not None:
        return hit, n, cols
    size = params(**kw)["size"]
    rows = math.ceil(n / cols)
    im = Image.new("RGBA", (cols * size, rows * size), (0, 0, 0, 0))
    for i, f in enumerate(fs):
        with Image.open(f) as fr:
            im.paste(fr.convert("RGBA"), ((i % cols) * size, (i // cols) * size))
    wd = config.workdir("models")
    try:
        dst = wd / "sheet.png"
        im.save(dst, format="PNG", optimize=False, compress_level=6)
        return cache.put_file("models", f"{k}_sheet", dst, ".png"), n, cols
    finally:
        shutil.rmtree(wd, ignore_errors=True)
