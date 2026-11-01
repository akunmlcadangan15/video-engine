"""Map rasters, whole-map derivatives, texel density and deep-zoom tile pyramids for HyperFrames map scenes."""
from __future__ import annotations

import functools
import math
import threading
from pathlib import Path

import hypoverum_core_rs as core

from . import cache, camera3d, config
from .util import EngineError, blake3_files, blake3_json

MAP_VERSION = "map-1"   # bump when _map_copy output changes
MAP_JPEG_QUALITY = 90   # default; profile [render] map_jpeg_quality overrides it (scene map_q)
_DECODE = threading.Lock()   # whole-map JPEG decodes (deep tiles, derivatives) run one at a time
MAP_MAX_SIDE = 3000     # widest whole-map derivative; deeper zoom shows deep tiles over it (deep_plan)
DEEP_TILE = 1024        # deep tile side (deep raster px)
DEEP_PAD = 2            # deep raster px each tile reaches into its right and bottom neighbours (no seams)
DEEP_MARGIN = 0.05      # viewport growth (fraction of its size) when picking the tiles a frame needs
DEEP_VERSION = "deep-1" # bump when deep tile output changes
DEEP_MAX_SIDE = 65536   # widest/tallest deep raster (virtual: only the tiles a frame shows are cut and kept)
DEEP_DECODE_MAX_PX = 640_000_000   # source px one full-scale deep decode may hold (rows 0..last needed row, 1.9 GB RGB);
                                   # past it the raster drops to half the source width (JPEG draft decode)
MORPH_PAPER, MORPH_GRAIN = "paper-F1E8D4.jpg", "grain-512.png"   # vendor/textures of a map morph


def map_image_size(path: Path) -> tuple[int, int]:
    from .native import image_size
    return image_size(path)


def _q(scene: dict) -> int:
    """JPEG quality of a scene's map derivative and deep tiles: profile [render] map_jpeg_quality (scene map_q),
    else MAP_JPEG_QUALITY."""
    return scene.get("map_q") or MAP_JPEG_QUALITY


def _map_width(need_w: float) -> int:
    return int(min(MAP_MAX_SIDE, math.ceil(need_w)))


def map_key(src: Path, need_w: float, q: int | None = None) -> str:
    """Key of the map derivative: its full recipe (version, width after the MAP_MAX_SIDE cap, LANCZOS resize and
    JPEG quality, Pillow version) and the source content hash."""
    import PIL
    return blake3_json({"v": MAP_VERSION, "algo": core.ALGO_VERSION, "src": blake3_files([src])[0],
                        "w": _map_width(need_w), "max": MAP_MAX_SIDE, "resample": "lanczos", "q": q or MAP_JPEG_QUALITY,
                        "pil": PIL.__version__})


def _need_w(scene: dict) -> float:
    """Widest on-screen map width: map width x the largest camera scale (x the tilt magnification at the bottom edge
    of the most tilted transform, camera3d.max_mag)."""
    c = scene["camera"]
    st = [c["start"], c["end"]] + [L["to"] for L in c["legs"]]
    W, H = scene["width"], scene["height"]
    return scene["map_size"][0] * max(x["s"] * (camera3d.max_mag(x["t"], W, H) if x.get("t") else 1.0) for x in st)


EFFECTIVE_VERSION = "eff-1"   # bump when the upscale detector (_upscaled_2x, _detail_band) changes
EFFECTIVE_MIN_W = 1600        # narrower rasters are taken at their pixel width


def _detail_band(w: int, h: int) -> tuple[int, int, int, int]:
    """Centre crop (x0, y0, x1, y1), at most 1024 x 256 px, the upscale detector reads."""
    return (max(0, w // 2 - min(512, w // 4)), max(0, h // 2 - min(128, h // 4)),
            min(w, w // 2 + min(512, w // 4)), min(h, h // 2 + min(128, h // 4)))


def _upscaled_2x(grey) -> bool:
    """A greyscale crop of a 2x pre-upscaled raster: mean differences of pixels 1, 2 and 3 apart grow like a ramp up to
    2 px (no detail finer than 2 px) and flatten after it. Crops with d2 <= 2 (flat sea, plain fills) never count."""
    import numpy as np
    d1, d2, d3 = (float(np.mean(np.abs(grey[:, j:] - grey[:, :-j]))) for j in (1, 2, 3))
    return d2 > 2.0 and d1 / d2 < 0.60 and d3 > 0 and d2 / d3 > 0.78


def _measure_effective_width(src: Path) -> int:
    """Pixel width, halved for a 2x pre-upscaled raster. One decode at a time (_DECODE); a JPEG decodes only the rows
    down to the bottom of the detail band."""
    import numpy as np
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with _DECODE, Image.open(src) as im:
        w, h = im.size
        box = _detail_band(w, h)
        if im.format == "JPEG" and len(im.tile) == 1 and box[3] < h:
            _load_top_rows(im, box[3])
        grey = np.asarray(im.crop(box).convert("L"), dtype=np.float32)
    if grey.shape[1] < 8:
        return w
    return w // 2 if _upscaled_2x(grey) else w


def effective_key(src_hash: str) -> str:
    """Cache key (kind "maps", .json) of a raster's effective width: detector version and source content hash."""
    return blake3_json({"v": EFFECTIVE_VERSION, "src": src_hash, "min_w": EFFECTIVE_MIN_W})


def effective_cache_name(src: Path) -> str | None:
    """Cache entry name of src's effective width, None when src is too narrow to be measured (never cached)."""
    if map_image_size(src)[0] < EFFECTIVE_MIN_W:
        return None
    return effective_key(blake3_files([src])[0]) + ".json"


@functools.lru_cache(maxsize=64)
def _effective_width_at(resolved: str, mtime_ns: int, size: int) -> int:
    src = Path(resolved)
    w = map_image_size(src)[0]
    if w < EFFECTIVE_MIN_W:
        return w
    key = effective_key(blake3_files([src])[0])
    if (hit := cache.get_json("maps", key)) is not None:
        return int(hit["w"])
    ew = _measure_effective_width(src)
    cache.put_json("maps", key, {"w": ew, "width": w})
    return ew


def effective_width(path: Path) -> int:
    """True horizontal detail width of a map raster: its pixel width, halved when it was pre-upscaled 2x without the
    matching high-frequency detail. Cached on disk by content hash (a 43200 px map decodes once, not per preflight)
    and in memory by path, mtime and size."""
    p = Path(path).resolve()
    st = p.stat()
    return _effective_width_at(str(p), st.st_mtime_ns, st.st_size)


def _map_copy(src: Path, need_w: float, q: int | None = None) -> Path:
    """Downscaled JPEG of the map (content cache), never larger than needed for the max zoom."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    tw = _map_width(need_w)
    key = map_key(src, need_w, q)
    if hit := cache.get_file("maps", key, ".jpg"):
        return hit
    tmp = cache.acquire(cache.tmp_name(config.CACHE_DIR / "maps" / f"{key}.part.jpg").with_suffix(".jpg"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with _DECODE, Image.open(src) as im:
        im = im if im.mode == "RGB" else im.convert("RGB")
        if im.width > tw:
            im = im.resize((tw, round(im.height * tw / im.width)), Image.LANCZOS)
        im.save(tmp, quality=q or MAP_JPEG_QUALITY)
    out = cache.put_file("maps", key, tmp, ".jpg")
    tmp.unlink(missing_ok=True)
    return out


def deep_plan(scene: dict, src_size: tuple[int, int]) -> dict | None:
    """Deep tiles of a map scene: when the camera zooms past the whole-map derivative's resolution (MAP_MAX_SIDE), a
    deep raster of width min(source width, map width x largest zoom) is cut into DEEP_TILE tiles and only the tiles
    some such frame shows (its viewport grown by DEEP_MARGIN) are kept. None when the derivative suffices."""
    need = _need_w(scene)
    dw = min(src_size[0], math.ceil(need))
    if dw <= _map_width(need):
        return None
    dp = _deep_tiles_at(scene, src_size, dw)
    if dp and dw * 2 > src_size[0] and src_size[0] * _deep_src_rows(dp, src_size) > DEEP_DECODE_MAX_PX:
        dp = _deep_tiles_at(scene, src_size, src_size[0] // 2)   # too many source rows to decode at full scale
    return dp


def drawn_width(scene: dict, src_size: tuple[int, int]) -> int:
    """Width of the map raster a map scene draws at its deepest zoom: the deep raster when deep tiles are cut, else the
    whole-map derivative (both capped by the source). M02 divides it by the on-screen map width."""
    dp = deep_plan(scene, src_size)
    return min(src_size[0], max(dp["w"] if dp else 0, _map_width(_need_w(scene))))


def _deep_src_rows(dp: dict, src_size: tuple[int, int]) -> int:
    """Source rows 0..the last row a deep tile reads (a baseline JPEG decodes top-down: the rows below are skipped)."""
    y1 = max(_deep_box(dp, i, j)[3] for i, j in dp["tiles"])
    return min(src_size[1], math.ceil(y1 * src_size[1] / dp["h"]) + 2)


def _deep_tiles_at(scene: dict, src_size: tuple[int, int], dw: int) -> dict | None:
    """Deep plan at raster width dw: the tiles some frame past the derivative's resolution shows."""
    from . import layout
    mw, mh = scene["map_size"]
    base = _map_width(_need_w(scene)) / mw
    dh = round(src_size[1] * dw / src_size[0])
    if max(dw, dh) > DEEP_MAX_SIDE:
        k = DEEP_MAX_SIDE / max(dw, dh)
        dw, dh = max(1, round(dw * k)), max(1, round(dh * k))
    d = dw / mw                                           # deep px per map px
    W, H, cam = scene["width"], scene["height"], scene["camera"]
    nx, ny = math.ceil(dw / DEEP_TILE), math.ceil(dh / DEEP_TILE)
    tiles = set()
    for f in range(scene["n_frames"]):
        c = layout.camera_at(cam, f)
        mag = camera3d.max_mag(c["t"], W, H) if c.get("t") else 1.0
        if c["s"] * mag <= base + 1e-9:
            continue
        x0, y0, x1, y1 = -c["x"] / c["s"], -c["y"] / c["s"], (W - c["x"]) / c["s"], (H - c["y"]) / c["s"]
        if c.get("t"):   # the flat picture the tilted screen shows: its corners unprojected (past the horizon: map top)
            q = [camera3d.unproject(sx, sy, c["t"], W, H) for sx, sy in ((0, 0), (W, 0), (0, H), (W, H))]
            qs = [p for p in q if p is not None]
            x0 = min(p[0] for p in qs) - c["x"]
            x1 = max(p[0] for p in qs) - c["x"]
            y0 = (min(p[1] for p in qs) - c["y"]) if len(qs) == 4 else -c["y"] - mh * c["s"]
            y1 = max(p[1] for p in qs) - c["y"]
            x0, y0, x1, y1 = x0 / c["s"], y0 / c["s"], x1 / c["s"], y1 / c["s"]
        gx, gy = (x1 - x0) * DEEP_MARGIN, (y1 - y0) * DEEP_MARGIN
        i0, i1 = max(0, math.floor((x0 - gx) * d / DEEP_TILE)), min(nx - 1, math.floor((x1 + gx) * d / DEEP_TILE))
        j0, j1 = max(0, math.floor((y0 - gy) * d / DEEP_TILE)), min(ny - 1, math.floor((y1 + gy) * d / DEEP_TILE))
        tiles |= {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}
    if not tiles:
        return None
    return {"w": dw, "h": dh, "tiles": sorted(tiles)}


def _deep_box(dp: dict, i: int, j: int) -> tuple[int, int, int, int]:
    """Deep raster box (x0, y0, x1, y1) of tile (i, j), DEEP_PAD into its neighbours, clipped to the raster."""
    return (i * DEEP_TILE, j * DEEP_TILE, min(dp["w"], (i + 1) * DEEP_TILE + DEEP_PAD),
            min(dp["h"], (j + 1) * DEEP_TILE + DEEP_PAD))


def deep_key(src_hash: str, dp: dict, i: int, j: int, q: int | None = None) -> str:
    import PIL
    return blake3_json({"v": DEEP_VERSION, "src": src_hash, "w": dp["w"], "h": dp["h"], "box": _deep_box(dp, i, j),
                        "resample": "lanczos", "q": q or MAP_JPEG_QUALITY, "pil": PIL.__version__})


def deep_entries(src: Path, dp: dict, q: int | None = None) -> list[dict]:
    """Scene block of the deep tiles: image name (its content key), raster box (x0, y0, x1, y1) and raster size; the
    template places a tile at box x map size / raster size in map px."""
    h = blake3_files([src])[0]
    return [{"img": f"assets/deep_{deep_key(h, dp, i, j, q)[:32]}.jpg", "box": list(_deep_box(dp, i, j)),
             "raster": [dp["w"], dp["h"]], **({"q": q} if q and q != MAP_JPEG_QUALITY else {})} for i, j in dp["tiles"]]


def _load_top_rows(im, rows: int) -> None:
    """Decode only the top `rows` rows of a baseline JPEG (libjpeg decodes top-down, so the rows below are never read):
    a deep tile set over Asia reads 13k of a 43200 x 21600 map's rows. The decoder reports the unread rest as a broken
    stream, so truncated loading is allowed for this one call (callers hold _DECODE); the rows read are exact."""
    from PIL import ImageFile
    t0 = im.tile[0]
    im._size = (im.width, rows)
    im.tile = [t0._replace(extents=(0, 0, im.width, rows))]
    old = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        im.load()
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


def deep_tiles(src: Path, scene: dict) -> dict[str, Path]:
    """Build-dir name -> cached file of every deep tile of a scene; renders the missing ones from one decode."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    out, todo = {}, []
    for e in scene.get("deep", []):
        name = Path(e["img"]).name
        if hit := cache.get_file("maps", Path(name).stem, ".jpg"):
            out[name] = hit
        else:
            todo.append(e)
    if not todo:
        return out
    with _DECODE, Image.open(src) as im:   # one whole-map decode at a time: a 43200 px map is 2.8 GB of RGB
        dw, dh = todo[0]["raster"]
        full_w, full_h = im.size
        if dw * 2 <= im.width:
            im.draft("RGB", (dw, dh))                      # JPEG: decode at a reduced scale when that is enough
        elif im.format == "JPEG" and len(im.tile) == 1:   # full scale: decode only rows 0..the last row a tile reads
            y1 = min(full_h, math.ceil(max(e["box"][3] for e in todo) * full_h / dh) + 2)
            if y1 < full_h:
                _load_top_rows(im, y1)
        sx, sy = im.width / dw, full_h * im.width / full_w / dh
        im = im if im.mode == "RGB" else im.convert("RGB")
        for e in todo:
            x0, y0, x1, y1 = e["box"]
            t = im.resize((x1 - x0, y1 - y0), Image.LANCZOS, box=(x0 * sx, y0 * sy, x1 * sx, y1 * sy))
            name = Path(e["img"]).name
            tmp = cache.acquire(cache.tmp_name(config.CACHE_DIR / "maps" / f"{Path(name).stem}.part.jpg").with_suffix(".jpg"))
            tmp.parent.mkdir(parents=True, exist_ok=True)
            t.save(tmp, format="JPEG", quality=e.get("q") or MAP_JPEG_QUALITY)
            out[name] = cache.put_file("maps", Path(name).stem, tmp, ".jpg")
            tmp.unlink(missing_ok=True)
    return out


def morph_data(m, scene: dict, project_dir: Path, base_size: tuple[int, int]) -> dict:
    """Scene block of a map morph: its whole-map derivative and deep tiles (the base map's camera), times and tones.
    The morph map must have the base map's aspect ratio (same extent and projection)."""
    src = project_dir / m.asset
    if not src.is_file():
        raise EngineError(f"morph asset {m.asset} not found")
    w, h = map_image_size(src)
    if abs(w / h - base_size[0] / base_size[1]) > 0.005 * base_size[0] / base_size[1]:
        raise EngineError(f"morph asset {m.asset} is {w}x{h}; it must have the aspect ratio of map_asset "
                          f"({base_size[0]}x{base_size[1]}): same extent and projection")
    dp = deep_plan(scene, (w, h))
    return {"img": f"assets/morph_{map_key(src, _need_w(scene), _q(scene))[:32]}.jpg", "asset": m.asset,
            "deep": deep_entries(src, dp, _q(scene)) if dp else [], "at_frame": m.at_frame, "s": m.s,
            "back_at_frame": m.back_at_frame, "sepia": m.sepia, "paper": m.paper, "grain": m.grain,
            "paper_texture": MORPH_PAPER, "grain_texture": MORPH_GRAIN}


def texture_dim(name: str | None) -> float | None:
    """Darkest multiply factor of a vendor/textures file (0.5th percentile of its luminance, 0-1): R64 checks box text
    against the box colour times this factor. None without a texture."""
    if name is None:
        return None
    src = config.TEXTURES_DIR / name
    if not src.is_file():
        raise EngineError(f"texture {name} not found in vendor/textures")
    import numpy as np
    from PIL import Image
    return round(float(np.percentile(np.asarray(Image.open(src).convert("L"), dtype=np.float64), 0.5)) / 255, 3)


def textures(scene: dict) -> list[str]:
    """vendor/textures files of a scene: the paper texture and grain of a [print] paper surface and of a map morph,
    and the year box texture."""
    pr, m, out = scene.get("print"), scene.get("morph"), []
    if pr and pr["surface"] == "paper":
        out += [f for f, a in ((pr["paper_texture"], pr["paper_alpha"]), (pr["grain"], pr["grain_alpha"])) if a > 0]
    if m:
        out += [f for f, a in ((m["paper_texture"], m["paper"]), (m["grain_texture"], m["grain"])) if a > 0 and f not in out]
    out += sorted({f["texture"] for f in scene.get("figures", []) if f.get("texture")} - set(out))
    return out
