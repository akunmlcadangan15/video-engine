#!/usr/bin/env python3
"""Reproject downloaded map rasters into the five commit-sized map assets of this project, and project the
Natural Earth vectors used by the timeline into each map's normalised [x, y] space (assets/geo.json).

No imagery is painted here: every pixel is resampled from a downloaded raster (NASA Blue Marble NG, Natural
Earth shaded relief) by an orthographic reprojection (a globe view centred on the area of the shot).

Sources (read only, never modified):
  $GEO_LIBRARY (default /workspace/geo-library)
    nasa/blue-marble/world.topo.bathy.200407.3x21600x10800.jpg       (July 2004, 60 px/deg)
  $OPEN_ASSETS (default /workspace/open-assets)
    maps/nasa/blue-marble-ng/500m-tiles/world.topo.bathy.200407.3x21600x21600.C1.jpg (July 2004, 240 px/deg)
  $GEO_LIBRARY
    natural-earth/raster/GRAY_HR_SR_OB_DR/GRAY_HR_SR_OB_DR.tif        (Natural Earth gray shaded relief)
    natural-earth/natural-earth-vector/10m_physical/*.shp             (seas, rivers)
    natural-earth/natural-earth-vector/10m_cultural/ne_10m_admin_0_countries.shp (countries)

Run with a Python that has numpy, opencv-python, Pillow and pyshp:  python3 tools/make_maps.py
"""
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np
import shapefile
from PIL import Image

cv2.setNumThreads(4)
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(2 ** 31))
GEO = Path(os.environ.get("GEO_LIBRARY", "/workspace/geo-library"))
HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "assets"
R = 6371.0
BM_JULY = GEO / "nasa/blue-marble/world.topo.bathy.200407.3x21600x10800.jpg"
OPEN_ASSETS = Path(os.environ.get("OPEN_ASSETS", "/workspace/open-assets"))
BM_JULY_C1 = OPEN_ASSETS / "maps/nasa/blue-marble-ng/500m-tiles/world.topo.bathy.200407.3x21600x21600.C1.jpg"
NE_GRAY = GEO / "natural-earth/raster/GRAY_HR_SR_OB_DR/GRAY_HR_SR_OB_DR.tif"
NEV = GEO / "natural-earth/natural-earth-vector/10m_physical"

BM500 = OPEN_ASSETS / "maps/nasa/blue-marble-ng/500m-tiles"     # July 2004 A1..D2, 90 deg each, 240 px/deg

# name: (source, centre lon, centre lat, width km, output width px[, height km]). Height km defaults to
# width * 16 / 9 (portrait cover fit); output height px = width px * height km / width km.
MAPS = {
    "caspian_globe":      ("bm_july", 50.0, 41.0, 5600.0, 3240),
    "caspian_deep":       ("bm_july_c1", 51.2, 39.6, 1000.0, 2160),
    "caspian_straits":    ("bm_july", 22.0, 41.0, 6000.0, 3240),
    "caspian_relief":     ("ne_gray", 51.0, 42.5, 3400.0, 2160),
    "caspian_paratethys": ("bm_july", 44.0, 44.5, 6000.0, 3240),
    # v3: sized for the deepest camera zoom of their shots at M02 >= 1.0 where the 500 m tiles allow it
    "caspian_eurasia":    ("bm500", 48.0, 43.0, 6000.0, 8640),
    "caspian_westward":   ("bm500", 16.0, 40.0, 8400.0, 10080, 4725.0),
    "caspian_basin":      ("bm500", 51.2, 39.6, 1400.0, 4000),
}


def dims(spec):
    """(width km, height km, width px, height px) of a map spec."""
    wkm, wpx = spec[3], spec[4]
    hkm = spec[5] if len(spec) > 5 else wkm * 16 / 9
    return wkm, hkm, wpx, round(wpx * hkm / wkm)


# Tone grade after reprojection (no pixel is painted; each output pixel is a monotone tone curve of its source pixel):
#   "night": highlight roll-off on linear luminance, Y' = Y / (1 + Y / 0.15), hue and chroma kept, so the [print]
#            light text and entity outlines reach their legibility targets over the bright Central Asian deserts;
#   "paper": lift towards white, v' = 1 - (1 - v) ** 5.0 per sRGB channel, so the gray relief reads as printed paper
#            under dark ink.
GRADE = {"caspian_globe": "night", "caspian_deep": "night", "caspian_straits": "night",
         "caspian_eurasia": "night", "caspian_westward": "night", "caspian_basin": "night",
         "caspian_paratethys": "night", "caspian_relief": "paper"}


def grade(img_bgr, kind):
    v = img_bgr.astype(np.float32) / 255.0
    if kind == "paper":
        return np.clip((1.0 - (1.0 - v) ** 5.0) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    y = lin @ np.array([0.0722, 0.7152, 0.2126], np.float32)
    k = (1.0 / (1.0 + y / 0.15))[..., None]
    lin = lin * k
    v = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - 0.055)
    return np.clip(v * 255.0 + 0.5, 0, 255).astype(np.uint8)


def ortho_inverse(x, y, lon0, lat0):
    """Orthographic inverse: plane km (x east, y north) -> lon, lat degrees (NaN beyond the limb)."""
    p0, l0 = math.radians(lat0), math.radians(lon0)
    rho = np.hypot(x, y)
    c = np.arcsin(np.clip(rho / R, -1, 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.arcsin(np.cos(c) * math.sin(p0) + np.where(rho > 0, y * np.sin(c) * math.cos(p0) / rho, 0))
        lon = l0 + np.arctan2(x * np.sin(c), rho * np.cos(c) * math.cos(p0) - y * np.sin(c) * math.sin(p0))
    bad = rho >= R
    lat[bad] = np.nan
    return (np.degrees(lon) + 180.0) % 360.0 - 180.0, np.degrees(lat)


def ortho_forward(lon, lat, lon0, lat0):
    p0, l0 = math.radians(lat0), math.radians(lon0)
    p, l = math.radians(lat), math.radians(lon)
    x = R * math.cos(p) * math.sin(l - l0)
    y = R * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0))
    return x, y


def load(src):
    if src == "bm_july":
        img = cv2.imread(str(BM_JULY), cv2.IMREAD_COLOR)
        return img, (-180.0, 90.0, 360.0 / img.shape[1])          # lon0, lat0 (top-left), deg/px
    if src == "bm_july_c1":
        img = cv2.imread(str(BM_JULY_C1), cv2.IMREAD_COLOR)
        return img, (0.0, 90.0, 90.0 / img.shape[1])
    if src == "ne_gray":
        img = cv2.imread(str(NE_GRAY), cv2.IMREAD_UNCHANGED)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img, (-180.0, 90.0, 360.0 / img.shape[1])
    raise ValueError(src)


def render_tiles(name, spec, strip=512):
    """Orthographic remap from the 500 m tiles, one tile in memory at a time, the output in row strips."""
    _, lon0, lat0, *_ = spec
    wkm, hkm, wpx, hpx = dims(spec)
    xs = (np.arange(wpx, dtype=np.float64) + 0.5) / wpx * wkm - wkm / 2
    out = np.zeros((hpx, wpx, 3), np.uint8)
    def lonlat(r0, r1):
        ys = hkm / 2 - (np.arange(r0, r1, dtype=np.float64) + 0.5) / hpx * hkm
        X, Y = np.meshgrid(xs, ys)
        lon, lat = ortho_inverse(X, Y, lon0, lat0)
        if np.isnan(lat).any():
            raise SystemExit(f"{name}: frame reaches beyond the globe limb")
        return lon, lat
    need = set()
    for r0 in range(0, hpx, strip):
        lon, lat = lonlat(r0, min(hpx, r0 + strip))
        ti = np.clip(((lon + 180) // 90).astype(int), 0, 3)
        tj = (lat < 0).astype(int)
        need |= set(zip(ti.ravel().tolist(), tj.ravel().tolist()))
    for i, j in sorted(need):
        path = BM500 / f"world.topo.bathy.200407.3x21600x21600.{'ABCD'[i]}{j + 1}.jpg"
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        dpp = 90.0 / img.shape[1]
        glon, glat = -180.0 + 90.0 * i, 90.0 - 90.0 * j
        for r0 in range(0, hpx, strip):
            r1 = min(hpx, r0 + strip)
            lon, lat = lonlat(r0, r1)
            m = (np.clip(((lon + 180) // 90).astype(int), 0, 3) == i) & ((lat < 0).astype(int) == j)
            if not m.any():
                continue
            mx = ((lon - glon) / dpp - 0.5).astype(np.float32)
            my = ((glat - lat) / dpp - 0.5).astype(np.float32)
            part = cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
            out[r0:r1][m] = part[m]
        del img
    out = grade(out, GRADE[name])
    path = OUT / f"{name}.jpg"
    Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).save(path, quality=84, optimize=True, progressive=True)
    return path


def render(name, spec, cache):
    if spec[0] == "bm500":
        return render_tiles(name, spec)
    src, lon0, lat0 = spec[:3]
    wkm, hkm, wpx, hpx = dims(spec)
    if src not in cache:
        cache.clear()
        cache[src] = load(src)
    img, (glon, glat, dpp) = cache[src]
    xs = (np.arange(wpx, dtype=np.float64) + 0.5) / wpx * wkm - wkm / 2
    ys = hkm / 2 - (np.arange(hpx, dtype=np.float64) + 0.5) / hpx * hkm
    X, Y = np.meshgrid(xs, ys)
    lon, lat = ortho_inverse(X, Y, lon0, lat0)
    if np.isnan(lat).any():
        raise SystemExit(f"{name}: frame reaches beyond the globe limb")
    mx = ((lon - glon) / dpp - 0.5).astype(np.float32)
    my = ((glat - lat) / dpp - 0.5).astype(np.float32)
    if mx.min() < -1 or my.min() < -1 or mx.max() > img.shape[1] or my.max() > img.shape[0]:
        raise SystemExit(f"{name}: frame leaves the source raster")
    out = cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
    out = grade(out, GRADE[name])
    path = OUT / f"{name}.jpg"
    Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).save(path, quality=84, optimize=True, progressive=True)
    return path


# ------------------------------------------------------------------ vectors
def shp_parts(path, field, names):
    r = shapefile.Reader(str(path))
    fields = [f[0] for f in r.fields[1:]]
    out = {}
    for sr in r.iterShapeRecords():
        d = dict(zip(fields, sr.record))
        if d.get(field) in names:
            pts = sr.shape.points
            idx = list(sr.shape.parts) + [len(pts)]
            for a, b in zip(idx[:-1], idx[1:]):
                out.setdefault(d[field], []).append(pts[a:b])
    return out


def ring_area(r):
    return 0.5 * abs(sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r))))


def simplify(pts, eps):
    """Douglas-Peucker on a closed ring in normalised map space."""
    if len(pts) < 4:
        return pts
    def dp(a):
        if len(a) < 3:
            return a
        (x1, y1), (x2, y2) = a[0], a[-1]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        d = [abs(dy * (x - x1) - dx * (y - y1)) / n for x, y in a[1:-1]]
        i = max(range(len(d)), key=d.__getitem__)
        if d[i] > eps:
            return dp(a[: i + 2])[:-1] + dp(a[i + 1:])
        return [a[0], a[-1]]
    a = list(pts)
    if a[0] == a[-1]:   # closed ring: split at the point farthest from the start, simplify both halves
        k = max(range(len(a)), key=lambda i: math.hypot(a[i][0] - a[0][0], a[i][1] - a[0][1]))
        return dp(a[: k + 1])[:-1] + dp(a[k:])
    return dp(a)


def to_norm(lonlat, spec):
    lon0, lat0 = spec[1], spec[2]
    wkm, hkm, _, _ = dims(spec)
    x, y = ortho_forward(lonlat[0], lonlat[1], lon0, lat0)
    return (x + wkm / 2) / wkm, (hkm / 2 - y) / hkm


def clip_ring(ring):
    """Sutherland-Hodgman clip of a normalised ring to the unit square."""
    def clip(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            p, q = pts[i - 1], pts[i]
            if inside(q):
                if not inside(p):
                    out.append(inter(p, q))
                out.append(q)
            elif inside(p):
                out.append(inter(p, q))
        return out
    def ix(v, axis):
        def f(p, q):
            t = (v - p[axis]) / ((q[axis] - p[axis]) or 1e-12)
            r = [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]
            r[axis] = v
            return tuple(r)
        return f
    pts = list(ring)
    for axis, v, keep in ((0, 0.0, lambda p: p[0] >= 0), (0, 1.0, lambda p: p[0] <= 1),
                          (1, 0.0, lambda p: p[1] >= 0), (1, 1.0, lambda p: p[1] <= 1)):
        if not pts:
            break
        pts = clip(pts, keep, ix(v, axis))
    return pts


def polygon(parts, spec, eps=0.0012, max_pts=220):
    """Largest part (in map space) of a feature, clipped to the map, simplified, rounded to 4 decimals."""
    best = None
    for ring in parts:
        nr = clip_ring([to_norm(p, spec) for p in ring])
        if len(nr) >= 3 and (best is None or ring_area(nr) > ring_area(best)):
            best = nr
    if best is None:
        return None
    e = eps
    s = simplify(best + [best[0]], e)[:-1]
    while len(s) > max_pts:
        e *= 1.4
        s = simplify(best + [best[0]], e)[:-1]
    return [[round(min(1, max(0, x)), 4), round(min(1, max(0, y)), 4)] for x, y in s]


def line(parts, spec, eps=0.002, lon_range=None, lat_range=None):
    pts = []
    for part in parts:
        for p in part:
            if lon_range and not lon_range[0] <= p[0] <= lon_range[1]:
                continue
            if lat_range and not lat_range[0] <= p[1] <= lat_range[1]:
                continue
            pts.append(p)
    return pts


# Hand-placed points (lon, lat) for pins, markers, notes and sea routes; their sources are standard gazetteer
# positions. The Paratethys outline is a schematic of its Miocene extent (Popov et al. 2004, Lithological-
# Paleogeographic maps of Paratethys), drawn as a region overlay, not as imagery.
POINTS = {
    "caspian_c": (50.8, 41.8), "caspian_n": (49.6, 46.6), "caspian_s": (51.6, 37.4), "caspian_mid": (50.6, 42.6),
    "south_basin": (51.6, 38.9), "kara_bogaz": (53.4, 41.2),
    "aral": (59.8, 45.2), "balkhash": (74.5, 46.5), "van": (42.9, 38.6), "urmia": (45.4, 37.7),
    "persian_gulf": (51.0, 27.2), "black_sea_c": (34.0, 43.2), "arctic_ob": (73.0, 67.0),
    "bosphorus": (29.05, 41.12), "gibraltar": (-5.6, 35.95), "atlantic": (-12.0, 37.0),
    "volga_mouth": (47.9, 46.0), "baku": (49.87, 40.38), "derbent": (48.29, 42.06),
    "ural_mouth": (51.9, 47.0), "ladoga": (31.5, 60.9), "onega": (35.5, 61.7),
    "russia": (45.6, 47.6), "kazakhstan": (57.5, 47.3), "turkmenistan": (57.8, 39.6), "iran": (53.2, 35.6),
    "azerbaijan": (47.6, 40.5), "west_coast": (47.9, 42.6), "med_c": (19.5, 34.3), "black_sea_lbl": (35.6, 42.6),
}
SEA_ROUTE_EAST = [(31.0, 42.6), (29.05, 41.15), (28.0, 40.75), (26.4, 40.25), (25.6, 39.2), (25.3, 37.4),
                  (23.8, 35.9), (21.0, 35.6)]
SEA_ROUTE_WEST = [(21.0, 35.6), (15.5, 36.4), (11.3, 37.4), (8.0, 37.8), (3.0, 37.6), (-1.5, 36.4),
                  (-5.6, 35.95), (-9.5, 36.3)]
PARATETHYS = [(10.5, 47.6), (14.0, 48.6), (18.5, 49.4), (24.0, 48.6), (28.0, 49.4), (33.0, 49.6), (39.5, 50.4),
              (45.0, 51.0), (51.0, 50.6), (56.5, 49.4), (61.5, 47.6), (62.4, 45.0), (60.0, 42.4), (55.5, 39.8),
              (53.2, 36.6), (49.0, 37.0), (46.5, 39.8), (43.0, 41.0), (41.5, 40.8), (38.0, 41.0), (33.0, 41.4),
              (28.5, 41.0), (26.0, 42.4), (22.5, 43.8), (18.5, 44.6), (15.5, 45.4), (12.0, 46.0)]


def main():
    import sys
    OUT.mkdir(exist_ok=True)
    cache = {}
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or list(MAPS)
    for name in [] if "--vectors-only" in sys.argv else names:
        p = render(name, MAPS[name], cache)
        print(f"{p.name}: {p.stat().st_size / 1e6:.2f} MB")
    cache.clear()
    seas = shp_parts(NEV / "ne_10m_geography_marine_polys.shp", "name",
                     {"Caspian Sea", "Black Sea", "Mediterranean Sea", "North Atlantic Ocean", "Aegean Sea"})
    rivers = shp_parts(NEV / "ne_10m_rivers_lake_centerlines.shp", "name", {"Volga", "Ural"})
    countries = shp_parts(NEV.parent / "10m_cultural/ne_10m_admin_0_countries.shp", "ADM0_A3",
                          {"RUS", "KAZ", "TKM", "IRN", "AZE"})
    geo = {}
    for name, spec in MAPS.items():
        g = {"spec": {"source": spec[0], "lon0": spec[1], "lat0": spec[2], "width_km": spec[3], "px": spec[4],
                      "height_km": dims(spec)[1], "px_h": dims(spec)[3]},
             "points": {k: [round(v, 4) for v in to_norm(p, spec)] for k, p in POINTS.items()},
             "regions": {}, "routes": {}}
        for k, parts in seas.items():
            poly = polygon(parts, spec)
            if poly:
                g["regions"][k] = poly
        for iso, parts in countries.items():
            poly = polygon(parts, spec)
            if poly:
                g["regions"][iso] = poly
        g["regions"]["Paratethys"] = polygon([PARATETHYS + [PARATETHYS[0]]], spec, eps=0.0)
        g["routes"]["sea_east"] = [[round(c, 4) for c in to_norm(p, spec)] for p in SEA_ROUTE_EAST]
        g["routes"]["sea_west"] = [[round(c, 4) for c in to_norm(p, spec)] for p in SEA_ROUTE_WEST]
        # lower Volga (Volgograd -> delta) and lower Ural (Oral -> mouth), downstream order
        volga = line(rivers["Volga"], spec, lon_range=(44.0, 49.0), lat_range=(45.7, 48.8))
        volga.sort(key=lambda p: -p[1])
        ural = line(rivers["Ural"], spec, lon_range=(50.5, 52.5), lat_range=(46.9, 51.3))
        ural.sort(key=lambda p: -p[1])
        for key, pts in (("volga", volga), ("ural", ural)):
            n = [to_norm(p, spec) for p in pts]
            step = max(1, len(n) // 10)
            n = n[::step] + [n[-1]]
            g["routes"][key] = [[round(x, 4), round(y, 4)] for x, y in n]
        geo[name] = g
    (OUT / "geo.json").write_text(json.dumps(geo, indent=1))
    print("geo.json written")


if __name__ == "__main__":
    main()
