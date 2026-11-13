"""Beats scene layout solver (_Planner)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .layout_camera import (
    _frange,
    animating,
    area_box,
    bound_drift,
    head_box,
    label_box,
    note_box,
    shrink_pulses,
)
from .layout_geometry import (
    Box,
    _closest,
    _pt_seg_dist,
    _r,
    corners_box,
    crosses_edge,
    halo,
    inflate,
    inside,
    overlap_area,
    point_in_poly,
    poly_edges,
    polyline_box_dist,
    quad_inside_poly,
    quad_outside_poly,
    rot_corners,
    seg_box_dist,
    text_box,
    text_metrics,
    wt,
)
from .layout_models import (
    ENTRY_ORDER,
    EXIT_ORDER,
    HEAD,
    HEAD_C,
    Heading,
    Label,
    LayoutProfile,
    Note,
    Plan,
    SHADOW,
    Span,
    Area,
    _detail,
    _frames,
)
from .units import Units

if TYPE_CHECKING:
    from .timeline import GsapScene, Segment, Timeline

class _Planner:
    sc: GsapScene
    L: LayoutProfile

    def __init__(self, seg: "Segment", tl: "Timeline", project_dir: Path):
        from . import hyperframes, maps, profiles
        self.hf = hyperframes
        sc = seg.gsap_scene_config
        assert sc is not None
        self.seg, self.tl, self.sc = seg, tl, sc
        S = tl.settings
        self.W, self.H, self.fps, self.n = S.width, S.height, S.fps, seg.n_frames
        L = profiles.of(tl).layout
        assert L is not None
        self.L = L
        prof = profiles.of(tl)
        self.theme = prof.render_values("hf")
        self.max_zoom = prof.camera.max_zoom
        lg = prof.legibility
        if lg is not None:   # read-only here: halo extents and the effective region fill opacity
            self.theme["legibility"] = lg.model_dump(mode="json")
        self.fill_opacity = lg.entity_fill_alpha if lg is not None else self.theme["region_fill_opacity"]
        self.map_path = project_dir / self.sc.map_asset
        iw, ih = maps.map_image_size(self.map_path)
        cover = max(self.W / iw, self.H / ih)
        self.mw, self.mh = iw * cover, ih * cover
        u = Units.of(self.W, self.H)
        self.kx, self.ky = u.kx, u.ky
        sc = self.sc
        self.pins = [hyperframes._pin_xy(p, sc, self.mw, self.mh) for p in sc.pins]
        self.regions = [[(x * self.mw, y * self.mh) for x, y in r.polygon] for r in sc.regions or []]
        self.routes_raw = [[(x * self.mw, y * self.mh) for x, y in r.points] for r in sc.routes or []]
        self.z1 = self.box(self.L.zones.z1)
        self.z2 = self.box(self.L.zones.z2)
        self.safe = self.box(self.L.zones.safe)
        self.T = self.L.timing
        self.f_in, self.f_out = _frames(self.T.enter_s, self.fps), _frames(self.T.exit_s, self.fps)
        self.f_stag = _frames(self.T.stagger_s, self.fps)
        self.f_settle = _frames(self.T.settle_s, self.fps)
        self.f_drop = _frames(self.T.head_drop_s, self.fps)
        self.label_role = self.theme.get("label_font", "script")
        self.label_role = "script" if self.label_role == "script" else "inter"
        self.title_role = "title" if self.theme.get("title_font") == "title" else "inter"
        self.label_wt: int | None = None
        self.pr = prof.print
        scene_flag = getattr(self.sc, "heading_ornaments", None)
        self.ornaments = bool(self.pr.heading_ornaments if scene_flag is None else scene_flag) if self.pr else False
        self.prb: dict | None = None
        if self.pr is not None:   # printed typography: fonts by the map surface
            from . import printstyle
            self.prb = printstyle.scene_block(self.pr, printstyle.surface(self.map_path, self.pr))
            self.roles = printstyle.roles(self.prb["surface"], self.pr)
            self.label_role, self.label_wt = self.roles["label"][0], wt(*self.roles["label"][:2])
            self.title_role = self.roles["title"][0]
        self.f_area_in = _frames(self.pr.title_in_s, self.fps) if self.pr else None
        self.log: list[str] = []

    def box(self, b) -> Box:
        return (b[0] * self.kx, b[1] * self.ky, b[2] * self.kx, b[3] * self.ky)

    # ------------------------------------------------------------------ sets per beat
    def beat_set(self, b, k: int = 0) -> set:
        s = {("head", i) for i in b.pins + b.markers} | {("label", i) for i in b.pins} | {("route", i) for i in b.routes}
        s |= {("fig", i) for i in b.card}
        if b.title and self.pr is not None:     # [print]: the title is printed inside the beat's region
            if b.region is not None:
                title = self.sc.title
                assert title is not None
                s.add(("area", self.area_id(title, b.region, False, True)))
            elif self.sc.title:
                self.log.append(f"beat at frame {b.at_frame}: title hidden (a printed title needs the beat's region)")
        elif b.title:
            s.add(("title", 0))
        s |= {("area", self.area_id(a.text, a.region, a.water, box=a.box)) for a in b.area_labels}
        if b.heading:
            s.add(("heading", k))
            self.heading_text[k] = b.heading
        for nt in b.notes:
            s.add(("note", self.note_id(nt)))
        return s

    def area_id(self, text: str, region: int, water: bool = False, title: bool = False,
                box: bool | None = None) -> int:
        k = (text, region, box)
        if k not in self.area_keys:
            self.area_keys.append(k)
        if water:
            self.area_water.add(self.area_keys.index(k))
        if title:
            self.area_title.add(self.area_keys.index(k))
        return self.area_keys.index(k)

    def note_id(self, nt) -> int:
        if not any(n is nt for n in self.note_list):
            self.note_list.append(nt)
        return next(j for j, n in enumerate(self.note_list) if n is nt)

    def content_beat(self, k: int):
        """The beat whose map content frames beat k: k itself, else the next beat with content (None = none)."""
        beats = self.sc.beats
        assert beats is not None
        for b in beats[k:]:
            if b.region is not None or b.routes or b.pins or b.markers:
                return b
        return None

    # ------------------------------------------------------------------ camera fit
    def content_box(self, b, s: float) -> Box:
        """Screen-space box (at scale s, translation 0) of a beat's map content."""
        pts = []
        if b.region is not None:
            pts += [(s * x, s * y) for x, y in self.regions[b.region]]
        for r in b.routes:
            pts += [(s * x, s * y) for x, y in self.trimmed_guess(r)]
        boxes = [corners_box(pts)] if pts else []
        lift = self.label_lift()
        for i in b.pins + b.markers:
            px, py = self.pins[i]
            boxes.append((s * px + HEAD[0], s * py + HEAD[1] - (lift if i in b.pins else 0.0),
                          s * px + HEAD[2], s * py + SHADOW[3]))
        return (min(q[0] for q in boxes), min(q[1] for q in boxes), max(q[2] for q in boxes), max(q[3] for q in boxes))

    def label_lift(self) -> float:
        """[print]: height above the pin head of the first-choice label (leader A: gap, leader, gap, label line), so
        the camera frames it; 0 without [print] (labels then sit beside the head)."""
        if self.pr is None:
            return 0.0
        prb = self.prb
        assert prb is not None
        px = (self.pr.label_px if prb["surface"] == "paper" else self.pr.label_dark_px) * self.ky
        return 2 * self.pr.leader_gap * self.ky + self.pr.leader_len * self.ky + 1.2 * px

    def trimmed_guess(self, r: int):
        return self.routes_raw[r]

    def fit(self, b) -> tuple[tuple[float, float], float]:
        if b is None:     # no beat has map content: the whole map
            return (0.5, 0.5), 1.0
        pad = self.L.zones.z2_pad
        inner = (self.z2[0] + pad * self.kx, self.z2[1] + pad * self.ky, self.z2[2] - pad * self.kx, self.z2[3] - pad * self.ky)
        aw, ah = inner[2] - inner[0], inner[3] - inner[1]
        lo, hi = 1.0, self.max_zoom   # a camera key cannot zoom further: small content stops at [camera] max_zoom
        for _ in range(40):
            m = (lo + hi) / 2
            cb = self.content_box(b, m)
            if cb[2] - cb[0] <= aw and cb[3] - cb[1] <= ah:
                lo = m
            else:
                hi = m
        from . import hyperframes

        def framed(s):   # content box on screen at zoom s, centred in Z2 inner (the render clamps to the map edge)
            cb = self.content_box(b, s)
            X = (inner[0] + inner[2]) / 2 - (cb[0] + cb[2]) / 2
            Y = (inner[1] + inner[3]) / 2 - (cb[1] + cb[3]) / 2
            c = ((self.W / 2 - X) / s, (self.H / 2 - Y) / s)
            st = hyperframes._state(c, s, self.W, self.H, self.mw, self.mh)
            return cb, c, (cb[0] + st["x"], cb[1] + st["y"], cb[2] + st["x"], cb[3] + st["y"])

        s = lo
        pulse = self.theme.get("camera_pulse")
        if pulse and pulse["dir"] > 0:   # push-in pulses: headroom so the pushed-in view still fits Z2 inner, as
            for q in (1.0, 0.75, 0.5, 0.25):   # far as the map edge keeps the content centred in it
                h = max(1.0, lo * math.exp(-pulse["zoom"] * q))
                if inside(framed(h)[2], inner, 1.0):
                    s = h
                    break
        cb, c, got = framed(s)
        if s <= 1.0 + 1e-6 and (cb[2] - cb[0] > aw + 1 or cb[3] - cb[1] > ah + 1) or not inside(got, inner, 1.0):
            self.log.append(f"beat at frame {b.at_frame}: content {_r(got)} does not fit Z2 inner {_r(inner)} "
                            f"at zoom {s:.2f} (map edge or size)")
        return (min(1.0, max(0.0, c[0] / self.mw)), min(1.0, max(0.0, c[1] / self.mh))), s

    # ------------------------------------------------------------------ schedule
    def schedule(self, items, start: int, gap: int, dur_of, anim: list) -> dict:
        """Start frames for `items` in order: each >= the previous start + gap and only when fewer than
        budget.animating windows overlap its own window."""
        out, t = {}, start
        cap = self.L.budget.animating
        for it in items:
            d = dur_of(it)
            while any(sum(1 for (a, z, _) in anim if a <= f < z) >= cap for f in range(t, t + d)):
                t += 1
            out[it] = t
            anim.append((t, t + d, f"{it[0]}:{it[1]}"))
            t += gap
        return out

    def run(self) -> Plan:
        sc, fps = self.sc, self.fps
        beats = sc.beats
        assert beats is not None
        self.area_keys: list[tuple[str, int, bool | None]] = []
        self.area_water: set[int] = set()
        self.area_title: set[int] = set()
        self.heading_text: dict[int, str] = {}
        self.note_list: list = []
        sets = [self.beat_set(b, k) for k, b in enumerate(beats)]
        nreg = len(self.regions)
        levels = [[(1.0 if b.region == r else self.L.labels.dim_alpha / max(1e-6, self.fill_opacity))
                   for r in range(nreg)] for b in beats]
        spans: dict[str, list[Span]] = {}
        region_steps: list[list] = [[] for _ in range(nreg)]
        anim: list[tuple[int, int, str]] = []
        keys = []
        beat_info = []
        route_draw = [_frames(r.draw_s or self.theme["route_draw_s"], fps) for r in sc.routes or []]
        count_f = {i: _frames(f.count_s or self.theme["counter_s"], fps) for i, f in enumerate(sc.figures or [])
                   if f.motion == "counter"}
        for k, b in enumerate(beats):
            f0 = b.at_frame
            prev = sets[k - 1] if k else set()
            exits = sorted(prev - sets[k], key=lambda e: (EXIT_ORDER.index(e[0]), e[1]))
            if k:
                exits += [("dim", r) for r in range(nreg) if levels[k][r] < levels[k - 1][r]]
            ex = self.schedule(exits, f0, _frames(self.T.stagger_min_s, fps), lambda e: self.f_out, anim)
            for e, t in ex.items():
                if e[0] == "dim":
                    region_steps[e[1]].append((t, self.f_out, levels[k - 1][e[1]], levels[k][e[1]]))
                else:
                    spans[f"{e[0]}:{e[1]}"][-1].t_out, spans[f"{e[0]}:{e[1]}"][-1].d_out = t, self.f_out
            ex_end = max([t + self.f_out for t in ex.values()], default=f0)
            settle_end = ex_end
            if k == 0 or b.camera:
                focus, zoom = self.fit(self.content_beat(k))
                if k == 0:
                    keys.append({"at_frame": 0, "focus": list(focus), "zoom": max(1.0, zoom / self.T.open_zoom_out), "move": "glide"})
                settle_end = ex_end + self.f_settle
                keys.append({"at_frame": settle_end, "focus": list(focus), "zoom": zoom, "move": "settle",
                             "move_s": round(self.f_settle / fps, 4)})
                if b.hold == "read":
                    keys[-1]["pulse"] = 0.0   # intent: the viewer reads; the wait keeps only the drift
            entries = sorted(sets[k] - prev, key=lambda e: (ENTRY_ORDER.index(e[0]), e[1]))
            regs = []
            for r in range(nreg):
                if k == 0:
                    regs.append(("region" if levels[0][r] >= 1 else "dim", r))
                elif levels[k][r] > levels[k - 1][r]:
                    regs.append(("region", r))
            entries = sorted(regs, key=lambda e: (ENTRY_ORDER.index(e[0]), e[1])) + entries

            def dur(e):
                if e[0] == "head":
                    return self.f_drop
                if e[0] == "route":
                    return route_draw[e[1]]
                if e[0] == "fig" and e[1] in count_f:
                    return max(self.f_in, count_f[e[1]])
                return self.span_in(e[0])
            en = self.schedule(entries, settle_end, self.f_stag, dur, anim)
            for e, t in en.items():
                if e[0] in ("region", "dim"):
                    region_steps[e[1]].append((t, self.f_in, 0.0 if k == 0 else levels[k - 1][e[1]], levels[k][e[1]]))
                else:
                    d = self.f_drop if e[0] == "head" else self.span_in(e[0])
                    spans.setdefault(f"{e[0]}:{e[1]}", []).append(Span(t, d))
            beat_info.append({"at": f0, "exits_end": ex_end, "settle_end": settle_end,
                              "entries": {f"{e[0]}:{e[1]}": t for e, t in en.items()},
                              "exits": {f"{e[0]}:{e[1]}": t for e, t in ex.items()}, "camera": k == 0 or b.camera})
        self.spans, self.region_steps, self.anim, self.beat_info = spans, region_steps, anim, beat_info
        log0 = list(self.log)
        pulse = self.theme.get("camera_pulse")
        rate = self.theme["camera_drift_per_s"]
        while True:
            self.log = list(log0)
            p = self.place(keys, route_draw)
            bad = self.pulse_conflicts(p) if pulse else {}
            drift = self.drift_conflicts(p) if rate > 0 else {}
            shrunk = bool(bad) and pulse is not None and shrink_pulses(keys, bad, pulse["zoom"])
            bounded = bool(drift) and bound_drift(keys, drift)
            if not (shrunk or bounded):
                break
        return p

    def place(self, keys: list[dict], route_draw: list[int]) -> Plan:
        """Camera plan (through the same code as the render) and every placement that depends on it."""
        sc, fps, n = self.sc, self.fps, self.n
        from .timeline import CameraKey
        cks = [CameraKey(**{k2: v for k2, v in kk.items() if not (k2 == "move_s" and kk["move"] != "settle")}) for kk in keys]
        tmp = sc.model_copy(update={"camera_path": cks})
        self.cam = self.hf.camera_plan(tmp, self.W, self.H, self.mw, self.mh, n, fps, self.theme)
        p = Plan(self.W, self.H, fps, n, self.mw, self.mh, self.L, self.theme, keys, self.cam, self.pins,
                 [x.label for x in sc.pins], self.regions, [], [], self.spans, self.region_steps)
        p.anim, p.beats = list(self.anim), self.beat_info
        p.route_draw = route_draw
        p.pr = self.prb
        p.routes = [self.trim_route(i, p) for i in range(len(self.routes_raw))]
        self.z1_items(p)
        if self.pr is not None:
            self.place_headings(p)
            self.place_notes(p)
        self.place_areas(p)
        self.place_labels(p)
        p.log = self.log
        return p

    def span_in(self, kind: str) -> int:
        """Entry length of an element kind ([print]: titles and place names, headings, notes have their own)."""
        if self.pr is not None:
            if kind == "area":
                area_in = self.f_area_in
                assert area_in is not None
                return area_in
            if kind == "heading":
                dur = max(self.pr.heading_fade_s, self.pr.rule_draw_s) if self.ornaments else self.pr.heading_fade_s
                return _frames(dur, self.fps)
            if kind == "note":
                return _frames(self.pr.note_write_s, self.fps)
        return self.f_in

    def pulse_conflicts(self, p: Plan) -> dict[int, tuple[int, set[int]]]:
        """Per key whose wait breaks a per-frame layout rule (L01-L07 checks of video_engine/gates/layout_gates.py) or
        moves a pin label on a frame off the base drift (inside a pulse or away after one): (away pulses in the wait,
        indices of the away pulses whose frames break it)."""
        from .gates import layout_gates as G
        L, W, H = self.L, self.W, self.H
        hit: dict[int, set[int]] = {}
        n_away: dict[int, int] = {}
        ats = [k["at_frame"] for k in p.keys]
        from . import layout as _layout
        moves = _layout.label_move_frames(p.log)
        for leg in p.cam["legs"]:
            if "away" not in leg:
                continue
            i, m = max(j for j, a in enumerate(ats) if a <= leg["f0"]), leg["away"]
            cur = p.keys[i].get("pulse")
            n_away[i] = len(cur) if isinstance(cur, list) else max(n_away.get(i, 0), m + 1)
            for f in range(leg["f0"], leg["f1"]):
                items = _layout.frame_items(p, f)
                if (G.overlap(items, L, W, H) or G.boundary(items, L, W, H) or G.zones(items, L, W, H)
                        or G.route_clear(items, L, W, H) or G.budget(items, L, len(animating(p, f)))
                        or f in moves):
                    hit.setdefault(i, set()).add(m)
                    break
        return {i: (n_away[i], h) for i, h in hit.items()}

    def drift_conflicts(self, p: Plan) -> dict[int, float]:
        """Per key whose wait breaks a per-frame overlap, boundary, zone or route rule (the L01, L02, L06, L07 checks
        of video_engine/gates/layout_gates.py) on a frame of its base drift that the same frame with the camera held
        at the wait's first frame does not break (the drift caused it): the drift ln offset of the first such frame."""
        import dataclasses
        from .gates import layout_gates as G
        L, W, H = self.L, self.W, self.H
        ats = [k["at_frame"] for k in p.keys]

        def broken(pp, f):
            from . import layout as _layout
            items = _layout.frame_items(pp, f)
            return bool(G.overlap(items, L, W, H) or G.boundary(items, L, W, H) or G.zones(items, L, W, H)
                        or G.route_clear(items, L, W, H))

        out: dict[int, float] = {}
        held: dict[int, Plan] = {}
        for leg in p.cam["legs"]:
            if leg["ease"] != "drift" or "away" in leg:
                continue
            i = max(j for j, a in enumerate(ats) if a <= leg["f0"])
            if i in out:
                continue
            for f in range(leg["f0"] + 1, leg["f1"]):
                if not broken(p, f):
                    continue
                if i not in held:
                    w0 = next(x["f0"] for x in p.cam["legs"] if x["f0"] >= ats[i])
                    held[i] = dataclasses.replace(p, cam={"start": p.camera(w0), "legs": []})
                if not broken(held[i], f):
                    out[i] = abs(math.log(p.camera(f)["s"] / p.camera(ats[i])["s"]))
                    break
        return out

    # ------------------------------------------------------------------ routes
    def trim_route(self, i: int, p: Plan):
        routes = self.sc.routes
        assert routes is not None
        r = routes[i]
        pts = list(self.routes_raw[i])
        if r.to_pin is None:
            return pts
        span = (self.spans.get(f"route:{i}") or [Span(0, 1)])[0]
        c = p.camera(span.t_in + p.route_draw[i])
        s = c["s"]
        px, py = self.pins[r.to_pin]
        gap = self.L.labels.route_pin_gap * self.kx
        head = (px * s + HEAD[0], py * s + HEAD[1], px * s + HEAD[2], py * s + SHADOW[3])
        # walk the polyline (map px) and stop at the last screen point >= gap (euclidean) from the head box
        segs = [(pts[j], pts[j + 1]) for j in range(len(pts) - 1)]
        out = [pts[0]]
        for a, b in segs:
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            steps = max(1, int(L * s))
            last_ok = None
            for q in range(steps + 1):
                t = q / steps
                m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                if seg_box_dist((m[0] * s, m[1] * s), (m[0] * s, m[1] * s), head) < gap:
                    if last_ok is not None:
                        out.append(last_ok)
                    return out
                last_ok = m
            out.append(b)
        return out

    # ------------------------------------------------------------------ Z1
    def z1_items(self, p: Plan):
        sc, L = self.sc, self.L
        col = self.L.grid.edges()[0] * self.kx
        top = math.ceil(self.z1[1] / (L.grid.unit * self.ky)) * (L.grid.unit * self.ky)
        beats = sc.beats
        assert beats is not None
        if sc.title and any(b.title for b in beats) and self.pr is None:
            px = L.sizes.title * self.ky
            m = text_metrics(sc.title, self.title_role, px, 1.04)
            x = col - m.ink[0]
            p.title = {"text": sc.title, "x": round(x, 2), "y": top, "px": px, "box": text_box(m, x, top, halo(self.theme, True, px)),
                       "css": (x, top, x + m.adv, top + m.h),
                       "ink": (x + m.ink[0], top + m.ink[1], x + m.ink[2], top + m.ink[3])}
        figures = sc.figures
        for b in beats:
            if not b.card:
                continue
            if self.pr is not None:
                self.centre_card(p, b)
                continue
            assert figures is not None
            fy = figures[b.card[0]]
            ytext = self.hf.figure_text(fy, fy.value, self.theme)
            px = L.sizes.year * self.ky
            my = text_metrics(ytext, "serif", px, 1.0, weight=800, nums=True)
            x = col - my.ink[0]
            base = top + my.base
            p.figs[b.card[0]] = {"text": ytext, "kind": "year", "x": round(x, 2), "y": top, "px": px,
                                 "box": text_box(my, x, top, halo(self.theme, False, px)), "base": base,
                                 "css": (x, top, x + my.adv, top + my.h),
                                 "ink": (x + my.ink[0], top + my.ink[1], x + my.ink[2], top + my.ink[3])}
            if len(b.card) > 1:
                ft = figures[b.card[1]]
                ttext = self.hf.figure_text(ft, ft.value, self.theme)
                pt = L.sizes.time * self.ky
                mt = text_metrics(ttext, self.title_role, pt, 1.0, nums=True)
                tx = x + my.ink[2] + L.labels.card_gap * self.kx - mt.ink[0]
                ty = base - mt.base
                p.figs[b.card[1]] = {"text": ttext, "kind": "time", "x": round(tx, 2), "y": round(ty, 2), "px": pt,
                                     "box": text_box(mt, tx, ty, halo(self.theme, False, pt)), "base": base,
                                 "css": (tx, ty, tx + mt.adv, ty + mt.h),
                                     "ink": (tx + mt.ink[0], ty + mt.ink[1], tx + mt.ink[2], ty + mt.ink[3])}

    def centre_card(self, p: Plan, b):
        """[print]: the year alone, centred (ink width year_width x the frame within year_px_min..max), the time
        centred under it in Cinzel caps."""
        pr, sc = self.pr, self.sc
        assert pr is not None
        figures = sc.figures
        assert figures is not None
        fy = figures[b.card[0]]
        ytext = self.hf.figure_text(fy, fy.value, self.theme)
        role, w = self.roles["year"][:2]
        ref = text_metrics(ytext, role, 100.0, 1.0, weight=wt(role, w), nums=True)
        px = pr.year_width * self.W / max(1e-6, (ref.ink[2] - ref.ink[0]) / 100)
        px = round(min(pr.year_px_max * self.ky, max(pr.year_px_min * self.ky, px)), 2)
        my = text_metrics(ytext, role, px, 1.0, weight=wt(role, w), nums=True)
        x = round(pr.year_cx * self.kx - (my.ink[0] + my.ink[2]) / 2, 2)
        y = round(pr.year_cy * self.ky - (my.ink[1] + my.ink[3]) / 2, 2)
        ink = (x + my.ink[0], y + my.ink[1], x + my.ink[2], y + my.ink[3])
        p.figs[b.card[0]] = {"text": ytext, "kind": "year", "x": x, "y": y, "px": px, "slot": "centre", "weight": w,
                             "box": text_box(my, x, y, halo(self.theme, False, px)), "base": y + my.base,
                             "css": (x, y, x + my.adv, y + my.h), "ink": ink}
        if len(b.card) > 1:
            ft = figures[b.card[1]]
            ttext = self.hf.figure_text(ft, ft.value, self.theme).upper()
            role_t, wt_t, trk = self.roles["time"][:3]
            pt = pr.time_px * self.ky
            mt = text_metrics(ttext, role_t, pt, 1.0, trk, wt(role_t, wt_t), nums=True)
            tx = round(pr.year_cx * self.kx - (mt.adv - trk * pt) / 2, 2)
            ty = round(ink[3] + pr.time_gap * self.ky - mt.ink[1], 2)
            p.figs[b.card[1]] = {"text": ttext, "kind": "time", "x": tx, "y": ty, "px": pt, "slot": "centre",
                                 "weight": wt_t, "tracking": trk, "box": text_box(mt, tx, ty, halo(self.theme, False, pt)),
                                 "base": ty + mt.base, "css": (tx, ty, tx + mt.adv, ty + mt.h),
                                 "ink": (tx + mt.ink[0], ty + mt.ink[1], tx + mt.ink[2], ty + mt.ink[3])}

    def place_headings(self, p: Plan):
        """[print] date headings: centred caps on the baseline heading_baseline_y. With ornaments, each side then
        gets a gap, a diamond, a gap and a rule (rules drawn from the centre outwards). The plain form, the
        default, is the caps alone."""
        pr = self.pr
        assert pr is not None
        role, w, trk = self.roles["heading"][:3]
        for k, text in sorted(self.heading_text.items()):
            T = text.upper()
            px = pr.heading_px * self.ky
            m = text_metrics(T, role, px, 1.0, trk, wt(role, w))
            mh = text_metrics("H", role, px, 1.0, 0.0, wt(role, w))
            base = pr.heading_baseline_y * self.ky
            y = round(base - m.base, 2)
            x = round(self.W / 2 - (m.adv - trk * px) / 2, 2)
            ink = (x + m.ink[0], y + m.ink[1], x + m.ink[2], y + m.ink[3])
            rule_y = base - (mh.base - mh.ink[1]) / 2
            tb = text_box(m, x, y, halo(self.theme, False, px))
            rules: tuple[tuple[float, float], ...] = ()
            box = tb
            if self.ornaments:
                g, d, ln = pr.rule_gap * self.kx, pr.diamond_px * self.kx, pr.rule_len * self.kx
                right = (ink[2] + g + d + g, ink[2] + g + d + g + ln)
                left = (ink[0] - g - d - g - ln, ink[0] - g - d - g)
                rules = (left, right)
                box = (min(tb[0], left[0]), min(tb[1], rule_y - d / 2), max(tb[2], right[1]), max(tb[3], rule_y + d / 2))
            p.headings[k] = Heading(T, px, x, y, box, (x, y, x + m.adv, y + m.h), base, rule_y, rules,
                                    self.ornaments)

    def place_notes(self, p: Plan):
        """[print] hand annotations: Kalam centred on their map point, rotated; the arrow leaves the rotated text box
        12 px out towards arrow_to and stops 14 px short of it (screen px at the note's entry, then fixed size)."""
        pr = self.pr
        assert pr is not None
        for n, nt in enumerate(self.note_list):
            px = pr.note_px * self.ky
            m = text_metrics(nt.text, "hand", px, 1.0)
            tb = text_box(m, 0, 0, halo(self.theme, False, px))
            w, h = tb[2] - tb[0], tb[3] - tb[1]
            anchor = (nt.at[0] * self.mw, nt.at[1] * self.mh)
            arrow = None
            sp = p.spans.get(f"note:{n}")
            if nt.arrow_to and sp:
                c = p.camera(sp[0].t_in)
                tx = (nt.arrow_to[0] * self.mw - anchor[0]) * c["s"]
                ty = (nt.arrow_to[1] * self.mh - anchor[1]) * c["s"]
                dist = math.hypot(tx, ty)
                ux, uy = (tx / dist, ty / dist) if dist else (0.0, 1.0)
                a = math.radians(-nt.rotation)
                lx, ly = ux * math.cos(a) - uy * math.sin(a), ux * math.sin(a) + uy * math.cos(a)
                t_edge = min((w / 2) / abs(lx) if abs(lx) > 1e-9 else math.inf, (h / 2) / abs(ly) if abs(ly) > 1e-9 else math.inf)
                t0, t1 = t_edge + 12 * self.kx, dist - 14 * self.kx
                if t1 - t0 >= 24 * self.kx:
                    arrow = (round(ux * t0, 2), round(uy * t0, 2), round(ux * t1, 2), round(uy * t1, 2))
                else:
                    self.log.append(f"note '{nt.text}': arrow target too close to the text, arrow dropped")
            p.notes[n] = Note(nt.text, px, w, h, anchor, nt.rotation, arrow)

    # ------------------------------------------------------------------ obstacles of one frame
    def obstacles(self, p: Plan, f: float, skip_pin: int | None = None, labels_upto: int | None = None):
        """(text boxes, head boxes, route polylines, region polygons) visible at frame f in screen px."""
        texts, heads, routes, polys = [], [], [], []
        if p.title and p.visible("title:0", f) > 0:
            texts.append(p.title["box"])
        for i, fg in p.figs.items():
            if p.visible(f"fig:{i}", f) > 0:
                texts.append(fg["box"])
        for k, a in p.areas.items():
            if p.visible(f"area:{k}", f) > 0:
                texts.append(area_box(p, a, f))
        for k, hd in p.headings.items():
            if p.visible(f"heading:{k}", f) > 0:
                texts.append(hd.box)
        for n, nt in p.notes.items():
            if p.visible(f"note:{n}", f) > 0:
                texts.append(note_box(p, nt, f))
        for i in range(len(self.pins)):
            if i != skip_pin and p.visible(f"head:{i}", f) > 0:
                heads.append(head_box(p, i, f))
            if labels_upto is not None and i < labels_upto and p.visible(f"label:{i}", f) > 0:
                lab = p.label_at(i, f)
                if lab is not None:
                    texts.append(label_box(p, i, lab, f))
        for i, r in enumerate(p.routes):
            if p.visible(f"route:{i}", f) > 0:
                routes.append([p.to_screen(f, q) for q in r])
        for i, poly in enumerate(self.regions):
            if p.region_level(i, f) > 0:
                polys.append([p.to_screen(f, q) for q in poly])
        return texts, heads, routes, polys

    def detail(self, p: Plan, f: float, b: Box) -> float:
        ii, (iw, ih) = _detail(str(self.map_path), self.map_path.stat().st_mtime)
        c = p.camera(f)
        sx = (iw / 4) / self.mw
        sy = (ih / 4) / self.mh
        x0 = int(max(0, min(ii.shape[1] - 1, (b[0] - c["x"]) / c["s"] * sx)))
        x1 = int(max(0, min(ii.shape[1] - 1, (b[2] - c["x"]) / c["s"] * sx)))
        y0 = int(max(0, min(ii.shape[0] - 1, (b[1] - c["y"]) / c["s"] * sy)))
        y1 = int(max(0, min(ii.shape[0] - 1, (b[3] - c["y"]) / c["s"] * sy)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float((ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]) / ((x1 - x0) * (y1 - y0)))

    # ------------------------------------------------------------------ area labels
    def place_areas(self, p: Plan):
        L = self.L
        for k, (text, reg, box) in enumerate(self.area_keys):
            if self.pr is not None:
                self.place_area_print(p, k, text, reg)
                continue
            span = p.spans[f"area:{k}"][0]
            f = span.t_in
            px = L.sizes.area_label * self.ky
            m = text_metrics(text.upper(), "title", px, 1.0, L.sizes.area_tracking)
            tb = text_box(m, 0, 0, halo(self.theme, False, px))
            w, h = tb[2] - tb[0], tb[3] - tb[1]
            poly = [p.to_screen(f, q) for q in self.regions[reg]]
            texts, heads, routes, _ = self.obstacles(p, f)
            inset = L.labels.area_inset * self.kx
            clear = L.zones.group_gap * self.kx    # an area label is its own group: >= group_gap to heads and text
            rclear = max(L.labels.route_clear, L.zones.group_gap) * self.kx
            c = p.camera(f)

            def free(cs) -> bool:
                bb = corners_box(cs)
                if not inside(bb, self.z2):
                    return False
                if any(overlap_area(inflate(bb, clear), t) > 0 for t in texts + heads):
                    return False
                return all(polyline_box_dist(r, bb) >= rclear for r in routes)
            cx = sum(q[0] for q in poly) / len(poly)
            cy = sum(q[1] for q in poly) / len(poly)
            bb = corners_box(poly)
            best = None
            for angle in [0.0, self.long_axis(poly)]:
                cands = []
                sx, sy = L.grid.unit * self.kx, L.grid.unit * self.ky
                y = bb[1]
                while y <= bb[3]:
                    x = bb[0]
                    while x <= bb[2]:
                        cands.append((math.hypot(x - cx, y - cy), x, y))
                        x += sx
                    y += sy
                for _, x, y in sorted(cands):
                    cs = rot_corners(x, y, w + 2 * inset, h + 2 * inset, angle)
                    if quad_inside_poly(cs, poly) and free(rot_corners(x, y, w, h, angle)):
                        best = (x, y, angle, None)
                        break
                if best:
                    break
            inside_ok = best is not None
            if best is None:   # outside the polygon with a leader from its edge
                for _, x, y in sorted((math.hypot(x - cx, y - cy), x, y)
                                      for x in _frange(self.z2[0], self.z2[2], L.grid.unit * self.kx)
                                      for y in _frange(self.z2[1], self.z2[3], L.grid.unit * self.ky)):
                    cs = rot_corners(x, y, w + 2 * inset, h + 2 * inset, 0.0)
                    if not quad_outside_poly(cs, poly) or not free(rot_corners(x, y, w, h, 0.0)):
                        continue
                    edge = min(((_pt_seg_dist((x, y), a, b2), a, b2) for a, b2 in poly_edges(poly)))
                    _, a, b2 = edge
                    q = _closest(a, b2, (x, y))
                    if math.hypot(q[0] - x, q[1] - y) - min(w, h) / 2 > L.labels.leader_max * self.kx:
                        continue
                    best = (x, y, 0.0, q)
                    break
            if best is None:
                self.log.append(f"area label '{text}' hidden: no position inside or outside region {reg}")
                p.spans[f"area:{k}"] = [Span(s.t_in, 0, s.t_in, 0) for s in p.spans[f"area:{k}"]]
                continue
            x, y, angle, q = best
            anchor = ((x - c["x"]) / c["s"], (y - c["y"]) / c["s"])
            lead = None
            if q is not None:
                lead = ((q[0] - c["x"]) / c["s"], (q[1] - c["y"]) / c["s"])
                self.log.append(f"area label '{text}' outside region {reg} with a leader")
            p.areas[k] = Area(text, reg, px, w, h, anchor, round(angle, 2), lead, inside_ok, k in self.area_water, key=k,
                              tracking=L.sizes.area_tracking, box=box)

    def place_area_print(self, p: Plan, k: int, text: str, reg: int):
        """[print] title or place name inside its region: the largest size from title_px down to title_px_min whose
        width is <= title_width_max x the region's screen box, at angle 0 or the long axis clamped to
        +-title_rot_max, nearest the region's pole of inaccessibility; inside with area_inset at the (wider) entry
        tracking, clear of other text, heads and routes."""
        L, pr = self.L, self.pr
        assert pr is not None
        box = self.area_keys[k][2]
        role, w8, trk = self.roles["area"][:3]
        if k in self.area_water:            # water names in the label face (italic on paper)
            role = self.roles["label"][0]
        w8 = wt(role, w8)
        trk_in = trk + (pr.title_tracking_in - pr.title_tracking)
        T = text.upper()
        f = p.spans[f"area:{k}"][0].t_in
        poly = [p.to_screen(f, q) for q in self.regions[reg]]
        bb = corners_box(poly)
        texts, heads, routes, _ = self.obstacles(p, f)
        inset = L.labels.area_inset * self.kx
        clear = L.zones.group_gap * self.kx
        rclear = max(L.labels.route_clear, L.zones.group_gap) * self.kx
        c = p.camera(f)

        def free(cs) -> bool:
            b2 = corners_box(cs)
            if not inside(b2, self.z2):
                return False
            if any(overlap_area(inflate(b2, clear), t) > 0 for t in texts + heads):
                return False
            return all(polyline_box_dist(r2, b2) >= rclear for r2 in routes)
        sx, sy = L.grid.unit * self.kx, L.grid.unit * self.ky
        grid_pts = [(x, y) for y in _frange(bb[1], bb[3], sy) for x in _frange(bb[0], bb[2], sx)
                    if point_in_poly((x, y), poly)]
        if not grid_pts:
            grid_pts = [((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)]
        pole = max(grid_pts, key=lambda q: min(_pt_seg_dist(q, a, b2) for a, b2 in poly_edges(poly)))
        rot = max(-pr.title_rot_max, min(pr.title_rot_max, self.long_axis(poly)))
        angles = [0.0] + ([round(rot, 2)] if abs(rot) >= 0.5 else [])
        cands = sorted(grid_pts, key=lambda q: math.hypot(q[0] - pole[0], q[1] - pole[1]))
        px = pr.title_px * self.ky
        while px >= pr.title_px_min * self.ky - 1e-6:
            m = text_metrics(T, role, px, 1.0, trk, w8)
            if m.adv - trk * px <= pr.title_width_max * (bb[2] - bb[0]):
                tb = text_box(m, 0, 0, halo(self.theme, False, px))
                tbi = text_box(text_metrics(T, role, px, 1.0, trk_in, w8), 0, 0, halo(self.theme, False, px))
                w, h, w_in = tb[2] - tb[0], tb[3] - tb[1], tbi[2] - tbi[0]
                for angle in angles:
                    for x, y in cands:
                        if quad_inside_poly(rot_corners(x, y, w_in + 2 * inset, h + 2 * inset, angle), poly) and \
                                free(rot_corners(x, y, w_in, h, angle)):
                            anchor = ((x - c["x"]) / c["s"], (y - c["y"]) / c["s"])
                            p.areas[k] = Area(text, reg, round(px, 2), w, h, anchor, angle, None, True,
                                              k in self.area_water, key=k, role=role, weight=w8, tracking=trk,
                                              tracking_in=trk_in, title=k in self.area_title, box=box)
                            return
            px -= 4 * self.ky
        px = pr.title_px_min * self.ky
        m = text_metrics(T, role, px, 1.0, trk, w8)
        tb = text_box(m, 0, 0, halo(self.theme, False, px))
        tbi = text_box(text_metrics(T, role, px, 1.0, trk_in, w8), 0, 0, halo(self.theme, False, px))
        w, h, w_in = tb[2] - tb[0], tb[3] - tb[1], tbi[2] - tbi[0]
        cx, cy = sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly)
        for _, x, y in sorted((math.hypot(x - cx, y - cy), x, y)
                              for x in _frange(self.z2[0], self.z2[2], sx)
                              for y in _frange(self.z2[1], self.z2[3], sy)):
            cs = rot_corners(x, y, w_in + 2 * inset, h + 2 * inset, 0.0)
            if not quad_outside_poly(cs, poly) or not free(rot_corners(x, y, w_in, h, 0.0)):
                continue
            _, a, b2 = min(((_pt_seg_dist((x, y), a, b2), a, b2) for a, b2 in poly_edges(poly)))
            q = _closest(a, b2, (x, y))
            if math.hypot(q[0] - x, q[1] - y) - min(w, h) / 2 > L.labels.leader_max * self.kx:
                continue
            anchor = ((x - c["x"]) / c["s"], (y - c["y"]) / c["s"])
            lead = ((q[0] - c["x"]) / c["s"], (q[1] - c["y"]) / c["s"])
            p.areas[k] = Area(text, reg, round(px, 2), w, h, anchor, 0.0, lead, False, k in self.area_water,
                              key=k, role=role, weight=w8, tracking=trk, tracking_in=trk_in,
                              title=k in self.area_title, box=box)
            self.log.append(f"{'title' if k in self.area_title else 'place name'} '{text}' outside region {reg} "
                            f"with a leader")
            return
        self.log.append(f"{'title' if k in self.area_title else 'place name'} '{text}' hidden: no position inside "
                        f"or outside region {reg} from {pr.title_px:g} to {pr.title_px_min:g} px")
        p.spans[f"area:{k}"] = [Span(s.t_in, 0, s.t_in, 0) for s in p.spans[f"area:{k}"]]

    @staticmethod
    def long_axis(poly) -> float:
        n = len(poly)
        mx = sum(q[0] for q in poly) / n
        my = sum(q[1] for q in poly) / n
        sxx = sum((q[0] - mx) ** 2 for q in poly)
        syy = sum((q[1] - my) ** 2 for q in poly)
        sxy = sum((q[0] - mx) * (q[1] - my) for q in poly)
        a = math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))
        return max(-45.0, min(45.0, a))

    # ------------------------------------------------------------------ pin labels
    def candidates(self, text: str) -> list[Label]:
        L = self.L
        out = []
        sizes = []
        s = L.sizes.pin_label
        while s >= L.sizes.pin_label_min - 1e-6:
            sizes.append(s)
            s -= L.sizes.pin_label_step
        if self.pr is not None:   # [print]: one size; first choice above the pin on a vertical leader
            prb = self.prb
            assert prb is not None
            sizes = [self.pr.label_px if prb["surface"] == "paper" else self.pr.label_dark_px]
            px = sizes[0] * self.ky
            m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
            gap, ll = self.pr.leader_gap * self.ky, self.pr.leader_len * self.ky
            y1 = HEAD[1] - gap
            y2 = y1 - ll
            out.append(Label(round(-m.adv / 2, 2), round(y2 - gap - m.ink[3], 2), px, "A", (0.0, y1, 0.0, y2), -1))
        gx, gy = L.labels.offset * self.kx, L.labels.offset * self.ky
        hx0, hy0, hx1, hy1 = HEAD
        hm = HEAD_C[1]
        for si, sz in enumerate(sizes):
            px = sz * self.ky
            m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
            w, h = m.adv, m.h
            pos = {"TR": (hx1 + gx, hy0 - h / 2), "BR": (hx1 + gx, hy1 - h / 2), "TL": (hx0 - gx - w, hy0 - h / 2),
                   "BL": (hx0 - gx - w, hy1 - h / 2), "TC": (-w / 2, hy0 - gy - h), "BC": (-w / 2, SHADOW[3] + gy),
                   "R": (hx1 + gx, hm - h / 2), "L": (hx0 - gx - w, hm - h / 2)}
            for ri, name in enumerate(L.labels.order):
                dx, dy = pos[name]
                out.append(Label(round(dx, 2), round(dy, 2), px, name, None, ri + si * len(L.labels.order)))
        # leaders at the full size: straight 2 px line from the head edge, label attached at its far end
        px = sizes[0] * self.ky
        m = text_metrics(text, self.label_role, px, 1.0, weight=self.label_wt)
        w, h = m.adv, m.h
        base_rank = len(out)
        dirs = [("TR", 1, -1), ("TL", -1, -1), ("BR", 1, 1), ("BL", -1, 1), ("R", 1, 0), ("L", -1, 0), ("T", 0, -1), ("B", 0, 1)]
        for li, length in enumerate(range(48, int(L.labels.leader_max) + 1, 24)):
            for di, (name, ux, uy) in enumerate(dirs):
                nrm = math.hypot(ux, uy)
                ux2, uy2 = ux / nrm, uy / nrm
                sx, sy = HEAD_C[0] + ux2 * 24, HEAD_C[1] + uy2 * 24
                ex, ey = sx + ux2 * length * self.kx, sy + uy2 * length * self.kx
                dx = ex if ux > 0 else (ex - w if ux < 0 else ex - w / 2)
                dy = ey - h / 2 if uy == 0 else (ey - h if uy < 0 else ey)
                out.append(Label(round(dx, 2), round(dy, 2), px, f"leader:{name}:{length}", (sx, sy, ex, ey),
                                 base_rank + li * len(dirs) + di))
        return out

    def label_ok(self, p: Plan, i: int, lab: Label, f: float, obs) -> bool:
        texts, heads, routes, polys = obs
        b = label_box(p, i, lab, f)
        L = self.L
        if not inside(b, self.z2):
            return False
        ec = L.labels.edge_clear * self.kx
        if any(crosses_edge(inflate(b, ec), poly) for poly in polys):
            return False
        tc = L.labels.text_clear * self.kx
        if any(overlap_area(inflate(b, tc), t) > 0 for t in texts + heads):
            return False
        rc = L.labels.route_clear * self.kx
        if any(polyline_box_dist(r, b) < rc for r in routes):
            return False
        if lab.leader is not None:
            P = p.to_screen(f, self.pins[i])
            a = (P[0] + lab.leader[0], P[1] + lab.leader[1])
            z = (P[0] + lab.leader[2], P[1] + lab.leader[3])
            if any(seg_box_dist(a, z, inflate(t, tc / 2)) == 0 for t in texts + heads):
                return False
        return True

    def cost(self, p: Plan, i: int, lab: Label, f: float, prev: Optional[Label]) -> float:
        b = label_box(p, i, lab, f)
        cxp = (b[0] + b[2]) / 2
        cyp = (b[1] + b[3]) / 2
        P = p.to_screen(f, self.pins[i])
        dist = math.hypot(cxp - P[0] - HEAD_C[0], cyp - P[1] - HEAD_C[1])
        return 10 * lab.rank + dist / 16 + 50 * (prev is None or prev.name != lab.name or prev.px != lab.px) + \
            5 * self.detail(p, f, b)

    def place_labels(self, p: Plan):
        T = self.T
        fps = self.fps
        hold = _frames(T.move_hold_s, fps)
        gapf = _frames(T.move_gap_s, fps)
        for i, pin in enumerate(self.sc.pins):
            sp = p.spans.get(f"label:{i}")
            if not sp:
                continue
            cands = self.candidates(pin.label)
            track: list = []
            moves_soft = 0
            last_move = -10 ** 9
            for s in sp:
                end = s.t_out + s.d_out if s.t_out is not None else self.n
                cur: Optional[Label] = None
                pending = None
                obs_at: dict[int, tuple] = {}

                def obs(f):
                    if f not in obs_at:
                        obs_at[f] = self.obstacles(p, f, skip_pin=i, labels_upto=i)
                    return obs_at[f]

                def run(c, f0):
                    """Frames from f0 the candidate stays valid (look-ahead to the span end)."""
                    f = f0
                    while f < end and self.label_ok(p, i, c, f, obs(f)):
                        f += 2
                    return min(f, end) - f0

                def pick(valid, f, prev):
                    if not valid:
                        return None
                    runs = {id(c): run(c, f) for c in valid}
                    longest = max(runs.values())
                    return min((c for c in valid if runs[id(c)] == longest), key=lambda c: self.cost(p, i, c, f, prev))
                for f in range(s.t_in, end):
                    valid = [c for c in cands if self.label_ok(p, i, c, f, obs(f))]
                    if cur is None and f == s.t_in:
                        prev = track[-1][1] if track else None
                        best = pick(valid, f, prev)
                        cur = best
                        track.append((f, best, False))
                        if best is None:
                            self.log.append(f"pin label '{pin.label}' hidden at frame {f}: no valid position")
                        elif best.leader is not None:
                            self.log.append(f"pin label '{pin.label}' uses a leader ({best.name}) from frame {f}")
                        continue
                    if cur is None:
                        continue
                    if not any(c.name == cur.name and c.px == cur.px for c in valid):
                        best = pick(valid, f, cur)
                        track.append((f, best, True))
                        self.log.append(f"pin label '{pin.label}' moved at frame {f} (hard rule): "
                                        f"{cur.name} -> {best.name if best else 'hidden'}")
                        cur, pending, last_move = best, None, f
                        continue
                    if moves_soft >= T.moves_per_shot or f - last_move < gapf:
                        continue
                    cc = self.cost(p, i, cur, f, cur)
                    best = min(valid, key=lambda c: self.cost(p, i, c, f, cur))
                    if best.name != cur.name and self.cost(p, i, best, f, cur) <= (1 - T.move_gain) * cc:
                        if pending is None or pending[0] != best.name:
                            pending = (best.name, f)
                        elif f - pending[1] >= hold:
                            track.append((f, best, False))
                            self.log.append(f"pin label '{pin.label}' moved at frame {f}: {cur.name} -> {best.name}")
                            cur, pending, last_move = best, None, f
                            moves_soft += 1
                    else:
                        pending = None
            p.tracks[i] = track
            for (f, lab, _) in track[1:]:
                p.anim.append((f - _frames(T.move_fade_s, fps) // 2, f + _frames(T.move_fade_s, fps) // 2, f"move:{i}"))
