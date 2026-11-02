"""Natural Earth (or any GeoJSON) features -> engine regions on a flat map, through the scene projection
(video_engine/projection.py) so regions, pins, routes and the camera share one transform.

country_region(): one feature (by ISO 3166-1 alpha-2) -> a Region input: every part of a MultiPolygon and every hole
projected to normalised map points, clipped to the map bounds (Sutherland-Hodgman against 0..1), simplified
(Douglas-Peucker, tolerance in map fractions), parts entirely off the map dropped. The largest outer ring is the
region `polygon`; every other ring (islands, holes) goes to `rings`, drawn with it under the even-odd rule, so an
archipelago gets one fill, one edge and one flag domain. Identity (ISO code, used for the flag) stays apart from the
display `entity` and `label`. Antimeridian: points are placed with projection.wrap_lng (within 180 degrees of the
map's centre); a ring that crosses the meridian opposite that centre is not split."""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

from .projection import MERC_LAT_MAX, merc_y, to_map

DATA_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
            "ne_50m_admin_0_countries.geojson")
OCEAN, LAND, BORDER = (14, 32, 52), (58, 74, 66), (110, 128, 118)
ISO_KEYS = ("ISO_A2_EH", "ISO_A2", "iso_a2")
NAME_KEYS = ("NAME", "NAME_LONG", "ADMIN", "name")
MIN_AREA = 1e-7   # rings smaller than this (map fraction squared) after clipping are dropped


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> tuple:
    return tuple(json.loads(Path(path).read_text())["features"])


def features(path) -> tuple:
    p = Path(path)
    return _load(str(p), p.stat().st_mtime)


def find(path, iso: str) -> dict:
    iso = iso.upper()
    for f in features(path):
        props = f.get("properties") or {}
        if any(str(props.get(k, "")).upper() == iso for k in ISO_KEYS):
            return f
    raise ValueError(f"no feature with ISO alpha-2 {iso} in {path}")


def _polygons(geom: dict) -> list:
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return list(geom["coordinates"])
    raise ValueError(f"geometry {geom['type']} is not a polygon")


def area(ring) -> float:
    """Signed shoelace area."""
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / 2


def _clip(ring: list, lo: float = 0.0, hi: float = 1.0) -> list:
    """Sutherland-Hodgman clip of a closed ring to the square lo..hi."""
    def cut(pts, inside, inter):
        out = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(inter(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(inter(prev, cur))
        return out

    def at_x(v):
        return lambda a, b: [v, a[1] + (b[1] - a[1]) * (v - a[0]) / (b[0] - a[0])]

    def at_y(v):
        return lambda a, b: [a[0] + (b[0] - a[0]) * (v - a[1]) / (b[1] - a[1]), v]

    pts = ring
    for inside, inter in ((lambda p: p[0] >= lo, at_x(lo)), (lambda p: p[0] <= hi, at_x(hi)),
                          (lambda p: p[1] >= lo, at_y(lo)), (lambda p: p[1] <= hi, at_y(hi))):
        if not pts:
            break
        pts = cut(pts, inside, inter)
    return [[min(hi, max(lo, x)), min(hi, max(lo, y))] for x, y in pts]


def simplify(ring: list, tol: float) -> list:
    """Douglas-Peucker on a closed ring (points within `tol` of the kept outline are dropped)."""
    if tol <= 0 or len(ring) <= 4:
        return ring

    def dp(pts):
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        best, k = -1.0, 0
        for i in range(1, len(pts) - 1):
            p = pts[i]
            d = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / L if L else math.dist(p, a)
            if d > best:
                best, k = d, i
        if best <= tol:
            return [a, b]
        return dp(pts[:k + 1])[:-1] + dp(pts[k:])

    far = max(range(len(ring)), key=lambda i: math.dist(ring[0], ring[i]))   # split the ring in two open chains
    out = dp(ring[:far + 1])[:-1] + dp(ring[far:] + ring[:1])[:-1]
    return out if len(out) >= 3 else ring


def _in(x, y, ring) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _rings(r) -> list:
    if isinstance(r, dict):
        return [r["polygon"], *r.get("rings", [])]
    return r.all_rings()


def label_anchor(r) -> list[float]:
    """A label point inside region r (model or dict): the area centroid of its main polygon when that lies inside
    the region (even-odd over every ring), else the interior grid point of the polygon farthest from any edge."""
    rings, main = _rings(r), _rings(r)[0]
    inside = lambda x, y: sum(_in(x, y, g) for g in rings) % 2 == 1
    a = area(main)
    if abs(a) > 1e-12:
        cx = sum((x0 + x1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(main, main[1:] + main[:1])) / (6 * a)
        cy = sum((y0 + y1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(main, main[1:] + main[:1])) / (6 * a)
        if inside(cx, cy):
            return [round(cx, 5), round(cy, 5)]
    xs, ys = [p[0] for p in main], [p[1] for p in main]

    def edge_dist(x, y):
        best = math.inf
        for g in rings:
            for (x0, y0), (x1, y1) in zip(g, g[1:] + g[:1]):
                dx, dy = x1 - x0, y1 - y0
                t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / ((dx * dx + dy * dy) or 1e-18)))
                best = min(best, math.hypot(x - x0 - t * dx, y - y0 - t * dy))
        return best
    n = 24
    cands = [(min(xs) + (max(xs) - min(xs)) * (i + 0.5) / n, min(ys) + (max(ys) - min(ys)) * (j + 0.5) / n)
             for i in range(n) for j in range(n)]
    cands = [c for c in cands if _in(c[0], c[1], main) and inside(*c)] or [(sum(xs) / len(xs), sum(ys) / len(ys))]
    x, y = max(cands, key=lambda c: edge_dist(*c))
    return [round(x, 5), round(y, 5)]


def country_region(path, iso: str, *, bounds, projection: str = "equirectangular", entity: str | None = None,
                   flag: bool = True, tolerance: float = 0.0008) -> dict:
    """Region input (dict for timeline.Region) for one country on a map with `bounds` [south, west, north, east] in
    `projection` ("equirectangular" or "mercator"). Raises ValueError when the ISO code is unknown or nothing of
    the country falls on the map."""
    f = find(path, iso)
    props = f.get("properties") or {}
    lat_max = MERC_LAT_MAX if projection == "mercator" else 90.0
    rings = []
    for poly in _polygons(f["geometry"]):
        for ring in poly:
            pts = [list(to_map(max(-lat_max, min(lat_max, p[1])), p[0], bounds, projection)) for p in ring]
            if pts and pts[0] == pts[-1]:
                pts = pts[:-1]
            pts = simplify(_clip(pts), tolerance)
            if len(pts) >= 3 and abs(area(pts)) > MIN_AREA:
                rings.append([[round(x, 5), round(y, 5)] for x, y in pts])
    if not rings:
        raise ValueError(f"{iso.upper()}: the country lies outside the map bounds {list(bounds)}")
    rings.sort(key=lambda g: -abs(area(g)))
    out = {"entity": entity or next((str(props[k]) for k in NAME_KEYS if props.get(k)), iso.upper()),
           "polygon": rings[0], **({"rings": rings[1:]} if len(rings) > 1 else {})}
    if flag:
        out["flag"] = iso.lower()
    return out


def map_height(width: int, bounds, projection: str = "equirectangular") -> int:
    """Raster height that keeps the projection's true shape at the given width."""
    s, w, n, e = bounds
    if projection == "mercator":
        return round(width * (merc_y(n) - merc_y(s)) / math.radians(e - w))
    return round(width * (n - s) / (e - w))


def base_map(path, out, *, bounds, projection: str = "equirectangular", width: int = 3000,
             source_url: str = DATA_URL) -> Path:
    """Static base map (asset preparation, no animation): land and soft borders of every feature in `path` drawn
    through the same transform as the scene, saved as `out` (JPEG/PNG) with the sidecar `<out>.json` that
    `engine draft` and the scene read: map_bounds, projection, data source and its SHA-256."""
    import hashlib

    from PIL import Image, ImageDraw, ImageFilter
    W, H = int(width), map_height(width, bounds, projection)
    lat_max = MERC_LAT_MAX if projection == "mercator" else 90.0
    px = lambda lng, lat: [v * k for v, k in zip(to_map(max(-lat_max, min(lat_max, lat)), lng, bounds, projection),
                                                  (W, H))]
    img, glow = Image.new("RGB", (W, H), OCEAN), Image.new("L", (W, H), 0)
    d, gd = ImageDraw.Draw(img), ImageDraw.Draw(glow)
    rings = [[px(*p[:2]) for p in ring] for f in features(path) for poly in _polygons(f["geometry"]) for ring in poly]
    for pts in rings:
        xs = [p[0] for p in pts]
        if max(xs) - min(xs) > W * 0.9 and bounds[3] - bounds[1] < 300:
            continue   # a ring wrapped across the far side of the map (antimeridian artefact)
        d.polygon([tuple(p) for p in pts], fill=LAND)
        gd.line([tuple(p) for p in pts + pts[:1]], fill=255, width=max(2, W // 900))
    img = Image.composite(Image.new("RGB", img.size, BORDER), img, glow.filter(ImageFilter.GaussianBlur(W / 1500)))
    out = Path(out)
    img.save(out, quality=92)
    out.with_suffix(".json").write_text(json.dumps({
        "map_bounds": [float(v) for v in bounds], "projection": projection,
        "title": "Natural Earth admin-0 base map (rasterised locally)", "source_url": "https://www.naturalearthdata.com/",
        "data_url": source_url, "data_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "licence": "Public Domain (Natural Earth terms of use)"}, indent=2) + "\n")
    return out
