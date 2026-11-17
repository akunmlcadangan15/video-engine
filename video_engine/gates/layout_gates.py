"""Layout gates L01-L10 of beats map scenes (profile [layout]): every frame of the plan from video_engine/layout.py,
the caption events, and the rendered DOM at the beat and camera-move probe times."""
from __future__ import annotations

from pathlib import Path

from video_engine import layout as lay
from video_engine.layout import Item, Plan
from video_engine.timeline import Segment, Timeline
from video_engine.gates.core import Finding

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


def caption_items(events: list[tuple], seg: Segment, f: int) -> list[Item]:
    """Caption boxes on screen at segment frame f (events: compiled caption events in absolute frames)."""
    from video_engine.gates.layer2 import caption_box
    a = seg.start_frame + f
    return [Item(f"caption:{e[0]}", "caption", caption_box(e), float(e[3]), e[2]) for e in events if e[0] <= a < e[1]]


# ---------------------------------------------------------------- per-frame checks (lists of problems)

from video_engine.layout.constraints import _k, _z, overlap, _grow, boundary, budget, hero, grid, zones, route_clear, caption_ground, anchors, stability, timing, dom_match, typefaces  # noqa: F401

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
        from video_engine.render import hyperframes as hyperframes
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
    from video_engine import profiles as profiles
    ov_allowed = profiles.of(tl).reference_style.overlap_allowed
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
