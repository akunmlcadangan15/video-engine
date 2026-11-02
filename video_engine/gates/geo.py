"""G02 geo/fact gate: region polygons are sane and closed, beat pins lie inside their beat's region, a pin label
never names a different region than the one it sits in, and (when the geo library is present) a geographic pin is on
land. The land check needs the Natural Earth `land` GeoJSON of the geo library (config.GEO_LIBRARY, default
<workspace>/geo-library); without it that part skips."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from .. import config
from ..timeline import Timeline
from .core import Finding

WATER = ("sea", "ocean", "lake", "river", "strait", "bay", "gulf", "channel", "mediterranean", "atlantic",
         "paratethys")
EPS = 1e-9


def _ring(poly: list[list[float]]) -> list[tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y in poly]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def _area(poly) -> float:
    return abs(sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))) / 2


def _cross(a, b, c, d) -> bool:
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > EPS) - (v < -EPS)
    return o(a, b, c) != o(a, b, d) and o(c, d, a) != o(c, d, b)


def self_intersects(poly) -> bool:
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j in (i, (i + 1) % n) or (j + 1) % n == i:
                continue
            if _cross(a, b, poly[j], poly[(j + 1) % n]):
                return True
    return False


def point_in_poly(x: float, y: float, poly) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _water(entity: str) -> bool:
    t = entity.lower()
    return any(w in t for w in WATER)


def _pin_xy(p, sc) -> tuple[float, float] | None:
    if sc.globe is not None:
        return None if p.lat is None or p.lng is None else ((p.lng + 180) / 360, (90 - p.lat) / 180)
    if p.x is not None and p.y is not None:
        return p.x, p.y
    if p.lat is not None and p.lng is not None and sc.map_bounds:
        s, w, n, e = sc.map_bounds
        return (p.lng - w) / (e - w), (n - p.lat) / (n - s)
    return None


def _pin_ll(p, sc) -> tuple[float, float] | None:
    if p.lat is not None and p.lng is not None:
        return p.lat, p.lng
    if p.x is not None and p.y is not None and sc.map_bounds:
        s, w, n, e = sc.map_bounds
        return n - p.y * (n - s), w + p.x * (e - w)
    return None


def _region_xy(poly) -> list[tuple[float, float]]:
    return _ring(poly)


@lru_cache(maxsize=4)
def _land(path: str, mtime: float) -> tuple:
    """Exterior rings of a Natural Earth land GeoJSON, with bboxes."""
    data = json.loads(Path(path).read_text())
    rings = []
    for f in data.get("features", []):
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            polys = [coords]
        elif g.get("type") == "MultiPolygon":
            polys = coords
        else:
            continue
        for poly in polys:
            if not poly:
                continue
            ring = [(float(x), float(y)) for x, y in poly[0]]
            if len(ring) >= 3:
                xs, ys = [q[0] for q in ring], [q[1] for q in ring]
                rings.append((ring, (min(xs), min(ys), max(xs), max(ys))))
    return tuple(rings)


def _land_file() -> Path | None:
    root = Path(os.environ.get("VIDEO_ENGINE_GEO_LIBRARY", config.GEO_LIBRARY))
    base = root / "natural-earth" / "natural-earth-vector" / "geojson"
    for name in ("ne_50m_land.geojson", "ne_10m_land.geojson", "ne_110m_land.geojson"):
        if (base / name).is_file():
            return base / name
    return None


def on_land(lat: float, lng: float) -> bool | None:
    """True/False for a lat/lng against the geo library land polygons; None when the library is absent."""
    p = _land_file()
    if p is None:
        return None
    rings = _land(str(p), p.stat().st_mtime)
    return any(b[0] <= lng <= b[2] and b[1] <= lat <= b[3] and point_in_poly(lng, lat, r) for r, b in rings)


def run(tl: Timeline, require_land: bool | None = None) -> list[Finding]:
    """G02 findings for every map scene (polygon sanity, pins in their beat region, labels not naming another region,
    geographic pins on land). `require_land` None = profile [gates.geo] value, default warn-only."""
    from .. import profiles
    g = profiles.of(tl).gates.geo
    need_land = bool(g.require_land) if (g is not None and require_land is None) else bool(require_land)
    out: list[Finding] = []
    for s in tl.segments:
        sc = s.gsap_scene_config
        if sc is None:
            continue
        regions = []
        for i, r in enumerate(sc.regions or []):
            poly = _region_xy(r.polygon)
            if len(set(poly)) < 3 or _area(poly) <= EPS:
                out.append(Finding("G02", "fail", f"region '{r.entity}' polygon has no area: give at least three "
                                                  f"distinct points", s.seg_index))
            elif len(poly) <= 256 and self_intersects(poly):
                out.append(Finding("G02", "fail", f"region '{r.entity}' polygon crosses itself: fix the outline",
                                   s.seg_index))
            regions.append((r, poly))
        if sc.beats:
            for b in sc.beats:
                if b.region is None:
                    continue
                r, poly = regions[b.region]
                for pi in b.pins:
                    pin = sc.pins[pi]
                    xy = _pin_xy(pin, sc)
                    if xy is not None and not point_in_poly(xy[0], xy[1], poly):
                        out.append(Finding("G02", "fail", f"pin '{pin.label}' is outside beat region {b.region} "
                                                          f"'{r.entity}'", s.seg_index))
        for pin in sc.pins:
            xy, ll = _pin_xy(pin, sc), _pin_ll(pin, sc)
            label = pin.label.strip().lower()
            for r, poly in regions:
                if label == r.entity.strip().lower() and xy is not None and not point_in_poly(xy[0], xy[1], poly):
                    out.append(Finding("G02", "fail", f"pin '{pin.label}' names region '{r.entity}' but sits outside "
                                                      f"it", s.seg_index))
            if ll is None or _water(pin.label):
                continue
            land = on_land(*ll)
            if land is False:
                lv = "fail" if need_land else "warn"
                out.append(Finding("G02", lv, f"pin '{pin.label}' at {ll[0]:.3f}, {ll[1]:.3f} is not on land "
                                              f"(Natural Earth land polygons)", s.seg_index))
    return out or [Finding("G02", "pass", "region polygons and geographic pins are consistent")]
