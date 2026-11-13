"""Camera path evaluation, pulse/drift helpers, and per-frame screen layout snapshots."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from .layout_geometry import (
    Box,
    Metrics,
    corners_box,
    halo,
    rot_corners,
    text_box,
    text_metrics,
    wt,
)
from .layout_models import Area, HEAD, Label, Note, Plan

def camera_at(cam: dict, f: float) -> dict:
    prev = cam["start"]
    for L in cam["legs"]:
        if f < L["f1"]:
            if f <= L["f0"]:
                return prev
            p = (f - L["f0"]) / (L["f1"] - L["f0"])
            from .hyperframes import leg_ease
            e = leg_ease(L, prev["s"])(p)
            return {k: prev[k] + (L["to"][k] - prev[k]) * e for k in ("x", "y", "s", "t", "r") if k in prev}
        prev = L["to"]
    return prev


def _glide(t):
    # smooth cosine ease-in-out: rise in and ease out, zero slope at both ends, no velocity jump at leg boundaries
    return (1 - math.cos(math.pi * t)) / 2


def _quart_in_out(t):   # GSAP power3.inOut
    return 8 * t ** 4 if t < 0.5 else 1 - (-2 * t + 2) ** 4 / 2


EASES = {"glide": _glide, "drift": lambda t: t, "smooth": _glide, "whip": _quart_in_out, "lin": lambda t: t}
PULSE_STEPS = (0.75, 0.5, 0.25, 0.0)    # pulse sizes (fractions of [camera] pulse_zoom) tried when a wait conflicts
PULSE_FLOOR = 1e-3                      # ln zoom; a smaller pulse size is 0 (drift only)
_LABEL_MOVE = re.compile(r"\bat frame (\d+)\b")


def label_move_frames(log: list[str]) -> set[int]:
    """Frames at which the planner log records a pin label hidden or moved ("pin label ... at frame N")."""
    return {int(m.group(1)) for ln in log if ln.startswith("pin label") for m in _LABEL_MOVE.finditer(ln)}


def shrink_pulses(keys: list[dict], bad: dict[int, tuple[int, set[int]]], zoom: float) -> bool:
    """Set keys[i]["pulse"] to one size per away pulse of its wait, each pulse listed in `bad` (key -> (away pulses,
    indices)) taking the next smaller PULSE_STEPS size of `zoom` (the profile pulse size), rounded to 4 decimals.
    Every step is strictly smaller; a size below PULSE_FLOOR is 0. Returns whether any size changed (a positive
    size always changes, so repeated calls reach 0 for every listed pulse)."""
    changed = False
    for i, (n_away, hit) in bad.items():
        cur = keys[i].get("pulse", zoom)
        cur = list(cur) if isinstance(cur, list) else [cur]
        cur = [cur[min(m, len(cur) - 1)] for m in range(n_away)]
        for m in hit:
            nxt = round(next((zoom * q for q in PULSE_STEPS if round(zoom * q, 4) < cur[m] - 1e-9), 0.0), 4)
            nxt = 0.0 if nxt < PULSE_FLOOR or nxt >= cur[m] else nxt
            changed |= nxt != cur[m]
            cur[m] = nxt
        keys[i]["pulse"] = cur
    return changed


DRIFT_SHARE = 0.7      # a conflicting wait's drift bound: this share of the drift offset of its first conflict frame
DRIFT_MIN = 0.03       # ln zoom; smallest drift bound (a turn every DRIFT_MIN / camera_drift_per_s seconds)


def bound_drift(keys: list[dict], hit: dict[int, float]) -> bool:
    """Set keys[i]["drift_max"] to DRIFT_SHARE of the conflict offset of each wait in `hit` (key -> drift ln offset of
    its first conflict frame), never below DRIFT_MIN and only when smaller than the current bound, rounded to 4
    decimals. Returns whether any bound changed (repeated calls reach DRIFT_MIN and then report no change)."""
    changed = False
    for i, g in hit.items():
        cur = keys[i].get("drift_max")
        nxt = round(max(DRIFT_MIN, DRIFT_SHARE * g), 4)
        if cur is not None and nxt >= cur - 1e-9:
            nxt = round(max(DRIFT_MIN, DRIFT_SHARE * cur), 4)
        if cur is None or nxt < cur - 1e-9:
            keys[i]["drift_max"] = nxt
            changed = True
    return changed


def cubic_bezier(x1: float, y1: float, x2: float, y2: float, t: float) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2) at progress t (x solved by bisection; same math as the template's bez)."""
    if t <= 0 or t >= 1:
        return min(1.0, max(0.0, t))
    bx = lambda u: 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if bx(m) < t else (lo, m)
    u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


def head_box(p: Plan, i: int, f: float) -> Box:
    P = p.to_screen(f, p.pins[i])
    return (P[0] + HEAD[0], P[1] + HEAD[1], P[0] + HEAD[2], P[1] + HEAD[3])


def _label_metrics(p: Plan, i: int, lab: Label) -> Metrics:
    if p.pr is not None:
        role, w = p.pr["roles"]["label"][:2]
        return text_metrics(p.pin_labels[i], role, lab.px, 1.0, weight=wt(role, w))
    role = "script" if p.theme.get("label_font", "script") == "script" else "inter"
    return text_metrics(p.pin_labels[i], role, lab.px, 1.0)


def label_box(p: Plan, i: int, lab: Label, f: float) -> Box:
    P = p.to_screen(f, p.pins[i])
    return text_box(_label_metrics(p, i, lab), P[0] + lab.dx, P[1] + lab.dy, halo(p.theme, False, lab.px, True))


def area_tracking(p: Plan, a: Area, f: float) -> float:
    """Tracking (em) of an area label at frame f: tracking_in easing (easeOutCubic) to tracking over each entry."""
    if a.tracking_in is not None:
        for s in p.spans.get(f"area:{a.key}", []):
            if s.d_in > 0 and s.t_in <= f < s.t_in + s.d_in:
                u = (f - s.t_in) / s.d_in
                return a.tracking_in + (a.tracking - a.tracking_in) * (1 - (1 - u) ** 3)
    return a.tracking


def area_metrics(p: Plan, a: Area, f: float) -> Metrics:
    return text_metrics(a.text.upper(), a.role, a.px, 1.0, round(area_tracking(p, a, f), 4), a.weight)


def area_corners(p: Plan, a: Area, f: float):
    c = p.to_screen(f, a.anchor)
    if a.tracking_in is None or area_tracking(p, a, f) == a.tracking:
        return rot_corners(c[0], c[1], a.w, a.h, a.angle)
    tb = text_box(area_metrics(p, a, f), 0, 0, halo(p.theme, False, a.px))
    return rot_corners(c[0], c[1], tb[2] - tb[0], tb[3] - tb[1], a.angle)


def note_corners(p: Plan, nt: Note, f: float):
    c = p.to_screen(f, nt.anchor)
    return rot_corners(c[0], c[1], nt.w, nt.h, nt.angle)


def note_box(p: Plan, nt: Note, f: float) -> Box:
    return corners_box(note_corners(p, nt, f))


def _frange(a: float, b: float, step: float):
    v = a
    while v <= b:
        yield v
        v += step


def area_box(p: Plan, a: Area, f: float) -> Box:
    return corners_box(area_corners(p, a, f))


# ---------------------------------------------------------------------------------------------------- frame state

@dataclass
class Item:
    key: str
    kind: str                     # title | year | time | label | area | head | route | region | caption | leader
    box: Optional[Box] = None
    px: float = 0.0
    text: str = ""
    opacity: float = 1.0
    pin: Optional[int] = None     # labels and heads: the pin index
    poly: Optional[list] = None   # regions: screen polygon; routes: screen polyline; areas: rotated corners
    level: float = 0.0            # regions: fill level (1 = full)
    seg: Optional[tuple] = None   # leaders: screen segment
    ink: Optional[Box] = None     # Z1 texts: ink box
    region: Optional[int] = None  # areas: their region
    inside: bool = True           # areas: placed inside the polygon
    css: Optional[Box] = None     # texts and heads: CSS box as the browser lays it out (DOM check); areas: unrotated
    base: Optional[float] = None  # Z1 texts: baseline y
    angle: float = 0.0            # areas: rotation (deg)
    slot: str = "z1"              # title / year / time: "z1" (grid) or "centre" ([print] year alone, headings)


def frame_items(p: Plan, f: float) -> list[Item]:
    out: list[Item] = []
    if p.title and (o := p.visible("title:0", f)) > 0:
        out.append(Item("title:0", "title", p.title["box"], p.title["px"], p.title["text"], o, ink=p.title["ink"],
                        css=p.title["css"]))
    for i, fg in p.figs.items():
        if (o := p.visible(f"fig:{i}", f)) > 0:
            out.append(Item(f"fig:{i}", fg["kind"], fg["box"], fg["px"], fg["text"], o, ink=fg["ink"], css=fg["css"],
                            base=fg["base"], slot=fg.get("slot", "z1")))
    for k, hd in p.headings.items():
        if (o := p.visible(f"heading:{k}", f)) > 0:
            out.append(Item(f"heading:{k}", "heading", hd.box, hd.px, hd.text, o, css=hd.css, base=hd.base,
                            slot="centre"))
    for n, nt in p.notes.items():
        if (o := p.visible(f"note:{n}", f)) > 0:
            cs = note_corners(p, nt, f)
            c = p.to_screen(f, nt.anchor)
            m = text_metrics(nt.text, "hand", nt.px, 1.0)
            out.append(Item(f"note:{n}", "note", corners_box(cs), nt.px, nt.text, o, poly=cs, angle=nt.angle,
                            css=(c[0] - m.adv / 2, c[1] - m.h / 2, c[0] + m.adv / 2, c[1] + m.h / 2), slot="map"))
            if nt.arrow:
                out.append(Item(f"leader:note:{n}", "leader", None, 0, "", o,
                                seg=((c[0] + nt.arrow[0], c[1] + nt.arrow[1]), (c[0] + nt.arrow[2], c[1] + nt.arrow[3]))))
    for k, a in p.areas.items():
        if (o := p.visible(f"area:{k}", f)) > 0:
            cs = area_corners(p, a, f)
            m = area_metrics(p, a, f)
            c = p.to_screen(f, a.anchor)
            out.append(Item(f"area:{k}", "area", corners_box(cs), a.px, a.text, o, poly=cs, region=a.region,
                            inside=a.inside, css=(c[0] - m.adv / 2, c[1] - m.h / 2, c[0] + m.adv / 2, c[1] + m.h / 2),
                            angle=a.angle))
            if a.leader:
                c = p.to_screen(f, a.anchor)
                out.append(Item(f"leader:area:{k}", "leader", None, 0, "", o, seg=(p.to_screen(f, a.leader), c)))
    for i in range(len(p.pins)):
        if (o := p.visible(f"head:{i}", f)) > 0:
            hb = head_box(p, i, f)
            out.append(Item(f"head:{i}", "head", hb, 0, "", o, pin=i, css=hb))
        if (o := p.visible(f"label:{i}", f)) > 0 and (lab := p.label_at(i, f)) is not None:
            P = p.to_screen(f, p.pins[i])
            m = _label_metrics(p, i, lab)
            out.append(Item(f"label:{i}", "label", label_box(p, i, lab, f), lab.px, p.pin_labels[i], o, pin=i,
                            css=(P[0] + lab.dx, P[1] + lab.dy, P[0] + lab.dx + m.adv, P[1] + lab.dy + m.h)))
            if lab.leader:
                P = p.to_screen(f, p.pins[i])
                out.append(Item(f"leader:{i}", "leader", None, 0, "", o, pin=i,
                                seg=((P[0] + lab.leader[0], P[1] + lab.leader[1]), (P[0] + lab.leader[2], P[1] + lab.leader[3]))))
    for i, r in enumerate(p.routes):
        if (o := p.visible(f"route:{i}", f)) > 0:
            out.append(Item(f"route:{i}", "route", None, 0, "", o, poly=[p.to_screen(f, q) for q in r]))
    for i, poly in enumerate(p.regions):
        if (lv := p.region_level(i, f)) > 0:
            out.append(Item(f"region:{i}", "region", None, 0, "", 1.0, poly=[p.to_screen(f, q) for q in poly], level=lv))
    return out


def animating(p: Plan, f: float) -> list[str]:
    return [k for (a, z, k) in p.anim if a <= f < z]


def scene_layout(p: Plan) -> dict:
    """The `layout` block of the scene data (seconds, output px; map px for anchors)."""
    fps = p.fps
    t = lambda f: round(f / fps, 4)
    els = {k: [[t(s.t_in), t(s.d_in), None if s.t_out is None else t(s.t_out), t(s.d_out)] for s in v]
           for k, v in p.spans.items()}
    out = {"els": els, "dim": p.L.labels.dim_alpha,
           "regions": [[[t(a), t(d), x, y] for (a, d, x, y) in st] for st in p.region_steps],
           "move_fade_s": p.L.timing.move_fade_s, "leader_px": p.L.labels.leader_px}
    if p.L.fly is not None:
        out["fly"] = p.L.fly.model_dump()
    if p.title:
        out["title"] = {"x": p.title["x"], "y": p.title["y"], "px": p.title["px"]}
    figures = {str(i): {"x": fg["x"], "y": fg["y"], "px": fg["px"]} for i, fg in p.figs.items()}
    out["figures"] = figures
    out["labels"] = {str(i): [[t(f), None if lab is None else
                               {"x": lab.dx, "y": lab.dy, "px": lab.px, "leader": lab.leader}, hard]
                              for (f, lab, hard) in tr] for i, tr in p.tracks.items()}
    out["areas"] = [{"key": k, "text": a.text.upper(), "px": a.px, "tracking": a.tracking,
                     "anchor": [round(a.anchor[0], 2), round(a.anchor[1], 2)], "angle": a.angle,
                     "leader": None if a.leader is None else [round(a.leader[0], 2), round(a.leader[1], 2)],
                     "region": a.region, **({"water": True} if a.water else {}),
                     **({"box": a.box} if a.box is not None else {}),
                     **({"role": a.role, "weight": a.weight, "tracking_in": a.tracking_in, "title": a.title}
                        if p.pr is not None else {})}
                    for k, a in p.areas.items()]
    if p.pr is not None:
        for i, fg in p.figs.items():
            figures[str(i)].update({k: fg[k] for k in ("slot", "weight", "tracking") if k in fg})
        out["headings"] = [{"key": k, "text": h.text, "x": h.x, "y": h.y, "px": h.px, "rule_y": round(h.rule_y, 2),
                            "ornaments": h.ornaments,
                            "rules": [[round(v, 2) for v in r] for r in h.rules]} for k, h in p.headings.items()]
        out["notes"] = [{"key": n, "text": nt.text, "px": nt.px, "angle": nt.angle,
                         "anchor": [round(nt.anchor[0], 2), round(nt.anchor[1], 2)], "arrow": nt.arrow}
                        for n, nt in p.notes.items()]
    return out
