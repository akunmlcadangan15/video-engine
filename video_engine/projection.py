"""The one lat/lng <-> map transform of a flat map scene: normalised [x, y] (0..1 of the map image) from
gsap_scene_config.map_bounds [south, west, north, east] and map_projection. Pins, props, gates, fx lines and the
geodata region builder all call these functions, so every element lands on the same raster pixel.

equirectangular: x and y linear in lng and lat (plate carree; the engine's original rule).
mercator: Web Mercator (EPSG:3857 on a sphere): y linear in ln(tan(pi/4 + lat/2)), latitudes within
+-MERC_LAT_MAX; x linear in lng as above.
Antimeridian: bounds keep west < east and may run past 180 (e.g. west 160, east 200 for a Pacific map); a longitude
is moved by whole turns to within 180 degrees of the map's centre longitude before it is placed."""
from __future__ import annotations

import math

PROJECTIONS = ("equirectangular", "mercator")
MERC_LAT_MAX = 85.05112878   # Web Mercator latitude limit (square world)


def merc_y(lat: float) -> float:
    if abs(lat) > MERC_LAT_MAX + 1e-9:
        raise ValueError(f"latitude {lat} is past the Web Mercator limit +-{MERC_LAT_MAX}")
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _lat_from_merc(v: float) -> float:
    return math.degrees(2 * math.atan(math.exp(v)) - math.pi / 2)


def wrap_lng(lng: float, west: float, east: float) -> float:
    """lng moved by whole turns to within 180 degrees of the bounds' centre longitude."""
    c = (west + east) / 2
    return c + (lng - c + 180.0) % 360.0 - 180.0


def to_map(lat: float, lng: float, bounds, projection: str = "equirectangular") -> tuple[float, float]:
    """(lat, lng) -> normalised map (x, y); values outside 0..1 lie off the map."""
    s, w, n, e = bounds
    x = (wrap_lng(lng, w, e) - w) / (e - w)
    if projection == "mercator":
        return x, (merc_y(n) - merc_y(lat)) / (merc_y(n) - merc_y(s))
    if projection != "equirectangular":
        raise ValueError(f"unknown map projection {projection!r} (known: {', '.join(PROJECTIONS)})")
    return x, (n - lat) / (n - s)


def to_lat_lng(x: float, y: float, bounds, projection: str = "equirectangular") -> tuple[float, float]:
    """Inverse of to_map: normalised map (x, y) -> (lat, lng); lng may run past 180 with such bounds."""
    s, w, n, e = bounds
    lng = w + x * (e - w)
    if projection == "mercator":
        return _lat_from_merc(merc_y(n) - y * (merc_y(n) - merc_y(s))), lng
    return n - y * (n - s), lng


def of(sc) -> str:
    """Projection of a scene (gsap_scene_config model or dict)."""
    v = sc.get("map_projection") if isinstance(sc, dict) else getattr(sc, "map_projection", None)
    return v or "equirectangular"
