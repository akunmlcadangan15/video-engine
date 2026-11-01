"""Channel style gates (profile `[gates.*]`; an absent table = gate off). Input: timeline, project dir, caption words,
voiceover onset. H01 hook, C01 hard cuts and shot changes, P01 camera motion, V01 visual type at shot changes, N01
novelty, K01/K02 keyword return, D01/D02 duration, C02 CTA/end card. S01 (silence) runs on the final file
(`silence_findings`)."""
from __future__ import annotations

import re
from pathlib import Path

import math

from .. import config, hyperframes, overlays, pauses, profiles, transitions
from ..util import EngineError
from ..timeline import HYPERFRAMES, CaptionWord, Segment, Timeline
from .core import Finding


PLAIN_NUMBERS = {"number_group": "", "number_decimal": "."}


def shot_changes(tl: Timeline) -> list[int]:
    """Frames of every segment boundary (hard cut or [transitions] transition). Camera moves inside a segment
    (glide, whip, pulse, drift, continuous zoom) are never shot changes."""
    return [s.start_frame for s in tl.segments[1:]]


def hard_cuts(tl: Timeline) -> list[int]:
    """Frames of hard cuts: segment boundaries without a [transitions] transition."""
    drawn = {w.boundary for w in transitions.windows(tl)}
    return [f for f in shot_changes(tl) if f not in drawn]


def element_events(s: Segment, fps: int) -> list[tuple[int, str]]:
    """(absolute frame, text) of every on-screen element appearance of a segment: pins, cards, region highlights,
    routes, title, figures (numbers without a thousands separator), drawn overlays."""
    out = []
    if s.engine_type == HYPERFRAMES:
        sc = s.gsap_scene_config
        out += [(s.start_frame + hyperframes.pin_frame(p, i, s.n_frames, fps), p.label) for i, p in enumerate(sc.pins)]
        out += [(s.start_frame + hyperframes.card_frame(c, s.n_frames, fps), " ".join(filter(None, [c.title, c.subtitle])))
                for c in sc.cards]
        out += [(s.start_frame + r.at_frame, "") for r in sc.regions or []]   # a highlight shows no text
        out += [(s.start_frame + (r.at_frame if r.label_at_frame is None else r.label_at_frame), r.label)
                for r in sc.regions or [] if r.label]
        out += [(s.start_frame + c.at_frame, "") for r in sc.regions or [] for c in r.changes]
        out += [(s.start_frame + b.at_frame, "") for b in sc.borders or []]
        out += [(s.start_frame + r.at_frame, "") for r in sc.routes or []]    # a route shows no text
        if sc.title:
            out.append((s.start_frame + (sc.title_at_frame or 0), sc.title))
        out += [(s.start_frame + f.at_frame, hyperframes.figure_text(f, f.value, PLAIN_NUMBERS)) for f in sc.figures or []]
    else:
        out += [(s.start_frame + overlays.frame_range(o, s)[0], " ".join(filter(None, [o.text, o.subtitle])))
                for o in overlays.drawn(s)]
    return sorted(out)


def camera_events(s: Segment) -> list[int]:
    """Absolute frames of the camera_path keys (each starts a new leg or the final drift: a new view counts as
    something new on screen)."""
    sc = s.gsap_scene_config if s.engine_type == HYPERFRAMES else None
    keys = (sc.globe.keys if sc.globe else sc.camera_path) if sc else None
    return [s.start_frame + k.at_frame for k in keys] if keys else []


def screen_text(tl: Timeline, a: int, b: int) -> list[str]:
    """Texts of elements on screen at any frame in [a, b) (pins and cards stay until the segment end)."""
    fps, out = tl.settings.fps, []
    for s in tl.segments:
        if s.end_frame <= a or s.start_frame >= b:
            continue
        if s.engine_type == HYPERFRAMES:
            out += [t for f, t in element_events(s, fps) if f < b]
        else:
            for o in overlays.drawn(s):
                lo, hi = (s.start_frame + f for f in overlays.frame_range(o, s))
                if lo < b and hi > a:
                    out.append(" ".join(filter(None, [o.text, o.subtitle])))
    return out


def h01_hook(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None, g: profiles.HookGate) -> list[Finding]:
    fps, out = tl.settings.fps, []

    def grade(what: str, t: float, warn: float, fail: float) -> None:
        lv = "fail" if t > fail + 1e-9 else "warn" if t > warn + 1e-9 else "pass"
        lim = fail if lv == "fail" else warn
        out.append(Finding("H01", lv, f"{what} at {t:.2f} s" + (f" (> {lim} s)" if lv != "pass" else f" (<= {warn} s)")))
    vo = tl.audio_chain.voiceover_path
    if vo and (project_dir / vo).is_file():
        grade("narration starts", pauses.speech_start_frame(project_dir / vo, fps) / fps, g.vo_warn_s, g.vo_fail_s)
    else:
        out.append(Finding("H01", "skip", "no voiceover: narration start not checked"))
    if words:
        grade("first caption", words[0].start_frame / fps, g.caption_warn_s, g.caption_fail_s)
    else:
        out.append(Finding("H01", "skip", "no captions yet: first caption not checked"))
    s0 = tl.segments[0]
    if g.open_on_moving_map and (s0.engine_type != HYPERFRAMES or (not s0.gsap_scene_config.camera_path
                                                                     and s0.gsap_scene_config.camera_preset == "static")):
        out.append(Finding("H01", "fail", "frame 0 must be a moving map (HyperFrames segment with a camera_path or a "
                                          "camera preset other than static)", 0))
    ev = [f for s in tl.segments for f, _ in element_events(s, fps)]
    if ev:
        grade("first graphic", min(ev) / fps, g.graphic_warn_s, g.graphic_fail_s)
    else:
        out.append(Finding("H01", "fail", "no pin, card or drawn overlay anywhere: the hook has no graphic"))
    return out


def c01_cuts(tl: Timeline, g: profiles.CutGate) -> list[Finding]:
    """FAIL levels on shot changes (every boundary), WARN levels on hard cuts (boundaries without a transition)."""
    fps, cuts, shots = tl.settings.fps, hard_cuts(tl), shot_changes(tl)
    dur = tl.total_frames / fps
    name = "hard cuts" if len(cuts) == len(shots) else f"shot changes ({len(cuts)} hard cuts)"
    rate, cut_rate = len(shots) / (dur / 10), len(cuts) / (dur / 10)
    out = []
    lv = "fail" if rate > g.fail_per_10s + 1e-9 else "warn" if cut_rate > g.warn_per_10s + 1e-9 else "pass"
    shown = cut_rate if lv == "warn" and len(cuts) != len(shots) else rate
    lim = g.fail_per_10s if lv == "fail" else g.warn_per_10s
    out.append(Finding("C01", lv, f"{len(shots)} {name} in {dur:.1f} s = {shown:.2f} per 10 s "
                                  f"({'>' if lv != 'pass' else '<='} {lim})"))
    if shots:
        t, tc = shots[0] / fps, (cuts[0] / fps if cuts else math.inf)
        lv = "fail" if t < g.first_fail_s - 1e-9 else "warn" if tc < g.first_warn_s - 1e-9 else "pass"
        what, f = ("hard cut", cuts[0]) if lv == "warn" or (cuts and cuts[0] == shots[0]) else ("shot change", shots[0])
        lim = g.first_fail_s if lv == "fail" else g.first_warn_s
        seg = next(s.seg_index for s in tl.segments if s.start_frame == f)
        out.append(Finding("C01", lv, f"first {what} at {f / fps:.2f} s ({'<' if lv != 'pass' else '>='} {lim} s)",
                           seg))
    return out


def transition_findings(tl: Timeline) -> list[Finding]:
    """C01 FAIL for every [transitions] window that leaves its two segments or overlaps the next one."""
    return [Finding("C01", "fail", f"transition into segment {i}: {m}", i) for i, m in transitions.fit_problems(tl)]


def motion_frames(tl: Timeline, project_dir: Path) -> list[tuple[float, float] | None]:
    """Per output frame: (|zoom speed| ln scale / s, 2D frame-centre speed in frame widths / s) of the planned camera,
    or None where the camera is not planned (native footage without camera_move). Transition frames are (inf, inf)."""
    import hypoverum_core_rs as core

    from .. import native
    from ..layout import camera_at
    from . import layer2
    fps, W, H = tl.settings.fps, tl.settings.width, tl.settings.height
    out: list[tuple[float, float] | None] = [None] * tl.total_frames
    for s in tl.segments:
        n = s.n_frames
        if s.engine_type == HYPERFRAMES:
            if not (project_dir / s.gsap_scene_config.map_asset).is_file():
                continue
            if s.gsap_scene_config.globe:
                from .. import globe
                G = hyperframes.scene_data(s, tl, project_dir)["globe"]
                for f, m in enumerate(globe.motion(G, n, fps, W, H)):
                    out[s.start_frame + f] = m
                continue
            cam = layer2._hf_plan(s, tl, project_dir)
            # (map point at the frame centre, scale) per frame; centre speed in output px = map px x scale
            v = [((W / 2 - c["x"]) / c["s"], (H / 2 - c["y"]) / c["s"], c["s"], c.get("t", 0.0))
                 for c in (camera_at(cam, f) for f in range(n + 1))]
            for f in range(n):   # a tilt change moves the frame edge by about half the frame height x the angle (rad)
                (xa, ya, sa, ta), (xb, yb, sb, tb) = v[f], v[f + 1]
                pan = math.hypot(xb - xa, yb - ya) * (sa + sb) / 2 * fps / W
                out[s.start_frame + f] = (abs(math.log(sb / sa)) * fps,
                                          max(pan, abs(math.radians(tb - ta)) * fps * H / 2 / W))
        elif s.camera_move is not None and (project_dir / s.source).is_file():
            job = native.plan(s, tl, project_dir)
            wins = list(core.camera_windows(job.start_win, job.end_win, n))
            wins.append(wins[-1])
            for f in range(n):   # window (x, y, w, h) in source px; the window width maps to the frame width
                a, b = wins[f], wins[f + 1]
                out[s.start_frame + f] = (abs(math.log(a[2] / b[2])) * fps,
                                          math.hypot((b[0] + b[2] / 2) - (a[0] + a[2] / 2), (b[1] + b[3] / 2) - (a[1] + a[3] / 2))
                                          / ((a[2] + b[2]) / 2) * fps)
    for w in transitions.windows(tl):
        for f in range(max(0, w.f0), min(tl.total_frames, w.f1)):
            out[f] = (math.inf, math.inf)
    return out


def p01_camera(tl: Timeline, project_dir: Path, g: profiles.CameraGate) -> list[Finding]:
    """Moving fraction of the planned frames and the longest hold (consecutive still frames; an unplanned frame
    ends a hold). Still = zoom speed < still_zoom_ln_s and centre speed < still_pan_w_s."""
    fps = tl.settings.fps
    mf = motion_frames(tl, project_dir)
    still = [None if m is None else (m[0] < g.still_zoom_ln_s and m[1] < g.still_pan_w_s) for m in mf]
    planned = [x for x in still if x is not None]
    if not planned:
        return [Finding("P01", "skip", "no planned camera frames")]
    moving = 1 - sum(planned) / len(planned)
    holds, run0 = [], None
    for f, x in enumerate(still + [None]):
        if x:
            run0 = f if run0 is None else run0
        elif run0 is not None:
            holds.append((f - run0, run0, f))
            run0 = None
    out = []
    for n, a, b in holds:
        t = n / fps
        if t > g.hold_warn_s + 1e-9:
            lv = "fail" if t > g.hold_fail_s + 1e-9 else "warn"
            out.append(Finding("P01", lv, f"camera holds {t:.1f} s (frames {a}-{b}; max "
                                          f"{g.hold_fail_s if lv == 'fail' else g.hold_warn_s} s)", _seg_at(tl, a),
                               frames=(a, b)))
    longest = max((n for n, _, _ in holds), default=0) / fps
    lv = "warn" if moving < g.moving_warn - 1e-9 else "pass"
    out.append(Finding("P01", lv, f"camera moving {moving:.0%} of {len(planned)} planned frames "
                                  f"({'<' if lv == 'warn' else '>='} {g.moving_warn:.0%}); longest hold {longest:.1f} s"))
    return out


def p02_drift(tl: Timeline, g: profiles.CameraGate) -> list[Finding]:
    """P02: the camera never waits still: the drift between moves (theme camera_drift_per_s, ln zoom / s) is at
    least min_drift_per_s and above still_zoom_ln_s, so a wait counts as P01 motion. No finding without
    min_drift_per_s."""
    if g.min_drift_per_s is None:
        return []
    rate = profiles.of(tl).theme.camera_drift_per_s
    floor = max(g.min_drift_per_s, g.still_zoom_ln_s)
    if rate + 1e-9 < floor:
        return [Finding("P02", "fail", f"camera drift {rate:g} ln/s below {floor:g} ln/s: waits are still")]
    return [Finding("P02", "pass", f"camera drift {rate:g} ln/s >= {floor:g} ln/s")]


def v01_visual_type(tl: Timeline) -> list[Finding]:
    out = []
    for a, b in zip(tl.segments, tl.segments[1:]):
        if a.kind != b.kind:
            continue
        same_map = (a.engine_type == HYPERFRAMES and b.engine_type == HYPERFRAMES
                    and a.gsap_scene_config.map_asset == b.gsap_scene_config.map_asset)
        if same_map:
            out.append(Finding("V01", "fail", f"{_change(tl, b)} between two segments of the same map "
                                              f"{a.gsap_scene_config.map_asset}: one map segment moves the camera", b.seg_index))
        else:
            out.append(Finding("V01", "warn", f"{_change(tl, b)} joins two '{a.kind}' segments; change shots only "
                                              "where the visual type changes (set visual_type when the inference is "
                                              "wrong)", b.seg_index))
    return out or [Finding("V01", "pass", f"every {'shot change' if transitions.windows(tl) else 'hard cut'} "
                                          "changes the visual type")]


def _change(tl: Timeline, b: Segment) -> str:
    """'hard cut' or the transition name of the boundary into segment b."""
    return "hard cut" if b.seg_index == 0 else {"cut": "hard cut", "crossfade": "crossfade",
                                                 "zoom_whip": "zoom whip", "page_turn": "page turn",
                                                 "state": "state dissolve"}[transitions.kind_at(tl, b.seg_index)]


def n01_novelty(tl: Timeline, g: profiles.NoveltyGate) -> list[Finding]:
    fps = tl.settings.fps
    ev = sorted({0, tl.total_frames, *shot_changes(tl), *(f for s in tl.segments for f, _ in element_events(s, fps)),
                 *(f for s in tl.segments for f in camera_events(s))})
    gaps = [(b - a, a, b) for a, b in zip(ev, ev[1:]) if (b - a) / fps > g.max_gap_s + 1e-9]
    out = [Finding("N01", "warn", f"nothing new on screen for {n / fps:.1f} s (frames {a}-{b}; max {g.max_gap_s} s): "
                                  "add a pin, card, overlay, camera key or shot", _seg_at(tl, a), frames=(a, b))
           for n, a, b in gaps]
    return out or [Finding("N01", "pass", f"something new at least every {g.max_gap_s} s")]


def _seg_at(tl: Timeline, f: int) -> int | None:
    return next((s.seg_index for s in tl.segments if s.start_frame <= f < s.end_frame), None)


def _tokens(texts: list[str], min_chars: int) -> set[str]:
    return {w for t in texts for w in re.sub(r"[^\w\s]", " ", t.lower()).split() if len(w) >= min_chars}


def k01_keyword_return(tl: Timeline, words: list[CaptionWord] | None, g: profiles.KeywordReturnGate) -> list[Finding]:
    fps, n = tl.settings.fps, tl.total_frames
    h, e = round(g.hook_s * fps), max(0, n - round(g.end_s * fps))
    ws = words or []
    hook = _tokens(screen_text(tl, 0, h) + [w.word for w in ws if w.start_frame < h], g.min_chars)
    end = _tokens(screen_text(tl, e, n), g.min_chars)
    if not hook:
        return [Finding("K01", "warn", f"no hook keyword (on-screen text or caption word of >= {g.min_chars} letters) "
                                       f"in the first {g.hook_s} s")]
    both = sorted(hook & end)
    if not both:
        return [Finding("K01", "warn", f"no hook keyword ({', '.join(sorted(hook)[:6])}) on screen in the last "
                                       f"{g.end_s} s")]
    return [Finding("K01", "pass", f"hook keyword back on screen at the end: {', '.join(both[:3])}")]


def d01_duration(tl: Timeline, g: profiles.DurationGate) -> list[Finding]:
    d = tl.total_frames / tl.settings.fps
    if g.min_s <= d <= g.max_s:
        return [Finding("D01", "pass", f"{d:.1f} s inside {g.min_s:g}-{g.max_s:g} s")]
    return [Finding("D01", "warn", f"duration {d:.1f} s outside {g.min_s:g}-{g.max_s:g} s")]


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def c02_cta(tl: Timeline, words: list[CaptionWord] | None, g: profiles.CtaGate) -> list[Finding]:
    """C02: no call-to-action or end-card phrase in on-screen text or captions."""
    seen = " ".join(screen_text(tl, 0, tl.total_frames) + [w.word for w in words or []])
    have = _words(seen)
    hits = []
    for phrase in g.phrases:
        p = _words(phrase)
        if p and any(have[i:i + len(p)] == p for i in range(len(have) - len(p) + 1)):
            hits.append(phrase)
    if hits:
        return [Finding("C02", "fail", f"call to action / end card phrase(s) on screen: {', '.join(hits)}")]
    return [Finding("C02", "pass", f"no CTA or end-card phrase ({len(g.phrases)} checked)")]


def k02_ending_keyword(tl: Timeline, words: list[CaptionWord] | None, g: profiles.EndingKeywordGate) -> list[Finding]:
    """K02: a hook keyword (first hook_s, on screen or caption) is still visible on the final frame."""
    fps, n = tl.settings.fps, tl.total_frames
    h = round(g.hook_s * fps)
    hook = _tokens(screen_text(tl, 0, h) + [w.word for w in words or [] if w.start_frame < h], g.min_chars)
    if not hook:
        return [Finding("K02", "warn", f"no hook keyword of >= {g.min_chars} letters in the first {g.hook_s:g} s")]
    end = _tokens(screen_text(tl, n - 1, n), g.min_chars)
    both = sorted(hook & end)
    if not both:
        return [Finding("K02", "fail", f"hook keyword ({', '.join(sorted(hook)[:6])}) is not on screen on the final "
                                       f"frame")]
    return [Finding("K02", "pass", f"hook keyword still on screen at the end: {', '.join(both[:3])}")]


def d02_duration_exact(tl: Timeline, g: profiles.ExactDurationGate) -> list[Finding]:
    d = tl.total_frames / tl.settings.fps
    if abs(d - g.target_s) <= g.tolerance_s + 1e-9:
        return [Finding("D02", "pass", f"{d:.2f} s within {g.target_s:g} +/- {g.tolerance_s:g} s")]
    return [Finding("D02", "fail", f"duration {d:.2f} s outside {g.target_s:g} +/- {g.tolerance_s:g} s")]


def run_style(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None,
              built_dirs: list[Path] | None = None) -> list[Finding]:
    g = profiles.of(tl).gates
    out: list[Finding] = []
    if g.hook:
        out += h01_hook(tl, project_dir, words, g.hook)
    out += transition_findings(tl)
    if g.cuts:
        out += c01_cuts(tl, g.cuts)
    if g.camera:
        out += p01_camera(tl, project_dir, g.camera)
        out += p02_drift(tl, g.camera)
    if g.visual_type:
        out += v01_visual_type(tl)
    if g.novelty:
        out += n01_novelty(tl, g.novelty)
    if g.keyword_return:
        out += k01_keyword_return(tl, words, g.keyword_return)
    if g.ending_keyword:
        out += k02_ending_keyword(tl, words, g.ending_keyword)
    if g.cta:
        out += c02_cta(tl, words, g.cta)
    if g.duration:
        out += d01_duration(tl, g.duration)
    if g.duration_exact:
        out += d02_duration_exact(tl, g.duration_exact)
    from . import anti_slop
    out += anti_slop.run(tl, project_dir, words, g.anti_slop, built_dirs=built_dirs)
    return out


def silence_gaps(path: Path, threshold_db: float, min_s: float) -> list[tuple[float, float]]:
    """(start_s, duration_s) of every gap below threshold_db lasting longer than min_s (ffmpeg silencedetect)."""
    import subprocess
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        f"silencedetect=noise={threshold_db}dB:d={min_s}", "-f", "null", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-500:]
        raise EngineError(f"S01 silencedetect failed ({r.returncode}) on {path}: {tail}")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    durs = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr)]
    end = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if len(starts) > len(durs) and end:   # a gap running to the end of the file has no silence_end line
        h, m, s = end.groups()
        durs.append(int(h) * 3600 + int(m) * 60 + float(s) - starts[-1])
    return list(zip(starts, durs))


def silence_findings(path: Path, tl: Timeline) -> list[Finding]:
    """S01 on the final file: no gap below threshold_db longer than max_gap_s."""
    g = profiles.of(tl).gates.silence
    if g is None:
        return []
    gaps = silence_gaps(path, g.threshold_db, g.max_gap_s)
    out = [Finding("S01", "fail", f"silent gap {d:.2f} s at {a:.2f} s (< {g.threshold_db:g} dB, max {g.max_gap_s} s)")
           for a, d in gaps]
    return out or [Finding("S01", "pass", f"no gap below {g.threshold_db:g} dB longer than {g.max_gap_s} s")]
