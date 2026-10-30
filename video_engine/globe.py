"""Blue Marble globe scenes (gsap_scene_config.globe): a three.js sphere textured with the whole-world equirectangular
map_asset, rendered by hf/globe.js in the HyperFrames page; `camera_at`, `pose` and `project` are its Python mirror.

Unit sphere, texture u = (lng + 180) / 360, v = (90 - lat) / 180 (three.js SphereGeometry), so the surface point is
dir(lat, lng) = (cos lat cos lng, sin lat, -cos lat sin lng). A view {lat, lng, alt, tilt} puts the camera at
P + alt (cos tilt n - sin tilt u) (P = dir(lat, lng), n = P, u = local north, east = u x n), looking at P with up u,
vertical field of view FOV. A C1 monotone cubic moves through the keys in lat, lng, ln altitude and tilt; a key hold
stops it, and the profile sets its start and end speed. Per-frame views go to hf/globe.js; after the last key and hold,
altitude drops at the profile drift rate (theme camera_drift_per_s, ln / s), floored at GLOBE_ALT_MIN.

Texture: the map_asset downscaled to TEX_W x TEX_W/2 (cache kind maps). Detail patch: when some frame needs more
texels per degree than that texture has, patches are cut from the detail tiles (NASA 86400 x 43200 set, 240 px / deg)
or else from map_asset: one per density level (`patch_plan`), each covering what its frames show, at most PATCH_MAX px
a side, drawn coarse to fine over the globe as partial spheres of the same radius (no depth test, front faces only).
"""
from __future__ import annotations

import math
from pathlib import Path

from . import cache, config
from .timeline import GLOBE_ALT_MIN, Segment, Timeline
from .util import EngineError, blake3_files, blake3_json

GLOBE_VERSION = "globe-1"
JS = Path(__file__).parent / "hf" / "globe.js"
THREE_FILES = [config.VENDOR / "npm" / "node_modules" / "three" / "build" / f for f in ("three.module.js", "three.core.js")]
FOV = 30.0            # vertical field of view (deg)
TEX_W = 8192          # whole-globe texture width (SwiftShader's largest texture side)
PATCH_MAX = 4096      # detail patch side cap (px)
PATCH_MARGIN = 0.08   # patch box growth (fraction of its size)
TILE_DEG = 90.0
TILE_PX_PER_DEG = 240.0
TILES = {(c, r): f"{c}{r}" for c in "ABCD" for r in (1, 2)}   # column A = -180..-90 lng; row 1 = north
JPEG_Q = 90


def _v(lat, lng):
    a, b = math.radians(lat), math.radians(lng)
    return (math.cos(a) * math.cos(b), math.sin(a), -math.cos(a) * math.sin(b))


def _ll(v):
    x, y, z = v
    return math.degrees(math.asin(max(-1.0, min(1.0, y)))), math.degrees(math.atan2(-z, x))


def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def camera_at(G: dict, f: float, fps: int) -> dict:
    """Globe view {lat, lng, alt, tilt} at frame f of scene globe block G."""
    K = G["keys"]
    def view(k, alt):
        return {"lat": k["lat"], "lng": (k["lng"] + 180) % 360 - 180, "alt": alt, "tilt": k["tilt"]}

    if f <= 0:
        return view(K[0], K[0]["alt"])
    last = K[-1]
    if f >= last["f"]:
        elapsed = max(0.0, f - last["f"] - last.get("hold", 0))
        return view(last, max(G["alt_min"], last["alt"] * math.exp(-G["drift"] * elapsed / fps)))
    for i, (a, b) in enumerate(zip(K, K[1:])):
        if f <= a["f"] + a.get("hold", 0):
            return view(a, a["alt"])
        if f < b["f"]:
            break
    points = []
    for k in K:
        lng = k["lng"] if not points else points[-1][1] + (k["lng"] - points[-1][1] + 180) % 360 - 180
        points.append((k["lat"], lng, math.log(k["alt"]), k["tilt"]))
    lengths = [b["f"] - a["f"] - a.get("hold", 0) for a, b in zip(K, K[1:])]
    slopes = [[(q - p) / h for p, q in zip(x, y)] for x, y, h in zip(points, points[1:], lengths)]

    def tangent(j, c):
        if K[j].get("hold", 0):
            return 0.0
        if j == 0:
            return G["start"] * slopes[0][c]
        if j == len(K) - 1:
            s = slopes[-1][c]
            if G.get("n") is None or last["f"] >= G["n"] - 1:
                return G["end"] * s
            return -G["drift"] / fps if c == 2 and s < 0 else 0.0
        x, y = slopes[j - 1][c], slopes[j][c]
        if x * y <= 0:
            return 0.0
        h0, h1 = lengths[j - 1], lengths[j]
        w1, w2 = 2 * h1 + h0, h1 + 2 * h0
        return (w1 + w2) / (w1 / x + w2 / y)

    h = lengths[i]
    t = (f - a["f"] - a.get("hold", 0)) / h
    h00, h10, h01, h11 = 2*t**3 - 3*t**2 + 1, t**3 - 2*t**2 + t, -2*t**3 + 3*t**2, t**3 - t**2
    p = [h00 * points[i][c] + h10 * h * tangent(i, c) + h01 * points[i + 1][c] + h11 * h * tangent(i + 1, c)
         for c in range(4)]
    return {"lat": p[0], "lng": (p[1] + 180) % 360 - 180, "alt": math.exp(p[2]), "tilt": p[3]}


def pose(v: dict) -> tuple[tuple, tuple, tuple, tuple]:
    """(position, forward, right, up) of the camera of view v."""
    P = _v(v["lat"], v["lng"])
    east = _norm(_cross((0.0, 1.0, 0.0), P))
    north = _cross(P, east)
    t = math.radians(v["tilt"])
    pos = _add(P, _mul(_add(_mul(P, math.cos(t)), _mul(north, -math.sin(t))), v["alt"]))
    fw = _norm(_sub(P, pos))
    r = _norm(_cross(fw, north))
    return pos, fw, r, _cross(r, fw)


def focal(H: int) -> float:
    return H / 2 / math.tan(math.radians(FOV) / 2)


def project(v: dict, lat: float, lng: float, W: int, H: int) -> tuple[float, float] | None:
    """Screen px of surface point (lat, lng) under view v; None when it faces away or is behind the camera."""
    pos, fw, r, up = pose(v)
    X = _v(lat, lng)
    d = _sub(X, pos)
    z = _dot(d, fw)
    if z <= 1e-9 or _dot(X, d) >= 0:
        return None
    F = focal(H)
    return W / 2 + _dot(d, r) / z * F, H / 2 - _dot(d, up) / z * F


def ray_hit(v: dict, sx: float, sy: float, W: int, H: int) -> tuple[float, float] | None:
    """(lat, lng) under screen point (sx, sy), None when the ray misses the globe."""
    pos, fw, r, up = pose(v)
    F = focal(H)
    d = _norm(_add(fw, _add(_mul(r, (sx - W / 2) / F), _mul(up, -(sy - H / 2) / F))))
    b = _dot(pos, d)
    c = _dot(pos, pos) - 1.0
    disc = b * b - c
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    return _ll(_add(pos, _mul(d, t))) if t > 0 else None


def need_px_per_deg(v: dict, H: int) -> float:
    """Screen px per degree of arc at the view centre."""
    return focal(H) / v["alt"] * math.pi / 180


def motion(G: dict, n: int, fps: int, W: int, H: int) -> list[tuple[float, float]]:
    """P01 speeds per frame: (|ln altitude| / s, centre-point travel in frame widths / s)."""
    out = []
    for f in range(n):
        a, b = camera_at(G, f, fps), camera_at(G, f + 1, fps)
        ang = math.acos(max(-1.0, min(1.0, _dot(_v(a["lat"], a["lng"]), _v(b["lat"], b["lng"])))))
        pan = ang * focal(H) / ((a["alt"] + b["alt"]) / 2) * fps / W
        out.append((abs(math.log(b["alt"] / a["alt"])) * fps, max(pan, abs(math.radians(b["tilt"] - a["tilt"])) * fps * H / 2 / W)))
    return out


def key_motion(G: dict, W: int, H: int) -> list[float]:
    """R56 motion of each key-to-key leg in output px: max(centre travel at the lower altitude, zoom at the corner)."""
    out = []
    for a, b in zip(G["keys"], G["keys"][1:]):
        ang = math.acos(max(-1.0, min(1.0, _dot(_v(a["lat"], a["lng"]), _v(b["lat"], b["lng"])))))
        out.append(max(ang * focal(H) / min(a["alt"], b["alt"]), abs(math.log(b["alt"] / a["alt"])) * math.hypot(W, H) / 2,
                       abs(math.radians(b["tilt"] - a["tilt"])) * H / 2))
    return out


# textures --------------------------------------------------------------------------------------------------------------
def tex_key(src: Path) -> str:
    import PIL
    return blake3_json({"v": GLOBE_VERSION, "src": blake3_files([src])[0], "w": TEX_W, "q": JPEG_Q, "pil": PIL.__version__})


def tile_files(d: Path) -> dict[str, Path]:
    """Tile name (A1..D2) -> file of a NASA 8-tile dir; EngineError unless all eight are there."""
    out = {}
    for name in TILES.values():
        hits = sorted(d.glob(f"*.{name}.jpg")) if d.is_dir() else []
        if len(hits) != 1:
            raise EngineError(f"globe detail_tiles '{d}': needs exactly one *.{name}.jpg (found {len(hits)})")
        out[name] = hits[0]
    return out


def base_ppd(src_w: int) -> float:
    """Texels per degree of the whole-globe texture (the map_asset, downscaled to TEX_W wide when larger)."""
    return min(TEX_W, src_w) / 360.0


def _level(need: float, base: float) -> int:
    return max(1, math.ceil(math.log2(need / base) - 1e-9))


def _box(views: list[dict], W: int, H: int) -> tuple[float, float, float, float] | None:
    """(west, east, south, north) around everything the views show (5 x 5 screen samples each, grown by
    PATCH_MARGIN); None when that spans more than 180 deg of longitude."""
    lats, lngs = [], []
    for v in views:
        for sx in (0, W / 4, W / 2, 3 * W / 4, W):
            for sy in (0, H / 4, H / 2, 3 * H / 4, H):
                if (h := ray_hit(v, sx, sy, W, H)) is not None:
                    lats.append(h[0])
                    lngs.append(h[1])
    if not lats or max(lngs) - min(lngs) > 180:
        return None
    gx, gy = (max(lngs) - min(lngs)) * PATCH_MARGIN + 0.02, (max(lats) - min(lats)) * PATCH_MARGIN + 0.02
    return (max(-180.0, min(lngs) - gx), min(180.0, max(lngs) + gx), max(-90.0, min(lats) - gy), min(90.0, max(lats) + gy))


def patch_plan(G: dict, n: int, fps: int, W: int, H: int, src_w: int, tiles: bool) -> list[dict]:
    """Detail patches [{level, west, east, south, north, w, h, src, have}], coarse to fine. Frames needing more than
    the whole-globe texture get level k (need in (base 2^(k-1), base 2^k]); each run of consecutive frames of one
    level gets a patch over everything they show at base 2^k texels / deg (capped by the source), split in halves
    while it would exceed PATCH_MAX px a side and the run's travel (not its single views) makes it that large;
    otherwise the patch keeps its box at a lower density."""
    base = base_ppd(src_w)
    src = TILE_PX_PER_DEG if tiles else src_w / 360.0
    if src <= base:
        return []
    top = _level(src, base)
    runs: list[tuple[int, list[dict]]] = []
    for f in range(n + 1):
        v = camera_at(G, f, fps)
        if (need := need_px_per_deg(v, H)) <= base:
            continue
        k = min(_level(need, base), top)
        if runs and runs[-1][0] == k and runs[-1][1][-1]["f"] == f - 1:
            runs[-1][1].append({**v, "f": f})
        else:
            runs.append((k, [{**v, "f": f}]))
    out = []

    def _side(v: dict) -> float:
        b = _box([v], W, H)
        return 360.0 if b is None else max((b[1] - b[0]) * math.cos(math.radians(b[2] + b[3]) / 2), b[3] - b[2])

    def cut(k: int, views: list[dict]) -> None:
        box = _box(views, W, H)
        if box is None:
            return
        west, east, south, north = box
        cos = math.cos(math.radians(0.0 if south < 0 < north else min(abs(south), abs(north))))   # lng px shrink by cos lat
        want = min(base * 2 ** k, src)
        side = max((east - west) * cos, north - south)
        if side * want > PATCH_MAX and len(views) > 1 and side > 1.5 * max(_side(views[0]), _side(views[-1])):
            cut(k, views[:len(views) // 2])
            cut(k, views[len(views) // 2:])
            return
        have = min(want, PATCH_MAX / side)
        out.append({"level": k, "west": round(west, 4), "east": round(east, 4), "south": round(south, 4),
                    "north": round(north, 4), "w": max(2, round((east - west) * cos * have)),
                    "h": max(2, round((north - south) * have)), "src": "tiles" if tiles else "map", "have": round(have, 3)})
    for k, views in runs:
        cut(k, views)
    return sorted(out, key=lambda p: p["level"])


def have_at(G: dict, v: dict, W: int, H: int) -> float:
    """Texels per degree drawn at the centre of view v: the finest patch over that point, else the texture."""
    lat, lng = v["lat"], v["lng"]
    return max([G["tex_ppd"]] + [p["have"] for p in G.get("patches", [])
                                 if p["west"] <= lng <= p["east"] and p["south"] <= lat <= p["north"]])


def patch_key(pp: dict, files: list[Path]) -> str:
    import PIL
    return blake3_json({"v": GLOBE_VERSION, "box": [pp[k] for k in ("west", "east", "south", "north", "w", "h", "src")],
                        "files": blake3_files(files), "q": JPEG_Q, "pil": PIL.__version__})


def _put(key: str, make) -> Path:
    if hit := cache.get_file("maps", key, ".jpg"):
        return hit
    tmp = cache.acquire(cache.tmp_name(config.CACHE_DIR / "maps" / f"{key}.part.jpg").with_suffix(".jpg"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    make().save(tmp, quality=JPEG_Q)
    out = cache.put_file("maps", key, tmp, ".jpg")
    tmp.unlink(missing_ok=True)
    return out


def texture(src: Path) -> Path:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None

    def make():
        with Image.open(src) as im:
            im.draft("RGB", (TEX_W, TEX_W // 2))
            return im.convert("RGB").resize((TEX_W, TEX_W // 2), Image.LANCZOS)
    return _put(tex_key(src), make)


def _patch_sources(pp: dict, src: Path, tiles_dir: Path | None) -> list[tuple[Path, tuple[float, float, float, float]]]:
    """(file, its lng/lat box west, east, south, north) of every source image the patch box reads."""
    if pp["src"] == "map":
        return [(src, (-180.0, 180.0, -90.0, 90.0))]
    files, out = tile_files(tiles_dir), []
    for (c, r), name in TILES.items():
        w = -180.0 + "ABCD".index(c) * TILE_DEG
        s = 0.0 if r == 1 else -90.0
        box = (w, w + TILE_DEG, s, s + TILE_DEG)
        if box[0] < pp["east"] and box[1] > pp["west"] and box[2] < pp["north"] and box[3] > pp["south"]:
            out.append((files[name], box))
    return out


def patch(pp: dict, src: Path, tiles_dir: Path | None) -> Path:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    srcs = _patch_sources(pp, src, tiles_dir)

    def make():
        out = Image.new("RGB", (pp["w"], pp["h"]))
        kx, ky = pp["w"] / (pp["east"] - pp["west"]), pp["h"] / (pp["north"] - pp["south"])
        for f, (w, e, s, n) in srcs:
            a, b = max(w, pp["west"]), min(e, pp["east"])
            c, d = max(s, pp["south"]), min(n, pp["north"])
            with Image.open(f) as im:
                sx, sy = im.width / (e - w), im.height / (n - s)
                box = ((a - w) * sx, (n - d) * sy, (b - w) * sx, (n - c) * sy)
                dst = (round((a - pp["west"]) * kx), round((pp["north"] - d) * ky),
                       round((b - pp["west"]) * kx), round((pp["north"] - c) * ky))
                if dst[2] > dst[0] and dst[3] > dst[1]:
                    piece = im.convert("RGB").resize((dst[2] - dst[0], dst[3] - dst[1]), Image.LANCZOS, box=box)
                    out.paste(piece, dst[:2])
        return out
    return _put(patch_key(pp, [f for f, _ in srcs]), make)


# scene -----------------------------------------------------------------------------------------------------------------
def scene_block(seg: Segment, tl: Timeline, project_dir: Path, theme: dict) -> dict:
    """Scene globe block: keys, drift, fov, texture and patches with their image names (content keys; files by
    `files`)."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    sc, S = seg.gsap_scene_config, tl.settings
    g = sc.globe
    src = project_dir / sc.map_asset
    with Image.open(src) as im:
        iw, ih = im.size
    if abs(iw / ih - 2.0) > 0.02:
        raise EngineError(f"globe map_asset '{sc.map_asset}' is {iw}x{ih}; a whole-world equirectangular image is 2:1")
    G = {"fov": FOV, "drift": theme["camera_drift_per_s"], "start": theme["globe_start_speed"],
         "end": theme["globe_end_speed"], "n": seg.n_frames, "alt_min": GLOBE_ALT_MIN, "tex_ppd": round(base_ppd(iw), 3),
         "keys": [{"f": k.at_frame, "lat": k.lat, "lng": k.lng, "alt": k.altitude, "tilt": k.tilt, "hold": k.hold} for k in g.keys],
         "tex": f"assets/globe_{tex_key(src)[:32]}.jpg"}
    G["views"] = [[round(v["lat"], 7), round(v["lng"], 7), round(v["alt"], 9), round(v["tilt"], 7)]
                  for f in range(seg.n_frames + 1) for v in [camera_at(G, f, S.fps)]]
    tiles_dir = project_dir / g.detail_tiles if g.detail_tiles else None
    if tiles_dir is not None:
        tile_files(tiles_dir)
    for pp in patch_plan(G, seg.n_frames, S.fps, S.width, S.height, iw, tiles_dir is not None):
        files = [f for f, _ in _patch_sources(pp, src, tiles_dir)]
        G.setdefault("patches", []).append({**pp, "img": f"assets/globe_{patch_key(pp, files)[:32]}.jpg"})
    return G


def files(seg: Segment, project_dir: Path, G: dict) -> dict[str, Path]:
    """Build-dir name -> cached file of the globe texture and patch (rendered when missing)."""
    sc = seg.gsap_scene_config
    src = project_dir / sc.map_asset
    out = {Path(G["tex"]).name: texture(src)}
    tiles = project_dir / sc.globe.detail_tiles if sc.globe.detail_tiles else None
    for pp in G.get("patches", []):
        out[Path(pp["img"]).name] = patch(pp, src, tiles)
    return out


def cache_names(seg: Segment, tl: Timeline, project_dir: Path, G: dict) -> set[str]:
    """Cache file names (maps/) the scene uses: for cache prune."""
    sc = seg.gsap_scene_config
    names = {tex_key(project_dir / sc.map_asset) + ".jpg"}
    tiles = project_dir / sc.globe.detail_tiles if sc.globe.detail_tiles else None
    for pp in G.get("patches", []):
        names.add(patch_key(pp, [f for f, _ in _patch_sources(pp, project_dir / sc.map_asset, tiles)]) + ".jpg")
    return names
