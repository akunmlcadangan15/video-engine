"""Layout gates L01-L10 of beats map scenes (profile [layout]): every frame of the plan from video_engine/layout.py,
the caption events, and the rendered DOM at the beat and camera-move probe times."""
from __future__ import annotations

import math
from pathlib import Path

from .. import layout as lay
from ..layout import Item, LayoutProfile, Plan, corners_box, inflate, inside, overlap_area
from ..timeline import Segment, Timeline
from ..units import Units
from .core import Finding

TEXT = ("title", "year", "time", "label", "area", "heading", "note", "caption")
GATES = {
    "L01": "overlap: text boxes (halo included) + text_clear, heads, leaders: 0 px2",
    "L02": "boundary: pin labels + edge_clear off every region edge; area labels inset inside their region",
    "L03": "budget: texts, labelled pins, routes, animating, big numbers, saturated regions per frame",
    "L04": "hero: the largest text >= hero_ratio x the next (caption and hand notes excluded)",
    "L05": "grid: Z1 ink on a column edge, tops and gaps on the unit, data card on one baseline",
    "L06": "zones: texts in the safe area, Z1 texts in Z1, map labels, heads and routes in Z2",
    "L07": "route anchors: ends on map anchors, never at text, >= route_clear from text",
    "L08": "stability: label moves per shot, never two within move_gap_s",
    "L09": "caption ground: highlighted fill under the caption box <= caption_ground",
    "L10": "timing: hold, stagger, exit length, camera settle before entries",
}
PRINT_GATES = {"T01": "typefaces: distinct typefaces per probed frame <= print.max_typefaces (caption excluded)"}
DOM_TOL = 3.0       # px: rendered CSS box vs the plan box
DOM_IDS = {"title": "title", "year": "figure", "time": "figure", "label": "pin", "area": "area", "head": "pinhead",
           "heading": "heading", "note": "note"}


def _k(L: LayoutProfile, W: int, H: int):
    u = Units.of(W, H)
    return u.kx, u.ky


def _z(b, kx, ky):
    return (b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky)


def caption_items(events: list[tuple], seg: Segment, f: int) -> list[Item]:
    """Caption boxes on screen at segment frame f (events: compiled caption events in absolute frames)."""
    from .layer2 import caption_box
    a = seg.start_frame + f
    return [Item(f"caption:{e[0]}", "caption", caption_box(e), float(e[3]), e[2]) for e in events if e[0] <= a < e[1]]


# ---------------------------------------------------------------- per-frame checks (lists of problems)

def overlap(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, _ = _k(L, W, H)
    m = L.labels.text_clear * kx
    g = L.zones.group_gap * kx
    texts = [i for i in items if i.kind in TEXT and i.box]
    heads = [i for i in items if i.kind == "head"]
    out = []
    card = ("year", "time")
    for j, a in enumerate(texts):
        for b in texts[j + 1:]:
            mm = 0.0 if a.kind in card and b.kind in card else m   # one group: the data card sets its own gap (L05)
            if overlap_area(inflate(a.box, mm), b.box) > 1e-6:
                out.append(f"{a.key} '{a.text}' and {b.key} '{b.text}' closer than {mm:.0f} px")
        for h in heads:
            if a.kind == "label" and a.pin == h.pin:
                continue                      # own head: the label offset rule (L02 candidates) sets that gap
            mm = g if a.kind == "area" else m
            if overlap_area(inflate(a.box, mm), h.box) > 1e-6:
                out.append(f"{a.key} '{a.text}' closer than {mm:.0f} px to {h.key}")
    for j, a in enumerate(heads):
        for b in heads[j + 1:]:
            if overlap_area(a.box, b.box) > 1e-6:
                out.append(f"{a.key} and {b.key} overlap")
    for ld in (i for i in items if i.kind == "leader"):
        own = f"label:{ld.pin}" if ld.pin is not None else ld.key.replace("leader:", "")
        for t in texts:
            if t.key != own and lay.seg_box_dist(ld.seg[0], ld.seg[1], inflate(t.box, m / 2)) == 0:
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
            b = inflate(t.box, L.labels.edge_clear * kx)
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
            if min(abs(i.ink[0] - e) for e in edges) > G.snap_tol:
                out.append(f"{i.key} '{i.text}' ink x {i.ink[0]:.1f} off the column edges (tol {G.snap_tol:g})")
            top = i.css[1]
            if abs(top - round(top / u) * u) > G.unit_tol:
                out.append(f"{i.key} '{i.text}' top y {top:.1f} not a multiple of {G.unit}")
    years = [i for i in items if i.kind == "year" and i.slot == "z1"]
    for t in (i for i in items if i.kind == "time" and i.slot == "z1"):
        if not years:
            continue                          # the year has already left (exit order)
        y = years[0]
        if abs(t.base - y.base) > G.unit_tol:
            out.append(f"{t.key} baseline {t.base:.1f} != year baseline {y.base:.1f}")
        gap = t.ink[0] - y.ink[2]
        if abs(gap - L.labels.card_gap * kx) > G.snap_tol:
            out.append(f"{t.key} starts {gap:.1f} px after the year ink (want {L.labels.card_gap:g})")
    return out


def zones(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, ky = _k(L, W, H)
    safe, z1, z2 = (_z(b, kx, ky) for b in (L.zones.safe, L.zones.z1, L.zones.z2))
    out = []
    for i in items:
        if i.kind in ("title", "year", "time") and i.slot == "z1":   # the ink in Z1; the halo may reach the margin
            if not inside(i.ink, z1, 0.5):
                out.append(f"{i.key} '{i.text}' ink {tuple(round(v) for v in i.ink)} outside Z1")
        elif i.kind in ("label", "area", "head", "note"):
            if not inside(i.box, z2, 0.5):
                out.append(f"{i.key} '{i.text}' box {tuple(round(v) for v in i.box)} outside Z2")
        elif i.kind == "route":
            if not inside(corners_box(i.poly), z2, 0.5):
                out.append(f"{i.key} outside Z2")
        if i.kind in TEXT and i.kind != "caption" and not inside(i.box, safe, 0.5):
            out.append(f"{i.key} '{i.text}' outside the text safe area")
    return out


def route_clear(items: list[Item], L: LayoutProfile, W: int = 1080, H: int = 1920) -> list[str]:
    kx, _ = _k(L, W, H)
    rc = L.labels.route_clear * kx
    out = []
    for r in (i for i in items if i.kind == "route"):
        for t in (i for i in items if i.kind in TEXT and i.kind != "caption"):
            d = lay.polyline_box_dist(r.poly, t.box)
            if d < rc - 1e-6:
                out.append(f"{r.key} {d:.0f} px from {t.key} '{t.text}' (min {rc:.0f})")
            for end in (r.poly[0], r.poly[-1]):
                if t.box[0] <= end[0] <= t.box[2] and t.box[1] <= end[1] <= t.box[3]:
                    out.append(f"{r.key} starts or ends at text {t.key}")
    return out


def caption_ground(items: list[Item], L: LayoutProfile) -> list[str]:
    out = []
    for c in (i for i in items if i.kind == "caption"):
        area = (c.box[2] - c.box[0]) * (c.box[3] - c.box[1])
        hit = sum(lay.clip_area(r.poly, c.box) for r in items if r.kind == "region" and r.level > 0.5)
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
    from ..printstyle import typeface
    faces: dict[str, list[str]] = {}
    for r in rects:
        if r.get("opacity", 1) > 0.05 and r["w"] > 0 and (r.get("text") or "").strip() and r.get("font_family"):
            faces.setdefault(typeface(r["font_family"]), []).append(r["id"])
    if len(faces) > max_n:
        return [f"{len(faces)} typefaces (max {max_n}): " + "; ".join(f"{k} {', '.join(v)}" for k, v in faces.items())]
    return []


# ---------------------------------------------------------------- runner

def probe_frames(p: Plan) -> list[int]:
    """Beat-settled frames (after the last entry of each beat) and the middle of each camera leg."""
    fs = set()
    for b in p.beats:
        ends = [t + 20 for t in b["entries"].values()]
        fs.add(min(p.n - 1, max(ends, default=b["settle_end"])))
    for L in p.cam["legs"]:
        if L["f1"] - L["f0"] >= 8:
            fs.add((L["f0"] + L["f1"]) // 2)
    return sorted(f for f in fs if 0 <= f < p.n)


def check_segment(s: Segment, tl: Timeline, project_dir: Path, events: list[tuple],
                  dom_dir: Path | None = None, dom_res: dict[int, dict] | None = None) -> list[Finding]:
    p = lay.plan(s, tl, project_dir)
    W, H, L = p.W, p.H, p.L
    seg_ev = [e for e in events if e[1] > s.start_frame and e[0] < s.end_frame]
    bad: dict[str, list[str]] = {g: [] for g in GATES}

    def note(g, f, probs):
        for m in probs:
            if len(bad[g]) < 6:
                bad[g].append(f"frame {f}: {m}")
            elif len(bad[g]) == 6:
                bad[g].append("...")
    for f in range(p.n):      # every frame, camera motion included
        items = lay.frame_items(p, f) + caption_items(seg_ev, s, f)
        note("L01", f, overlap(items, L, W, H))
        note("L02", f, boundary(items, L, W, H))
        note("L03", f, budget(items, L, len(lay.animating(p, f))))
        note("L04", f, hero(items, L))
        note("L05", f, grid(items, L, W, H))
        note("L06", f, zones(items, L, W, H))
        note("L07", f, route_clear(items, L, W, H))
        note("L09", f, caption_ground(items, L))
    bad["L07"] += anchors(p, s.gsap_scene_config)
    bad["L08"] += stability(p)
    bad["L10"] += timing(p)
    pr = p.pr
    if pr is not None:
        bad.update({g: [] for g in PRINT_GATES})
    probes = 0
    if dom_dir is not None:
        from .. import hyperframes
        frames = probe_frames(p)
        cached_items = (dom_res or {}).get(s.seg_index, {}).get("all_times") or []
        by_t = {round(i["t"], 4): i for i in cached_items}
        missing = [f / p.fps for f in frames if round(f / p.fps, 4) not in by_t]
        if missing:
            res = hyperframes.probe_dom(dom_dir, missing, W, H,
                                        ref=hyperframes.keys(s, tl, project_dir)[0])
            for i in res["times"]:
                by_t[round(i["t"], 4)] = i
        for f in frames:
            item = by_t[round(f / p.fps, 4)]
            note("L01", f, [f"DOM: {m}" for m in dom_match(lay.frame_items(p, f), item["rects"])])
            if pr is not None:
                note("T01", f, typefaces(item["rects"], int(pr["max_typefaces"])))
        probes = len(frames)
    from .. import profiles
    ov_allowed = bool((profiles.of(tl).reference_style or {}).get("overlap", {}).get("allowed"))
    out = []
    for g, desc in {**GATES, **(PRINT_GATES if pr is not None else {})}.items():
        if bad[g]:
            for m in bad[g]:
                lv = "info" if (g == "L01" and ov_allowed and "DOM:" not in m) else "fail"
                out.append(Finding(g, lv, m, s.seg_index))
        elif g in PRINT_GATES and not probes:
            out.append(Finding(g, "warn", f"{desc}: not checked (no DOM probe)", s.seg_index))
        else:
            extra = f"; DOM matches the plan at {probes} probes" if g == "L01" and probes else ""
            tail = f"{probes} probes clean" if g in PRINT_GATES else f"{p.n} frames clean{extra}"
            out.append(Finding(g, "pass", f"{desc}: {tail}", s.seg_index))
    for m in p.log:
        out.append(Finding("L06" if "Z2" in m else "L02", "warn", f"layout: {m}", s.seg_index))
    return out


def run(tl: Timeline, project_dir: Path, events: list[tuple], workdir: Path | None = None,
        dom_res: dict[int, dict] | None = None) -> list[Finding]:
    """All beats segments; workdir = the preflight workdir holding the built HyperFrames dirs (None = no DOM)."""
    out = []
    for s in tl.segments:
        sc = s.gsap_scene_config
        if sc is None or not sc.beats:
            continue
        if lay.profile_layout(tl) is None:
            out.append(Finding("L06", "fail", "gsap_scene_config.beats needs a profile with a [layout] section",
                               s.seg_index))
            continue
        if not (project_dir / sc.map_asset).is_file():
            continue
        d = workdir / "hf" / f"seg{s.seg_index:03d}" if workdir is not None else None
        out += check_segment(s, tl, project_dir, events, d if d is not None and (d / "index.html").is_file() else None, dom_res)
    return out
