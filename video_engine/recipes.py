"""Story pattern compiler: narrative intent -> a native HyperFrames map scene (gsap_scene_config fields).

Five builders, each deterministic and parameterised by places (lat/lng), countries (ISO alpha-2), values and times:
  country_reveal  orient wide, then fit each country (region + flag + glow edge + label) into the frame
  follow_route    the route draws itself while the camera pulls back so the destination lands inside the frame
  compare_places  two places and two values: one view holding both, figures in the bands away from the geometry,
                  the larger value last and in its entity colour (A/B on one camera; not a split screen)
  explain_flow    several paths revealed in narration order under a legend card, one view holding them all
  stat_emphasis   one value drives the counter (unit, decimals) and its source card, timed together

Layout comes from geometry: the map's bounds, projection and size give the cover-fit container (hyperframes
`_state` is the same transform the renderer uses); `fit` picks the deepest zoom (<= Frame.max_zoom) that keeps a
bbox inside the R63 safe area minus the bands reserved for text, and text bands are chosen on the side away from
the geometry on screen. Nothing is tied to one video: no fixed coordinates or timestamps. Each builder returns
Built(scene, why, needs); `why` records the choices (zoom, band, timing) for the build report and the reviewer.
`compile(recipe, ...)` is the entry point for draft `map_recipe` and for scripts."""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field

from . import geodata, projection
from .hyperframes import _state, motion_px
from .units import Units

FIG_TOP, FIG_BOTTOM = 0.27, 0.72      # figure centres (frame height fractions) in the top / bottom text band
BAND_TOP, BAND_BOTTOM = 0.36, 0.33    # frame height reserved for the top band (card + figure) / the bottom band
PAD_PX = 36                           # breathing room between geometry and the safe area edge (frame px)
CONTEXT = 2.4                         # orientation view: the focus bbox grown by this factor
TEXEL_MIN = 1.2                       # source px per screen px at the deepest zoom (gate M02 headroom)
PIN_UP_PX, PIN_DOWN_PX = 100, 20      # a dot pin's boxed label sits centred above it (frame px)


def label_half_px(labels) -> float:
    """Half the width of the widest pin label box (theme label size; measured ~32 px per character + padding)."""
    return max((16 * len(str(t)) + 14 for t in labels or [] if t), default=0)


@dataclass
class MapInfo:
    path: str                  # project-relative map image
    asset_id: str
    width: int                 # image px
    height: int
    bounds: list               # [south, west, north, east]
    projection: str = "equirectangular"


@dataclass
class Frame:
    W: int = 1080
    H: int = 1920
    fps: int = 30
    n: int = 240               # segment length (frames)
    max_zoom: float = 4.0      # profile [camera] max_zoom


@dataclass
class Built:
    scene: dict                # gsap_scene_config fields (no map_asset / bounds; the caller adds them)
    why: str
    needs: list = field(default_factory=list)


def cover(m: MapInfo, f: Frame) -> tuple[float, float]:
    """Container size (frame px) of the map at camera scale 1: the cover fit of the image."""
    s = max(f.W / m.width, f.H / m.height)
    return m.width * s, m.height * s


def _fr(f: Frame, s: float, end: bool = False) -> int:
    v = round(s * f.fps)
    return max(0, min(f.n if end else f.n - 1, v))


def _pt(m: MapInfo, lat: float, lng: float, what: str) -> list[float]:
    x, y = projection.to_map(float(lat), float(lng), m.bounds, m.projection)
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError(f"{what} ({lat}, {lng}) is outside the map bounds {list(m.bounds)}")
    return [round(x, 5), round(y, 5)]


def _bbox(pts) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _grow(b, k: float, floor: float = 0.0):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    hw, hh = max((b[2] - b[0]) * k, floor) / 2, max((b[3] - b[1]) * k, floor) / 2
    return max(0.0, cx - hw), max(0.0, cy - hh), min(1.0, cx + hw), min(1.0, cy + hh)


def screen_box(m: MapInfo, f: Frame, key: dict, b) -> tuple[float, float, float, float]:
    mw, mh = cover(m, f)
    st = _state((key["focus"][0] * mw, key["focus"][1] * mh), key["zoom"], f.W, f.H, mw, mh)
    return (b[0] * mw * st["s"] + st["x"], b[1] * mh * st["s"] + st["y"],
            b[2] * mw * st["s"] + st["x"], b[3] * mh * st["s"] + st["y"])


def zoom_cap(m: MapInfo, f: Frame) -> float:
    """Deepest zoom the map's source resolution carries (TEXEL_MIN source px per screen px), within Frame.max_zoom."""
    return max(1.0, min(f.max_zoom, m.width / (cover(m, f)[0] * TEXEL_MIN)))


def fit(m: MapInfo, f: Frame, b, *, top: float = 0.0, bottom: float = 0.0, max_zoom: float | None = None,
        min_zoom: float = 1.0, labels=None) -> tuple[dict, bool]:
    """Camera view {focus, zoom} with bbox `b` (normalised map) inside the safe area minus `top` / `bottom` frame
    fractions reserved for text: the deepest zoom that fits (and that the map resolution carries), centred in the free
    area, checked through the renderer's clamped transform. `labels`: pin label texts at points of b; their boxes
    (centred above each pin) must fit too. Returns (view, fits); fits False = best effort at min_zoom."""
    mw, mh = cover(m, f)
    sx, st_, sb = Units.of(f.W, f.H).safe()
    lx = label_half_px(labels)
    up, down = (PIN_UP_PX, PIN_DOWN_PX) if labels else (0, 0)
    ax0, ax1 = sx + PAD_PX + lx, f.W - sx - PAD_PX - lx
    ay0, ay1 = max(st_, top * f.H) + PAD_PX + up, min(sb, f.H - bottom * f.H) - PAD_PX - down
    bw, bh = max((b[2] - b[0]) * mw, 1e-6), max((b[3] - b[1]) * mh, 1e-6)
    zmax = min(max_zoom or f.max_zoom, zoom_cap(m, f))
    z = max(min_zoom, min(zmax, (ax1 - ax0) / bw, (ay1 - ay0) / bh))
    acx, acy = (ax0 + ax1) / 2, (ay0 + ay1) / 2
    bcx, bcy = (b[0] + b[2]) / 2 * mw, (b[1] + b[3]) / 2 * mh
    while True:
        c = (bcx - (acx - f.W / 2) / z, bcy - (acy - f.H / 2) / z)
        view = {"focus": [round(min(1.0, max(0.0, c[0] / mw)), 5), round(min(1.0, max(0.0, c[1] / mh)), 5)],
                "zoom": round(z, 4)}
        x0, y0, x1, y1 = screen_box(m, f, view, b)
        ok = x0 >= ax0 - PAD_PX + 1 and x1 <= ax1 + PAD_PX - 1 and y0 >= ay0 - PAD_PX + 1 and y1 <= ay1 + PAD_PX - 1
        if ok or z <= min_zoom + 1e-6:
            return view, ok
        z = max(min_zoom, z * 0.93)


def _need(ok: bool, what: str, m: MapInfo):
    """Geometry that must stay whole (a destination, every path, a compared place) and cannot fit at zoom 1 is an
    error, not a silent crop."""
    if not ok:
        raise ValueError(f"{what} does not fit the vertical frame at zoom 1 on this map ({m.width}x{m.height}, bounds "
                         f"{list(m.bounds)}): use a map extent closer to the frame's aspect or fewer/closer places")


def _band_away(m: MapInfo, f: Frame, b) -> str:
    """'top' or 'bottom': the text band on the side away from bbox b at its unreserved fitted view."""
    view, _ = fit(m, f, b)
    y0, y1 = screen_box(m, f, view, b)[1::2]
    return "top" if (y0 + y1) / 2 > f.H / 2 else "bottom"


def _side(m, f, view, pt) -> str:
    x = screen_box(m, f, view, (*pt, *pt))[0]
    return "left" if x > f.W * 0.6 else "right"


MIN_MOTION_PX = 110                   # each camera leg moves at least this much on screen (gate R56 needs 100)


def leg_motion(m: MapInfo, f: Frame, a: dict, b: dict) -> float:
    mw, mh = cover(m, f)
    st = lambda v: _state((v["focus"][0] * mw, v["focus"][1] * mh), v["zoom"], f.W, f.H, mw, mh)
    return motion_px(st(a), st(b), f.W, f.H)


def opening(m: MapInfo, f: Frame, view: dict, anchor) -> dict:
    """First camera view before `view`: wider when the map allows, else leaning in on `anchor` (normalised map
    point, the first thing the scene shows) so the opening leg still moves >= MIN_MOTION_PX."""
    wide = {"focus": view["focus"], "zoom": round(max(1.0, view["zoom"] / 1.15), 4)}
    if leg_motion(m, f, wide, view) >= MIN_MOTION_PX:
        return wide
    cap, lean = zoom_cap(m, f), wide
    for k in range(1, 12):
        lean = {"focus": [round(anchor[0], 5), round(anchor[1], 5)], "zoom": round(min(cap, view["zoom"] * (1 + 0.1 * k)), 4)}
        if leg_motion(m, f, lean, view) >= MIN_MOTION_PX:
            break
    return lean


def _key(at: int, view: dict, move: str | None = None) -> dict:
    return {"at_frame": at, **view, **({"move": move} if move and move != "glide" else {})}


def _keys(keys: list[dict], n: int) -> list[dict]:
    """Strictly increasing key frames inside the segment (later duplicates dropped)."""
    out = []
    for k in sorted(keys, key=lambda k: k["at_frame"]):
        if out and k["at_frame"] <= out[-1]["at_frame"]:
            continue
        if k["at_frame"] < n:
            out.append(k)
    return out


def map_extent(latlngs, f: Frame, *, projection_name: str = "equirectangular", margin: float = 0.6,
               aspect: float | None = None) -> list[float]:
    """Map bounds [south, west, north, east] for a vertical scene about these (lat, lng) points: their box grown by
    `margin` of its size on every side (at least 2 degrees), then widened in one direction to the frame aspect
    (default W/H x 1.25: the whole width shows at zoom 1 with a little room to pan), in projected units."""
    from .projection import MERC_LAT_MAX, merc_y
    lats, lngs = [p[0] for p in latlngs], [p[1] for p in latlngs]
    s_, n_, w_, e_ = min(lats), max(lats), min(lngs), max(lngs)
    dy, dx = max(n_ - s_, 2.0), max(e_ - w_, 2.0)
    s_, n_ = s_ - dy * margin - (dy - (n_ - s_)) / 2, n_ + dy * margin + (dy - (n_ - s_)) / 2
    w_, e_ = w_ - dx * margin - (dx - (e_ - w_)) / 2, e_ + dx * margin + (dx - (e_ - w_)) / 2
    lim = MERC_LAT_MAX if projection_name == "mercator" else 90.0
    s_, n_ = max(-lim, s_), min(lim, n_)
    target = aspect or f.W / f.H * 1.25
    yspan = (lambda a, b: merc_y(b) - merc_y(a)) if projection_name == "mercator" else (lambda a, b: math.radians(b - a))
    cur = math.radians(e_ - w_) / yspan(s_, n_)
    if cur < target:                                          # too tall: widen
        add = math.degrees(target * yspan(s_, n_)) - (e_ - w_)
        w_, e_ = w_ - add / 2, e_ + add / 2
    else:                                                     # too wide: grow north and south until the aspect holds
        for _ in range(200):
            if math.radians(e_ - w_) / yspan(s_, n_) <= target or (s_ <= -lim and n_ >= lim):
                break
            s_, n_ = max(-lim, s_ - 0.25), min(lim, n_ + 0.25)
    return [round(s_, 4), round(w_, 4), round(n_, 4), round(e_, 4)]


# ----------------------------------------------------------------------------------------------------------- builders
def country_reveal(m: MapInfo, f: Frame, *, geojson, countries: list, labels: list | None = None, at_s: float = 0.6,
                   flag: bool = True, edge: str = "glow") -> Built:
    if not countries:
        raise ValueError("countries: give at least one ISO alpha-2 code")
    regs = [geodata.country_region(geojson, iso, bounds=m.bounds, projection=m.projection, flag=flag)
            for iso in countries]
    boxes = [_bbox([p for g in [r["polygon"], *r.get("rings", [])] for p in g]) for r in regs]
    allb = _bbox([(b[0], b[1]) for b in boxes] + [(b[2], b[3]) for b in boxes])
    dur = f.n / f.fps
    views = [fit(m, f, b) for b in boxes]
    final, final_ok = fit(m, f, allb) if len(regs) > 1 else views[0]
    wide, _ = fit(m, f, _grow(allb, CONTEXT, 0.25), max_zoom=max(1.0, min(v[0]["zoom"] for v in views) / 1.5))
    step = max(1.2, (dur - at_s - 1.6) / len(regs))
    keys, notes = [_key(0, wide)], []
    for i, (r, (v, ok)) in enumerate(zip(regs, views)):
        t = at_s + i * step
        land = _fr(f, t + min(1.1, step * 0.6))
        keys.append(_key(land, v, "reveal" if i == 0 else None))
        r.update(at_frame=max(0, land - round(0.4 * f.fps)), edge=edge,
                 label=(labels[i] if labels and i < len(labels) else r["entity"]).upper()[:40],
                 label_at=geodata.label_anchor(r), label_at_frame=min(f.n - 1, land + round(0.3 * f.fps)))
        notes.append(f"{r['entity']}: zoom {v['zoom']:.2f} at {land / f.fps:.1f}s{'' if ok else ' (best effort)'}")
    if len(regs) > 1:
        _need(final_ok, "all the countries together", m)
        keys.append(_key(_fr(f, dur - 1.2), final))
        notes.append(f"all: zoom {final['zoom']:.2f}{'' if final_ok else ' (best effort)'}")
    return Built({"camera_path": _keys(keys, f.n), "regions": regs},
                 f"country_reveal: orient at zoom {wide['zoom']:.2f}, then fit each country into the safe area; "
                 + "; ".join(notes), needs=["map with sidecar map_bounds", "Natural Earth admin-0 GeoJSON",
                                            *(["flag-icons"] if flag else [])])


def follow_route(m: MapInfo, f: Frame, *, points: list, labels: tuple | list | None = None, at_s: float = 0.6,
                 draw_s: float | None = None) -> Built:
    if len(points) < 2:
        raise ValueError("points: a route needs at least 2 points [lat, lng]")
    pts = [_pt(m, la, lo, f"points[{i}]") for i, (la, lo) in enumerate(points)]
    dur = f.n / f.fps
    draw = draw_s or max(1.2, min(4.0, (dur - at_s) * 0.5))
    rb = _bbox(pts)
    start_box = _grow((*pts[0], *pts[0]), 1, max(rb[2] - rb[0], rb[3] - rb[1]) / 3)
    v0, _ = fit(m, f, start_box, labels=labels[:1] if labels else None)
    v1, ok = fit(m, f, rb, labels=labels)
    _need(ok, "the whole route", m)
    v0["zoom"] = max(v0["zoom"], v1["zoom"])
    a = _fr(f, at_s)
    end = min(f.n - 1, a + round(draw * f.fps))
    draw = (end - a) / f.fps
    keys = [_key(0, v0), _key(end, v1)]
    pins = []
    if labels:
        pins = [{"label": labels[0], "x": pts[0][0], "y": pts[0][1], "at_frame": max(0, a - round(0.3 * f.fps)),
                 "label_side": _side(m, f, v1, pts[0])},
                {"label": labels[-1], "x": pts[-1][0], "y": pts[-1][1], "at_frame": end,
                 "label_side": _side(m, f, v1, pts[-1])}]
    return Built({"camera_path": keys, "routes": [{"points": pts, "at_frame": a, "draw_s": round(draw, 3)}],
                  "pins": pins},
                 f"follow_route: start at zoom {v0['zoom']:.2f}, pull back to fit the whole route (zoom {v1['zoom']:.2f})"
                 f" as it ends at {end / f.fps:.1f}s{'' if ok else ' (best effort)'}", needs=["map with sidecar map_bounds"])


def _num(v) -> float:
    return float(v)


SPLIT_DIVIDER_PX = 8


def _split_compare(m: MapInfo, f: Frame, a: dict, b: dict, pa, pb, source, at_s: float, gap_s: float) -> Built:
    """Two stacked panels, one camera each on the same map: each panel fits its own place (and its label) at the
    panel size; each figure sits in its panel's band away from its place; both counters run on one frame clock."""
    fp = Frame(f.W, (f.H - SPLIT_DIVIDER_PX) // 2, f.fps, f.n, f.max_zoom)
    small, big = sorted((a, b), key=lambda p: _num(p["value"]))
    t_small, t_big = _fr(f, at_s + 0.4), _fr(f, at_s + 0.4 + gap_s)
    places = ((a, pa), (b, pb))
    bands = [_band_away(m, fp, _grow((*pt, *pt), 1, 0.08)) for _, pt in places]
    card_panel = None
    if source:   # a panel card fits only at the panel top: it goes where that panel's figure sits in the bottom band
        if "bottom" not in bands:
            bands[1] = "bottom"
        card_panel = bands.index("bottom")
    panels, notes = [], []
    for k, (p, pt) in enumerate(places):
        # panel bands (fractions of the panel): a figure centred at 0.25 / 0.75 takes 0.36 / 0.3 of it, the card
        # (panel top) 0.31; the place and its label fit in what is left
        box, band = _grow((*pt, *pt), 1, 0.03), bands[k]
        top = 0.36 if band == "top" else (0.31 if k == card_panel else 0.0)
        view, ok = fit(m, fp, box, top=top, bottom=0.3 if band == "bottom" else 0.0, labels=[p.get("label")],
                       max_zoom=min(fp.max_zoom, 3.0))
        _need(ok, f"{p.get('label')} in its panel", m)
        first = p is small
        t = t_small if first else t_big
        panels.append({"camera_path": [_key(0, opening(m, fp, view, pt)), _key(_fr(f, at_s), view)],
                       "pins": [{"label": p.get("label") or "?", "x": pt[0], "y": pt[1], "at_frame": max(0, t - 9),
                                 "label_side": _side(m, fp, view, pt)}],
                       "figures": [{"kind": "number", "value": _num(p["value"]), "decimals": int(p.get("decimals", 0)),
                                    "prefix": p.get("prefix", ""), "suffix": p.get("unit", ""), "motion": "counter",
                                    "at_frame": t, "position": [0.5, 0.25 if band == "top" else 0.75],
                                    **({} if first else {"entity": p.get("label") or "B"})}],
                       "cards": ([{"title": "Perbandingan", "subtitle": source, "position": "top",
                                   "at_frame": _fr(f, at_s)}] if k == card_panel else [])})
        notes.append(f"{p.get('label')}: zoom {view['zoom']:.2f}, figure {band}")
    second = {"map_asset": m.path, "map_asset_id": m.asset_id, "map_bounds": m.bounds,
              "map_projection": m.projection, **panels[1]}
    return Built({**panels[0], "split": {"second": second, "divider_px": SPLIT_DIVIDER_PX}},
                 f"compare_places: layout split, two cameras on one frame clock ({'; '.join(notes)}); "
                 f"{big.get('label')} ({big['value']}) lands last in its entity colour",
                 needs=["map with sidecar map_bounds", "values with a stated source"])


def compare_places(m: MapInfo, f: Frame, *, a: dict, b: dict, source: str | None = None, at_s: float = 0.5,
                   gap_s: float = 1.2, split: bool = False) -> Built:
    pa, pb = (_pt(m, p["lat"], p["lng"], f"{k} ({p.get('label', '')})") for k, p in (("a", a), ("b", b)))
    if split:
        return _split_compare(m, f, a, b, pa, pb, source, at_s, gap_s)
    both = _bbox([pa, pb])                                  # the pin label boxes give the breathing room
    # layout from geometry: "bands" (figures in the top and bottom bands) when both places fit between them,
    # else "adjacent" (each figure beside its own place, away from the other one)
    names = [a.get("label"), b.get("label")]
    view, ok = fit(m, f, both, top=BAND_TOP, bottom=BAND_BOTTOM, labels=names)
    mode, card_pos = "bands", "top"
    if not ok:
        mode = "adjacent"
        for card_pos, top, bottom in (("top", 0.2, 0.0), ("bottom", 0.0, 0.25), ("top", 0.0, 0.0)):
            view, ok = fit(m, f, both, top=top if source else 0.0, bottom=bottom if source else 0.0, labels=names)
            if ok:
                break
    _need(ok, "both places", m)
    top = 0.2 if source and card_pos == "top" and mode == "adjacent" else 0.0
    bottom_edge = 0.75 if source and card_pos == "bottom" and mode == "adjacent" else 1.0
    upper = a if pa[1] <= pb[1] else b
    small, big = sorted((a, b), key=lambda p: _num(p["value"]))
    wide = opening(m, f, view, pa if small is a else pb)
    t_small, t_big = _fr(f, at_s + 0.4), _fr(f, at_s + 0.4 + gap_s)
    sx, st_, sb = Units.of(f.W, f.H).safe()
    half = Units.of(f.W, f.H).y(0.5 * 150) + 10                     # half a figure (theme figure_px 150) + margin
    figs, pins = [], []
    for p, pt in ((a, pa), (b, pb)):
        first = p is small
        if mode == "bands":
            pos = [0.5, FIG_TOP if p is upper else FIG_BOTTOM]
        else:
            x, y = screen_box(m, f, view, (*pt, *pt))[:2]
            off = 2.2 * half
            up = y - off if p is upper else y + off
            lo, hi = max(st_, top * f.H) + half, min(sb, bottom_edge * f.H) - half
            if not lo <= up <= hi:                          # no room on that side: the other side
                up = y + off if p is upper else y - off
            pos = [round(min(0.7, max(0.3, x / f.W)), 4), round(min(hi, max(lo, up)) / f.H, 4)]
        figs.append({"kind": "number", "value": _num(p["value"]), "decimals": int(p.get("decimals", 0)),
                     "prefix": p.get("prefix", ""), "suffix": p.get("unit", ""), "motion": "counter",
                     "at_frame": t_small if first else t_big, "position": pos,
                     **({} if first else {"entity": p.get("label") or "B"})})
        pins.append({"label": p.get("label") or "?", "x": pt[0], "y": pt[1],
                     "at_frame": max(0, (t_small if first else t_big) - round(0.3 * f.fps)),
                     "label_side": _side(m, f, view, pt)})
    cards = ([{"title": "Perbandingan", "subtitle": source, "position": card_pos, "at_frame": _fr(f, at_s)}]
             if source else [])
    return Built({"camera_path": [_key(0, wide), _key(_fr(f, at_s + 0.4), view)], "pins": pins, "figures": figs,
                  "cards": cards},
                 f"compare_places: layout {mode}, source card {card_pos} (zoom {view['zoom']:.2f}); {upper.get('label')} lies north; "
                 f"{big.get('label')} ({big['value']}) lands last in its entity colour. A/B on one camera, not a split "
                 "screen.", needs=["map with sidecar map_bounds", "values with a stated source"])


FLOW_COLOURS = ["#7fd4ff", "#ffb84a", "#9dff7a", "#ff7ad9", "#c9a7ff", "#ffffff"]


def explain_flow(m: MapInfo, f: Frame, *, paths: list, at_s: list | None = None, labels: list | None = None,
                 legend: str | None = None, legend_note: str | None = None, speed_px: float = 140.0) -> Built:
    """Moving streams (timeline Flow) along each path, revealed in narration order; with `labels` each path gets its
    own colour and a legend row, else one colour under the `legend` card."""
    if not paths:
        raise ValueError("paths: give at least one path")
    pts = [[_pt(m, la, lo, f"paths[{i}][{j}]") for j, (la, lo) in enumerate(p)] for i, p in enumerate(paths)]
    for i, p in enumerate(pts):
        if len(p) < 2:
            raise ValueError(f"paths[{i}]: a path needs at least 2 points")
    if labels is not None and len(labels) != len(pts):
        raise ValueError(f"labels: {len(labels)} labels for {len(pts)} paths")
    dur = f.n / f.fps
    times = at_s or [0.5 + i * max(0.8, (dur - 2.5) / len(pts)) for i in range(len(pts))]
    if len(times) != len(pts):
        raise ValueError(f"at_s: {len(times)} times for {len(pts)} paths")
    allb = _bbox([q for p in pts for q in p])
    view, ok = fit(m, f, _grow(allb, 1.1, 0.05), top=BAND_TOP * 0.6 if legend else 0.0,
                   bottom=0.2 if labels else 0.0)
    _need(ok, "every path", m)
    wide = opening(m, f, view, pts[0][0])
    flows = [{"paths": [p], "at_frame": _fr(f, t), "speed_px": speed_px, "seed": i,
              "colour": FLOW_COLOURS[i % len(FLOW_COLOURS)] if labels else FLOW_COLOURS[0],
              **({"label": str(labels[i])[:32]} if labels else {})} for i, (p, t) in enumerate(zip(pts, times))]
    cards = [{"title": legend, **({"subtitle": legend_note} if legend_note else {}), "position": "top",
              "at_frame": _fr(f, max(0.0, times[0] - 0.3))}] if legend else []
    return Built({"camera_path": [_key(0, wide), _key(_fr(f, times[0] + 0.4), view)], "flows": flows,
                  "cards": cards},
                 f"explain_flow: {len(pts)} moving streams revealed at {', '.join(f'{t:.1f}s' for t in times)} under "
                 f"one view (zoom {view['zoom']:.2f}){'; colour legend at the bottom' if labels else ''}",
                 needs=["map with sidecar map_bounds"])


def stat_emphasis(m: MapInfo, f: Frame, *, value, unit: str = "", decimals: int = 0, at_s: float = 0.8,
                  source: str | None = None, label: str | None = None, place: dict | None = None,
                  count_s: float | None = None) -> Built:
    pins, keys = [], []
    if place:
        pt = _pt(m, place["lat"], place["lng"], f"place ({place.get('label', '')})")
        b = _grow((*pt, *pt), 1, 0.12)
        band = _band_away(m, f, b)
        view, _ = fit(m, f, b, top=BAND_TOP if band == "top" else 0.12, bottom=BAND_BOTTOM if band == "bottom" else 0,
                      max_zoom=min(f.max_zoom, 2.5), labels=[place.get("label")])
        keys = [_key(0, opening(m, f, view, pt)), _key(_fr(f, at_s), view)]
        if place.get("label"):
            pins.append({"label": place["label"], "x": pt[0], "y": pt[1], "at_frame": max(0, _fr(f, at_s) - 9),
                         "label_side": _side(m, f, view, pt)})
    else:
        band = "top"
    t = _fr(f, at_s)
    fig = {"kind": "number", "value": _num(value), "decimals": int(decimals), "suffix": unit, "motion": "counter",
           "at_frame": t, "position": [0.5, FIG_TOP if band == "top" else FIG_BOTTOM],
           **({"count_s": count_s} if count_s else {})}
    cards = [{"title": label or (place or {}).get("label") or "Data", "subtitle": f"Sumber: {source}" if source
              else "Nilai ilustratif", "position": "top", "at_frame": t}]
    return Built({**({"camera_path": keys} if keys else {}), "pins": pins, "figures": [fig], "cards": cards},
                 f"stat_emphasis: one value {value}{unit} drives the counter and its source card from {at_s:.1f}s; "
                 f"figure in the {band} band, away from the place", needs=["a sourced value"])


PATTERNS = {"country_reveal": country_reveal, "follow_route": follow_route, "compare_places": compare_places,
            "explain_flow": explain_flow, "stat_emphasis": stat_emphasis}
GEO_PATTERNS = {"country_reveal"}


def params(pattern: str) -> list[str]:
    return [p for p in inspect.signature(PATTERNS[pattern]).parameters if p not in ("m", "f", "geojson")]


def compile(recipe: dict, m: MapInfo, f: Frame, *, geojson=None) -> Built:
    """{pattern, ...params} -> Built. ValueError names an unknown pattern, an unknown or missing parameter."""
    if not isinstance(recipe, dict) or recipe.get("pattern") not in PATTERNS:
        raise ValueError(f"unknown pattern {recipe.get('pattern') if isinstance(recipe, dict) else recipe!r} "
                         f"(known: {', '.join(PATTERNS)})")
    name, kw = recipe["pattern"], {k: v for k, v in recipe.items() if k != "pattern"}
    allowed = params(name)
    for k in kw:
        if k not in allowed:
            raise ValueError(f"{k}: unknown field for pattern {name} (allowed: {', '.join(allowed)})")
    if name in GEO_PATTERNS:
        if geojson is None:
            raise ValueError(f"pattern {name} needs country geometry: a Natural Earth admin-0 GeoJSON")
        kw["geojson"] = geojson
    try:
        return PATTERNS[name](m, f, **kw)
    except TypeError as e:
        raise ValueError(f"pattern {name}: {e}") from None
