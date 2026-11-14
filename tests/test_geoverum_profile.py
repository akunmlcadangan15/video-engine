"""Geoverum profile values: the camera drift counts as motion in P01; the region palette leaves six free muted
colours after the pinned entities; pinned Eurasian/maritime entities take distinct colours near their target reference hues."""
import collections
import itertools
import math

import pytest

from video_engine import config, entities, geodata, profiles
from video_engine.gates import layer2, style

GEO = profiles.load("geoverum")
SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
PINNED_EURASIA = ["Caspian Sea", "Kazakhstan", "Russia", "Turkmenistan", "Iran", "Azerbaijan", "Paratethys", "Black Sea",
           "Mediterranean", "Atlantic"]
# target blocking colours for pinned Eurasian and maritime entities in the Geoverum palette
REFERENCE = {"Caspian Sea": "#2779b4", "Russia": "#bf1a0e", "Kazakhstan": "#984a16", "Turkmenistan": "#dea427",
             "Iran": "#5a9e1b", "Azerbaijan": "#958060", "Black Sea": "#0588ed", "Mediterranean": "#34afdb",
             "Paratethys": "#a4c2d8", "Atlantic": "#2c3e7a"}
OLD_PALETTE = ["#136868", "#B56A95", "#4B4D8B", "#C99D4E", "#6B8B5A", "#5F4025"]
OLD_ENTITIES = {"Indonesia": "#B56A95", "Sekutu": "#4B4D8B", "Belanda": "#C99D4E", "Jepang": "#6B8B5A"}


def _oklab(h):
    lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))]
    r, g, b = lin
    lms = [0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b, 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
           0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b]
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in lms)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _lch(h):
    L, a, b = _oklab(h)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _de(a, b):
    return math.dist(_oklab(a), _oklab(b))


def _hue_diff(a, b):
    d = abs(_lch(a)[2] - _lch(b)[2]) % 360
    return min(d, 360 - d)


def _geo(monkeypatch, **theme):
    data = GEO.data.model_copy(update={"theme": GEO.theme.model_copy(update=theme)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("geoverum", data, "x"))
    return data


def _regions(names, seg=2):
    def mut(d):
        d["profile"] = "geoverum"
        d["segments"][seg]["gsap_scene_config"]["regions"] = [{"entity": n, "polygon": SQ} for n in names]
    return mut


def _drift_wait(d):
    """Segment 2 (60 frames): a glide to the key at frame 30, then a drift-only wait (pulse 0) to the segment end."""
    d["profile"] = "geoverum"
    sc = d["segments"][2]["gsap_scene_config"]
    sc["camera_path"] = [{"at_frame": 0, "focus": [0.4, 0.4], "zoom": 1.2},
                         {"at_frame": 30, "focus": [0.5, 0.5], "zoom": 1.5, "pulse": 0}]


def _drift_frames(tl, p):
    s = tl.segments[2]
    mf = style.motion_frames(tl, p)
    return [mf[f] for f in range(s.start_frame + 31, s.start_frame + 59)]


# good ---------------------------------------------------------------------------------------------------------------
def test_good_geoverum_drift_wait_counts_as_moving_in_P01(project):
    p, tl = project(_drift_wait)
    g = profiles.CameraGate(still_zoom_ln_s=0.05)
    assert GEO.gates.camera is None and GEO.theme.camera_drift_per_s == 0.06 and g.still_zoom_ln_s == 0.05
    zs = [m[0] for m in _drift_frames(tl, p)]
    assert all(z >= g.still_zoom_ln_s for z in zs)                 # every drift frame is above the still threshold
    assert max(zs) == pytest.approx(GEO.theme.camera_drift_per_s, rel=0.02)


def test_good_geoverum_palette_leaves_six_free_distinct_muted_colours():
    th = GEO.theme
    pal = [c.lower() for c in th.palette]
    pinned = {c.lower() for c in th.entities.values()}
    free = [c for c in pal if c not in pinned]
    assert len(set(pal)) == len(pal) and pinned <= set(pal)
    assert len(free) >= 6
    for c in pal:                                                   # muted, vintage-paper range
        L, C, _ = _lch(c)
        assert 0.38 <= L <= 0.84 and C <= 0.125, c
    assert min(_de(a, b) for a, b in itertools.combinations(pal, 2)) >= 0.07
    teal, indigo = pal.index("#136868"), pal.index("#4b4d8b")
    assert abs(teal - indigo) > 1
    assert free[:2] == ["#136868", "#5f4025"]                       # earlier projects keep their teal, umber picks


def test_good_six_unpinned_entities_take_six_distinct_free_colours_E01_clean(project):
    names = ["A", "B", "C", "D", "E", "F"]
    _, tl = project(_regions(names))
    col = entities.colours(tl)
    assert len(set(col.values())) == 6
    assert not set(col.values()) & {c.lower() for c in GEO.theme.entities.values()}
    assert not [f for f in layer2.e01_entity_colours(tl, {}) if f.level != "pass"]


def test_good_pinned_entities_distinct_near_reference_E01_clean(project):
    _, tl = project(_regions(PINNED_EURASIA))
    col = entities.colours(tl)
    assert set(col) == set(PINNED_EURASIA) and len(set(col.values())) == len(PINNED_EURASIA)
    assert not [f for f in layer2.e01_entity_colours(tl, {}) if f.level != "pass"]
    pal = [c.lower() for c in GEO.theme.palette]
    for e in PINNED_EURASIA:
        ref = REFERENCE[e]
        assert col[e] in pal
        assert _hue_diff(col[e], ref) <= 25, e
        # no free palette colour lies closer to the reference than the pinned one
        free = [c for c in pal if c not in col.values()]
        assert all(_de(col[e], ref) <= _de(c, ref) for c in free), e


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_old_drift_wait_is_still_in_P01(project, monkeypatch):
    _geo(monkeypatch, camera_drift_per_s=0.02)
    p, tl = project(_drift_wait)
    zs = [m[0] for m in _drift_frames(tl, p)]
    assert all(z < profiles.CameraGate().still_zoom_ln_s for z in zs)    # the whole drift-only wait is a hold


def test_bad_old_palette_six_unpinned_entities_share_colours_warn_E01(project, monkeypatch):
    _geo(monkeypatch, palette=OLD_PALETTE, entities=OLD_ENTITIES)
    _, tl = project(_regions(["A", "B", "C", "D", "E", "F"]))
    assert len(set(entities.colours(tl).values())) < 6
    assert any(f.level == "warn" and "share colour" in f.message for f in layer2.e01_entity_colours(tl, {}))


def test_bad_pinned_entities_on_old_palette_share_colours_warn_E01(project, monkeypatch):
    _geo(monkeypatch, palette=OLD_PALETTE, entities=OLD_ENTITIES)
    _, tl = project(_regions(PINNED_EURASIA))
    assert any(f.level == "warn" and "share colour" in f.message for f in layer2.e01_entity_colours(tl, {}))


LAND_COLOURS = {"#9c443b", "#a7693c", "#c99d4e", "#6b8b5a", "#a19073", "#b56a95", "#4b4d8b"}
WATER_KEYS = {"Caspian Sea", "Laut Kaspia", "Black Sea", "Laut Hitam", "Mediterranean", "Laut Mediterania",
              "Laut Tengah", "Paratethys", "Atlantic", "Samudra Atlantik"}
COUNTRY_ALIAS_GROUPS = [
    ("Indonesia",), ("Australia",), ("Papua Nugini", "Papua New Guinea"), ("Timor Leste", "Timor-Leste"),
    ("Malaysia",), ("Singapura", "Singapore"), ("Brunei Darussalam", "Brunei"), ("Filipina", "Philippines"),
    ("Belanda", "Netherlands"), ("Jepang", "Japan"), ("Rusia", "Russia", "Russian Federation"),
    ("Amerika Serikat", "United States of America", "United States", "USA", "AS"),
    ("Republik Rakyat Tiongkok", "China", "Tiongkok", "Cina", "RRT"),
    ("Britania Raya", "United Kingdom", "Inggris", "UK", "Great Britain"),
    ("Prancis", "France", "Perancis"), ("Jerman", "Germany"), ("Spanyol", "Spain"), ("Portugal",),
    ("Turki", "Turkey"), ("Uni Emirat Arab", "United Arab Emirates", "UEA", "Emirat Arab"),
    ("Antartika", "Antarktika", "Antarctica"),
]
BORDER_PAIRS = [
    ("Indonesia", "Australia"), ("Indonesia", "Malaysia"), ("Indonesia", "Singapura"), ("Indonesia", "Brunei"),
    ("Indonesia", "Filipina"), ("Indonesia", "Timor Leste"), ("Indonesia", "Papua Nugini"),
    ("Malaysia", "Thailand"), ("Malaysia", "Singapura"), ("Malaysia", "Brunei"), ("Malaysia", "Filipina"),
    ("China", "Taiwan"), ("China", "India"), ("China", "Russia"), ("China", "Jepang"), ("China", "Vietnam"),
    ("Inggris", "Prancis"), ("Inggris", "Irlandia"), ("Inggris", "Belanda"), ("Inggris", "Spanyol"),
    ("Russia", "Ukraina"), ("Russia", "Belarus"), ("Russia", "Finlandia"), ("Russia", "Polandia"),
    ("Israel", "Palestina"), ("Israel", "Mesir"), ("Israel", "Yordania"), ("Israel", "Suriah"), ("Israel", "Lebanon"),
    ("India", "Pakistan"), ("India", "Bangladesh"), ("India", "Sri Lanka"), ("Amerika Serikat", "Kanada"),
    ("Amerika Serikat", "Meksiko"), ("Amerika Serikat", "Kuba"), ("Brasil", "Argentina"),
]


def _seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2


def _feature_segments(idx: int, geom: dict, thresh: float, grid: dict):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    segs = []
    for poly in polys:
        for ring in poly:
            for a, b in zip(ring, ring[1:]):
                bb = (min(a[0], b[0]) - thresh, min(a[1], b[1]) - thresh,
                      max(a[0], b[0]) + thresh, max(a[1], b[1]) + thresh)
                sidx = len(segs)
                segs.append((a[0], a[1], b[0], b[1], *bb))
                for gx in range(math.floor(bb[0] * 4), math.floor(bb[2] * 4) + 1):
                    for gy in range(math.floor(bb[1] * 4), math.floor(bb[3] * 4) + 1):
                        grid[(gx, gy)].append((idx, sidx))
    return segs


def _touch_segments(s1: tuple, s2: tuple, thresh2: float) -> bool:
    ax, ay, bx, by, x0, y0, x1, y1 = s1
    cx, cy, dx, dy, ux0, uy0, ux1, uy1 = s2
    if x1 < ux0 or ux1 < x0 or y1 < uy0 or uy1 < y0:
        return False
    return min(_seg_dist2(ax, ay, cx, cy, dx, dy), _seg_dist2(bx, by, cx, cy, dx, dy),
               _seg_dist2(cx, cy, ax, ay, bx, by), _seg_dist2(dx, dy, ax, ay, bx, by)) <= thresh2


def _land_border_pairs(feats: list, thresh: float = 0.015) -> set[tuple[int, int]]:
    thresh2, grid = thresh * thresh, collections.defaultdict(list)
    all_segs = [_feature_segments(i, f["geometry"], thresh, grid) for i, f in enumerate(feats)]
    pairs: set[tuple[int, int]] = set()
    for items in grid.values():
        if len({c for c, _ in items}) < 2:
            continue
        for i, (c1, s1) in enumerate(items):
            for c2, s2 in items[i + 1:]:
                pair = (min(c1, c2), max(c1, c2))
                if c1 != c2 and pair not in pairs and _touch_segments(all_segs[c1][s1], all_segs[c2][s2], thresh2):
                    pairs.add(pair)
    return pairs


def test_good_country_entities_cover_indonesia_aliases_and_border_constraints():
    ents = {k: v.lower() for k, v in GEO.theme.entities.items()}
    assert ents["Indonesia"] == "#9c443b"
    assert all(v in LAND_COLOURS for k, v in ents.items() if k not in WATER_KEYS)
    for grp in COUNTRY_ALIAS_GROUPS:
        cols = {ents[name] for name in grp}
        assert len(cols) == 1, (grp, cols)
    for a, b in BORDER_PAIRS:
        assert ents[a] != ents[b], f"border conflict between {a} and {b} ({ents[a]})"

    admin = (config.GEO_LIBRARY / "natural-earth" / "natural-earth-vector" / "geojson"
             / "ne_50m_admin_0_countries.geojson")
    if not admin.is_file():
        return
    feats = geodata.features(admin)
    assert len(feats) == 242
    for f in feats:
        pr = f["properties"]
        assert ents[pr["NAME"]] == ents[pr["NAME_ID"]] in LAND_COLOURS
    for c1, c2 in _land_border_pairs(feats):
        n1, n2 = feats[c1]["properties"]["NAME"], feats[c2]["properties"]["NAME"]
        assert ents[n1] != ents[n2], f"land border conflict: {n1} vs {n2} ({ents[n1]})"

