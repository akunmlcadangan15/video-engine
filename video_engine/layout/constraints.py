"""Pure layout predicates shared by planning and gates."""
from __future__ import annotations
import math
from video_engine import layout as lay
from video_engine.layout import Item, LayoutProfile, Plan, corners_box, inflate, inside, overlap_area
from video_engine.core.units import Units
TEXT = ("title", "year", "time", "label", "area", "heading", "note", "caption")
DOM_TOL = 3.0       # px: rendered CSS box vs the plan box
DOM_IDS = {"title": "title", "year": "figure", "time": "figure", "label": "pin", "area": "area", "head": "pinhead",
           "heading": "heading", "note": "note"}


def _k(L: LayoutProfile, W: int, H: int):
    u = Units.of(W, H)
    return u.kx, u.ky


def _z(b, kx, ky):
    return (b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky)


def overlap(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, _ = _k(L, W, H)
    m = L.labels.text_clear * kx
    g = L.zones.group_gap * kx
    texts = [i for i in items if i.kind in TEXT and i.box]
    heads = [i for i in items if i.kind == "head"]
    out = []
    card = ("year", "time")
    for j, a in enumerate(texts):
        ab = a.box
        assert ab is not None
        for b in texts[j + 1:]:
            bb = b.box
            assert bb is not None
            mm = 0.0 if a.kind in card and b.kind in card else m   # one group: the data card sets its own gap (L05)
            if overlap_area(inflate(ab, mm), bb) > 1e-6:
                out.append(f"{a.key} '{a.text}' and {b.key} '{b.text}' closer than {mm:.0f} px")
        for h in heads:
            if a.kind == "label" and a.pin == h.pin:
                continue                      # own head: the label offset rule (L02 candidates) sets that gap
            hb = h.box
            assert hb is not None
            mm = g if a.kind == "area" else m
            if overlap_area(inflate(ab, mm), hb) > 1e-6:
                out.append(f"{a.key} '{a.text}' closer than {mm:.0f} px to {h.key}")
    for j, a in enumerate(heads):
        ab = a.box
        assert ab is not None
        for b in heads[j + 1:]:
            bb = b.box
            assert bb is not None
            if overlap_area(ab, bb) > 1e-6:
                out.append(f"{a.key} and {b.key} overlap")
    for ld in (i for i in items if i.kind == "leader"):
        own = f"label:{ld.pin}" if ld.pin is not None else ld.key.replace("leader:", "")
        seg = ld.seg
        assert seg is not None
        for t in texts:
            tb = t.box
            assert tb is not None
            if t.key != own and lay.seg_box_dist(seg[0], seg[1], inflate(tb, m / 2)) == 0:
                out.append(f"{ld.key} crosses {t.key} '{t.text}'")
    return out


def _grow(cs, d):
    cx = sum(p[0] for p in cs) / 4
    cy = sum(p[1] for p in cs) / 4
    w = math.dist(cs[0], cs[1])
    h = math.dist(cs[1], cs[2])
    ang = math.degrees(math.atan2(cs[1][1] - cs[0][1], cs[1][0] - cs[0][0]))
    return lay.rot_corners(cx, cy, w + 2 * d, h + 2 * d, ang)


def boundary(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, _ = _k(L, W, H)
    regs = {int(i.key.split(":")[1]): i for i in items if i.kind == "region"}
    out = []
    for t in items:
        if t.kind == "label":
            tb = t.box
            assert tb is not None
            b = inflate(tb, L.labels.edge_clear * kx)
            for r, ri in regs.items():
                if lay.crosses_edge(b, ri.poly):
                    out.append(f"{t.key} '{t.text}' + {L.labels.edge_clear:g} px crosses region {r} edge")
        elif t.kind == "area" and t.region in regs:
            cs = _grow(t.poly, L.labels.area_inset * kx)
            poly = regs[t.region].poly
            ok = lay.quad_inside_poly(cs, poly) if t.inside else lay.quad_outside_poly(cs, poly)
            if not ok:
                out.append(f"{t.key} '{t.text}' not {'inside' if t.inside else 'clear of'} region {t.region} "
                           f"with {L.labels.area_inset:g} px inset")
    return out


def budget(items: list[Item], L: LayoutProfile, n_animating: int = 0) -> list[str]:
    B, out = L.budget, []
    n = sum(1 for i in items if i.kind in TEXT)
    if n > B.texts:
        out.append(f"{n} texts on screen (max {B.texts})")
    n = sum(1 for i in items if i.kind == "label")
    if n > B.labelled_pins:
        out.append(f"{n} labelled pins (max {B.labelled_pins})")
    n = sum(1 for i in items if i.kind == "route")
    if n > B.routes:
        out.append(f"{n} routes (max {B.routes})")
    if n_animating > B.animating:
        out.append(f"{n_animating} elements animating (max {B.animating})")
    n = sum(1 for i in items if i.kind in ("year", "time") and i.px >= L.sizes.big_number - 1e-6)
    if n > B.big_numbers:
        out.append(f"{n} numbers of {L.sizes.big_number:g} px or more (max {B.big_numbers})")
    n = sum(1 for i in items if i.kind == "region" and i.level > 0.5)
    if n > B.saturated_regions:
        out.append(f"{n} highlighted regions (max {B.saturated_regions})")
    return out


def hero(items: list[Item], L: LayoutProfile) -> list[str]:
    sizes = sorted((i.px for i in items if i.kind in TEXT and i.kind not in ("caption", "note")), reverse=True)
    if len(sizes) >= 2 and sizes[0] < L.sizes.hero_ratio * sizes[1] - 1e-6:
        return [f"largest text {sizes[0]:g} px < {L.sizes.hero_ratio:g} x next {sizes[1]:g} px"]
    return []


def grid(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, ky = _k(L, W, H)
    G = L.grid
    edges = [e * kx for e in G.edges()]
    u = G.unit * ky
    out = []
    for i in items:
        if i.kind in ("title", "year") and i.slot == "z1":     # centre slots ([print] year alone) sit off the grid
            ink, css = i.ink, i.css
            assert ink is not None and css is not None
            if min(abs(ink[0] - e) for e in edges) > G.snap_tol:
                out.append(f"{i.key} '{i.text}' ink x {ink[0]:.1f} off the column edges (tol {G.snap_tol:g})")
            top = css[1]
            if abs(top - round(top / u) * u) > G.unit_tol:
                out.append(f"{i.key} '{i.text}' top y {top:.1f} not a multiple of {G.unit}")
    years = [i for i in items if i.kind == "year" and i.slot == "z1"]
    for t in (i for i in items if i.kind == "time" and i.slot == "z1"):
        if not years:
            continue                          # the year has already left (exit order)
        y = years[0]
        tb, yb, ti, yi = t.base, y.base, t.ink, y.ink
        assert tb is not None and yb is not None and ti is not None and yi is not None
        if abs(tb - yb) > G.unit_tol:
            out.append(f"{t.key} baseline {tb:.1f} != year baseline {yb:.1f}")
        gap = ti[0] - yi[2]
        if abs(gap - L.labels.card_gap * kx) > G.snap_tol:
            out.append(f"{t.key} starts {gap:.1f} px after the year ink (want {L.labels.card_gap:g})")
    return out


def zones(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, ky = _k(L, W, H)
    safe, z1, z2 = (_z(b, kx, ky) for b in (L.zones.safe, L.zones.z1, L.zones.z2))
    out = []
    for i in items:
        if i.kind in ("title", "year", "time") and i.slot == "z1":   # the ink in Z1; the halo may reach the margin
            ink = i.ink
            assert ink is not None
            if not inside(ink, z1, 0.5):
                out.append(f"{i.key} '{i.text}' ink {tuple(round(v) for v in ink)} outside Z1")
        elif i.kind in ("label", "area", "head", "note"):
            box = i.box
            assert box is not None
            if not inside(box, z2, 0.5):
                out.append(f"{i.key} '{i.text}' box {tuple(round(v) for v in box)} outside Z2")
        elif i.kind == "route":
            poly = i.poly
            assert poly is not None
            if not inside(corners_box(poly), z2, 0.5):
                out.append(f"{i.key} outside Z2")
        if i.kind in TEXT and i.kind != "caption":
            box = i.box
            assert box is not None
            if not inside(box, safe, 0.5):
                out.append(f"{i.key} '{i.text}' outside the text safe area")
    return out


def route_clear(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, _ = _k(L, W, H)
    rc = L.labels.route_clear * kx
    out = []
    for r in (i for i in items if i.kind == "route"):
        poly = r.poly
        assert poly is not None
        for t in (i for i in items if i.kind in TEXT and i.kind != "caption"):
            box = t.box
            assert box is not None
            d = lay.polyline_box_dist(poly, box)
            if d < rc - 1e-6:
                out.append(f"{r.key} {d:.0f} px from {t.key} '{t.text}' (min {rc:.0f})")
            for end in (poly[0], poly[-1]):
                if box[0] <= end[0] <= box[2] and box[1] <= end[1] <= box[3]:
                    out.append(f"{r.key} starts or ends at text {t.key}")
    return out


def caption_ground(items: list[Item], L: LayoutProfile) -> list[str]:
    out = []
    for c in (i for i in items if i.kind == "caption"):
        box = c.box
        assert box is not None
        area = (box[2] - box[0]) * (box[3] - box[1])
        hit = 0.0
        for r in items:
            if r.kind == "region" and r.level > 0.5:
                poly = r.poly
                assert poly is not None
                hit += lay.clip_area(poly, box)
        if area > 0 and hit / area > L.labels.caption_ground + 1e-6:
            out.append(f"caption '{c.text}' box {100 * hit / area:.0f}% over a highlighted fill "
                       f"(max {100 * L.labels.caption_ground:.0f}%)")
    return out


# ---------------------------------------------------------------- plan-level checks

def anchors(p: Plan, sc) -> list[str]:
    """L07: a route starts on its map point and ends on its map point or route_pin_gap short of its to_pin head."""
    out = []
    for i, (r, pts) in enumerate(zip(sc.routes or [], p.routes)):
        p0 = (r.points[0][0] * p.mw, r.points[0][1] * p.mh)
        if math.dist(p0, pts[0]) > 0.5:
            out.append(f"route:{i} start moved off its map anchor")
        sp = p.spans.get(f"route:{i}")
        if not sp:
            continue
        f = sp[0].t_in + p.route_draw[i]
        if r.to_pin is None:
            pe = (r.points[-1][0] * p.mw, r.points[-1][1] * p.mh)
            if math.dist(pe, pts[-1]) > 0.5:
                out.append(f"route:{i} end moved off its map anchor")
            continue
        end = p.to_screen(f, pts[-1])
        hb = lay.head_box(p, r.to_pin, f)
        d = lay.seg_box_dist(end, end, hb)
        gap = Units.of(p.W, p.H).x(p.L.labels.route_pin_gap)
        if not gap - 2 <= d <= gap + 4:
            out.append(f"route:{i} ends {d:.0f} px from pin {r.to_pin} head (want {gap:.0f})")
    return out


def stability(p: Plan) -> list[str]:
    T, out = p.L.timing, []
    gap = T.move_gap_s * p.fps
    for i, tr in p.tracks.items():
        moves = tr[1:]
        soft = [f for (f, _, hard) in moves if not hard]
        if len(soft) > T.moves_per_shot:
            out.append(f"label:{i} {len(soft)} non-urgent moves (max {T.moves_per_shot} per shot)")
        fs = [f for (f, _, _) in moves]
        for a, b in zip(fs, fs[1:]):
            if b - a < gap:
                out.append(f"label:{i} moves at frames {a} and {b}: closer than {T.move_gap_s:g} s")
    return out


def timing(p: Plan) -> list[str]:
    T, fps, out = p.L.timing, p.fps, []
    texts = {"title:0": p.title["text"] if p.title else ""}
    texts.update({f"fig:{i}": fg["text"] for i, fg in p.figs.items()})
    texts.update({f"label:{i}": t for i, t in enumerate(p.pin_labels)})
    texts.update({f"area:{k}": a.text for k, a in p.areas.items()})
    texts.update({f"heading:{k}": h.text for k, h in p.headings.items()})
    texts.update({f"note:{k}": n.text for k, n in p.notes.items()})
    for key, spans in p.spans.items():
        for s in spans:
            end = p.n if s.t_out is None else s.t_out
            need = max(T.min_hold_s, len(texts.get(key, "")) / T.chars_per_s) * fps
            if s.d_in == 0 and s.t_out == s.t_in:
                continue                     # hidden (logged by the planner)
            if end - (s.t_in + s.d_in) < need - 0.5:
                tail = "" if s.t_out is not None else " before the segment ends"
                out.append(f"{key} held {(end - s.t_in - s.d_in) / fps:.2f} s{tail} (min {need / fps:.2f})")
            if s.t_out is not None and not T.exit_min_s * fps - 0.5 <= s.d_out <= T.exit_max_s * fps + 0.5:
                out.append(f"{key} exit {s.d_out / fps:.2f} s outside {T.exit_min_s:g}-{T.exit_max_s:g} s")
    for b in p.beats:
        ent = sorted(b["entries"].values())
        if ent and ent[0] < b["settle_end"]:
            out.append(f"beat at {b['at']}: entries start before the camera settles")
        if b["exits"] and max(b["exits"].values()) + round(T.exit_s * fps) > (ent[0] if ent else p.n):
            out.append(f"beat at {b['at']}: exits still running after the first entry")
        for a, c in zip(ent, ent[1:]):
            if c - a < T.stagger_min_s * fps - 0.5:
                out.append(f"beat at {b['at']}: entries {a} and {c} staggered {(c - a) / fps:.2f} s "
                           f"(min {T.stagger_min_s:g})")
    return out


def dom_match(plan_items: list[Item], rects: list[dict]) -> list[str]:
    """Rendered CSS boxes against the plan (fonts loaded): each managed text and head within DOM_TOL px."""
    by = {r["id"]: r for r in rects if r.get("opacity", 1) > 0.05 and r["w"] > 0}
    out = []
    for i in plan_items:
        if i.kind not in DOM_IDS or i.css is None or i.opacity < 0.95:
            continue
        rid = "title" if i.kind == "title" else f"{DOM_IDS[i.kind]}:{i.key.split(':')[1]}"
        r = by.get(rid)
        if r is None:
            out.append(f"{rid} planned but not rendered")
            continue
        want = i.css if not i.angle else corners_box(lay.rot_corners(
            (i.css[0] + i.css[2]) / 2, (i.css[1] + i.css[3]) / 2, i.css[2] - i.css[0], i.css[3] - i.css[1], i.angle))
        got = (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])
        if max(abs(a - b) for a, b in zip(want, got)) > DOM_TOL:
            out.append(f"{rid} rendered at {tuple(round(v) for v in got)}, planned {tuple(round(v) for v in want)}")
    planned = {("title" if i.kind == "title" else f"{DOM_IDS[i.kind]}:{i.key.split(':')[1]}")
               for i in plan_items if i.kind in DOM_IDS}
    for rid in by:
        if rid.split(":")[0] in ("title", "figure", "pin", "area", "pinhead", "heading", "note") and rid not in planned:
            out.append(f"{rid} rendered but not planned")
    return out


def typefaces(rects: list[dict], max_n: int) -> list[str]:
    """T01: distinct typefaces of the visible text rects (DOM probe font_family; weights and italics of one family
    are one typeface)."""
    from video_engine.layout.printstyle import typeface
    faces: dict[str, list[str]] = {}
    for r in rects:
        if r.get("opacity", 1) > 0.05 and r["w"] > 0 and (r.get("text") or "").strip() and r.get("font_family"):
            faces.setdefault(typeface(r["font_family"]), []).append(r["id"])
    if len(faces) > max_n:
        return [f"{len(faces)} typefaces (max {max_n}): " + "; ".join(f"{k} {', '.join(v)}" for k, v in faces.items())]
    return []
