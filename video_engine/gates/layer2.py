"""Layer 2 gates. Math: M01 source length (and no HDR source), M02 map resolution at the deepest zoom, M04 globe texture resolution, G01 grade LUTs, R56 camera motion with automatic intensity raise, R62 caption safe margins.
DOM: R63 HyperFrames element margins and collisions at 3 seek times (plus camera_path key frames) per segment
(headless Chrome, dynamic port); E01 entity colours as painted."""
from __future__ import annotations

import dataclasses
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hypoverum_core_rs as core

from .. import captions, entities, hyperframes, legibility, maps, native, profiles, props, watermark
from ..timeline import HYPERFRAMES, NATIVE, CaptionWord, Segment, Timeline
from ..util import EngineError, decode_colour, video_info
from .core import Finding

R56_MIN_PX_1080P = 100.0     # minimum camera motion over a segment, output px, for a 1080x1920 frame
INTENSITY_MAX = 6.0
INTENSITY_STEP = 0.05
R62_MIN_FONT_FRAC = 0.6      # caption font may shrink to this fraction of the style size to fit the safe width
SAFE_X = 60                  # px at 1080 wide; scaled with width
SAFE_TOP = 100               # px at 1920 high; scaled with height
SAFE_BOTTOM = 100
CAPTION_BAND_HALF = 0.75     # caption box half-height as a fraction of font size
CAPTION_REACH_BAND = 24.0    # px of outline/shadow reach below the glyphs the band already holds (caption_pad)
PLACE_STEP = 10              # px: vertical search step of automatic caption placement
OVERLAP_TOL = 2              # px
M02_WARN = 1.2               # map source detail px per on-screen px at the deepest zoom: warn below
M02_FAIL = 0.85              # fail below (shown more than ~1.18x upscaled)


def r56_min_px(W: int, H: int) -> float:
    return R56_MIN_PX_1080P * math.hypot(W, H) / math.hypot(1080, 1920)


def m01_source_length(tl: Timeline, project_dir: Path) -> list[Finding]:
    out = []
    fps = tl.settings.fps
    for s in tl.segments:
        if s.engine_type != NATIVE or s.is_still or not s.source:
            continue
        src = project_dir / s.source
        if not src.is_file():
            continue
        try:
            vi = video_info(src)
        except EngineError as e:
            out.append(Finding("M01", "fail", f"cannot probe {s.source}: {e}", s.seg_index))
            continue
        try:
            decode_colour(src)   # an HDR (PQ/HLG) source cannot be decoded as SDR BT.709
        except EngineError as e:
            out.append(Finding("M01", "fail", str(e), s.seg_index))
            continue
        have = math.floor(vi["duration"] * fps + 1e-6)
        need = s.source_in_frame + s.n_frames
        if need > have:
            out.append(Finding("M01", "fail", f"{s.source} has {have} frames at {fps} fps; segment needs "
                               f"source_in_frame {s.source_in_frame} + {s.n_frames} = {need}", s.seg_index))
    return out or [Finding("M01", "pass", "all footage sources long enough")]


def m02_map_resolution(tl: Timeline, project_dir: Path) -> list[Finding]:
    """Texels per screen px at the deepest camera zoom of each HyperFrames segment (keys, pulses, drift and the tilt
    magnification included): real source detail (pixel width, or the effective width of a 2x pre-upscaled raster) /
    on-screen map width; warn below M02_WARN, fail below M02_FAIL. The derivative and the deep raster are sized to the
    on-screen width on purpose (1 texel per px when the source allows), so they count only when they are cut below
    both that width and the source (a DEEP_MAX_SIDE cap): then the drawn raster is the limit."""
    out = []
    units = [u for s in tl.segments if s.engine_type == HYPERFRAMES for u in hyperframes.split_panels(s, tl)]
    for s, tlu in units:   # a split segment: each panel at its own size
        if s.gsap_scene_config.globe:
            continue   # globe scenes: M04
        sc = s.gsap_scene_config
        try:
            scene = hyperframes.scene_data(s, tlu, project_dir)
            iw, ih = maps.map_image_size(project_dir / sc.map_asset)
            ew = maps.effective_width(project_dir / sc.map_asset)
        except (EngineError, OSError) as e:
            out.append(Finding("M02", "fail", f"map '{sc.map_asset}': {e}", s.seg_index))
            continue
        need = maps._need_w(scene)
        detail = min(ew, iw)
        drawn = maps.drawn_width(scene, (iw, ih))
        capped = drawn < min(detail, math.floor(need))
        r = (min(drawn, detail) if capped else detail) / need
        via = f", drawn from a {drawn} px raster" if capped else ""
        if ew < iw:
            via += f" (effective detail ~{ew} px)"
        msg = (f"map '{sc.map_asset}' {iw} px wide{via}, shown up to {need:.0f} px wide at zoom "
               f"{need / scene['map_size'][0]:.2f} ({r:.2f} texels per screen px)")
        if r < M02_FAIL:
            out.append(Finding("M02", "fail", msg + f"; needs >= {M02_FAIL:g}x (a larger source or a lower zoom)", s.seg_index))
        elif r < M02_WARN:
            out.append(Finding("M02", "warn", msg + f"; below the {M02_WARN:g}x sharpness headroom", s.seg_index))
    return out or [Finding("M02", "pass", "every map source covers its deepest zoom")]


def m03_tilt_cover(tl: Timeline, project_dir: Path) -> list[Finding]:
    """Tilted HyperFrames cameras: every screen corner of every frame shows map (camera3d.uncovered); fail otherwise."""
    from .. import camera3d
    out, n = [], 0
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES or not any(k.tilt for k in s.gsap_scene_config.camera_path or []):
            continue
        n += 1
        try:
            bad = camera3d.uncovered(hyperframes.scene_data(s, tl, project_dir))
        except (EngineError, OSError) as e:
            out.append(Finding("M03", "fail", f"tilted camera: {e}", s.seg_index))
            continue
        if bad:
            f, corners = bad[0]
            out.append(Finding("M03", "fail", f"tilted camera shows no map at the {', '.join(corners)} corner(s) on "
                               f"{len(bad)} frames (first: frame {f}); zoom in, move the focus off the map edge or "
                               "tilt less", s.seg_index))
    return out or [Finding("M03", "pass", f"{n} tilted segments, map under every corner" if n else "no tilted camera")]


def m04_globe_resolution(tl: Timeline, project_dir: Path) -> list[Finding]:
    """Globe scenes: lowest over frames of texels per degree drawn at the view centre (finest detail patch there, else
    the whole-globe texture) / screen px per degree: warn below M02_WARN, fail below M02_FAIL."""
    from .. import globe
    out, n = [], 0
    fps, W, H = tl.settings.fps, tl.settings.width, tl.settings.height
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES or not s.gsap_scene_config.globe:
            continue
        n += 1
        try:
            G = hyperframes.scene_data(s, tl, project_dir)["globe"]
        except (EngineError, OSError) as e:
            out.append(Finding("M04", "fail", f"globe: {e}", s.seg_index))
            continue
        r, f, need, have = min((globe.have_at(G, v, W, H) / (nd := globe.need_px_per_deg(v, H)), f, nd, globe.have_at(G, v, W, H))
                               for f in range(s.n_frames + 1) for v in [globe.camera_at(G, f, fps)])
        msg = f"globe at frame {f} shown at {need:.1f} px/deg, source {have:.1f} px/deg ({r:.2f}x)"
        if r < M02_FAIL:
            out.append(Finding("M04", "fail", msg + f"; needs >= {M02_FAIL:g}x (detail_tiles or a higher altitude)", s.seg_index))
        elif r < M02_WARN:
            out.append(Finding("M04", "warn", msg + f"; upscaled below {M02_WARN:g}x", s.seg_index))
    return out or [Finding("M04", "pass", f"{n} globe segments, source covers every view" if n else "no globe")]


def g01_grade_luts(tl: Timeline, project_dir: Path) -> list[Finding]:
    """Every .cube LUT a grade reads (segment, beat or profile [grade]) exists and parses as a 3D LUT; fail otherwise."""
    from .. import grade
    out = [Finding("G01", "fail", e, s.seg_index) for s in tl.segments for e in grade.check(s, tl, project_dir)]
    n = sum(1 for s in tl.segments if grade.ranges(s, tl, project_dir))
    return out or [Finding("G01", "pass", f"{n} graded segments, every grade LUT readable" if n else "no grades")]


def _native_motion(s: Segment, tl: Timeline, sw: int, sh: int, intensity: float) -> float:
    cm = s.camera_move
    return core.camera_plan(cm.preset, intensity, float(sw), float(sh), float(tl.settings.width),
                            float(tl.settings.height), cm.focus_box)[2]


def _hf_motion(s: Segment, tl: Timeline, project_dir: Path, intensity: float) -> float:
    sc = s.gsap_scene_config.model_copy(update={"camera_intensity": intensity})
    W, H = tl.settings.width, tl.settings.height
    iw, ih = maps.map_image_size(project_dir / sc.map_asset)
    cover = max(W / iw, H / ih)
    return hyperframes.camera_states(sc, W, H, iw * cover, ih * cover)[2]


def _hf_plan(s: Segment, tl: Timeline, project_dir: Path) -> dict:
    W, H = tl.settings.width, tl.settings.height
    iw, ih = maps.map_image_size(project_dir / s.gsap_scene_config.map_asset)
    cover = max(W / iw, H / ih)
    return hyperframes.camera_plan(s.gsap_scene_config, W, H, iw * cover, ih * cover, s.n_frames, tl.settings.fps,
                                   profiles.of(tl).render_values("hf"))


def _r56_path(s: Segment, tl: Timeline, project_dir: Path, target: float) -> list[Finding]:
    """R56 on a camera_path: every key-to-key leg moves at least the minimum (no automatic fix)."""
    keys = s.gsap_scene_config.camera_path
    if keys[-1].at_frame >= s.n_frames:
        return []   # T02 reports it
    motion = _hf_plan(s, tl, project_dir)["leg_motion"]
    bad = [Finding("R56", "fail", f"camera_path leg {i + 1} (frames {keys[i].at_frame}-{keys[i + 1].at_frame}) moves "
                   f"{m:.0f}px < {target:.0f}px; move the key focus or zoom further", s.seg_index)
           for i, m in enumerate(motion) if m < target]
    return bad or [Finding("R56", "pass", f"camera_path: {len(motion)} legs, each >= {target:.0f}px "
                                          f"(min {min(motion):.0f}px)", s.seg_index)]


def _r56_globe(s: Segment, tl: Timeline, project_dir: Path, target: float) -> list[Finding]:
    """R56 on a globe: every key-to-key leg moves at least the minimum; one key: the drift zoom over the segment."""
    from .. import globe
    W, H = tl.settings.width, tl.settings.height
    G = hyperframes.scene_data(s, tl, project_dir)["globe"]
    motion = globe.key_motion(G, W, H)
    if not motion:
        a, b = globe.camera_at(G, 0, tl.settings.fps), globe.camera_at(G, s.n_frames, tl.settings.fps)
        m = abs(math.log(a["alt"] / b["alt"])) * math.hypot(W, H) / 2
        return [Finding("R56", "pass" if m >= target else "fail", f"globe drift {m:.0f}px "
                        f"{'>=' if m >= target else '<'} {target:.0f}px", s.seg_index)]
    bad = [Finding("R56", "fail", f"globe leg {i + 1} moves {m:.0f}px < {target:.0f}px; move or zoom further", s.seg_index)
           for i, m in enumerate(motion) if m < target]
    return bad or [Finding("R56", "pass", f"globe: {len(motion)} legs, each >= {target:.0f}px (min {min(motion):.0f}px)", s.seg_index)]


def _raise_intensity(motion_at, i0: float, target: float) -> tuple[float, float] | None:
    i = i0
    while i <= INTENSITY_MAX + 1e-9:
        m = motion_at(i)
        if m >= target:
            return round(i, 2), m
        i += INTENSITY_STEP
    return None


def r56_camera(tl: Timeline, project_dir: Path) -> tuple[list[Finding], list[dict]]:
    """Fails a camera move whose start/end delta is under the minimum; raises intensity in memory when allowed."""
    out, fixes = [], []
    W, H = tl.settings.width, tl.settings.height
    target = r56_min_px(W, H)
    for s in tl.segments:
        if s.engine_type == NATIVE:
            cm = s.camera_move
            if cm is None or not (project_dir / s.source).is_file():
                continue   # footage without a camera move: motion is measured on pixels (qa.py)
            if cm.preset == "static":
                if s.is_still:
                    out.append(Finding("R56", "fail", "still image with preset 'static' has no motion", s.seg_index))
                continue
            sw, sh = native.source_size(project_dir / s.source, s.is_still)
            motion_at = lambda i, s=s, sw=sw, sh=sh: _native_motion(s, tl, sw, sh, i)
            cur, auto, field = cm.intensity, cm.auto_intensity, "camera_move.intensity"
        elif s.engine_type == HYPERFRAMES:
            sc = s.gsap_scene_config
            if not (project_dir / sc.map_asset).is_file() or sc.camera_preset not in hyperframes.HF_PRESETS:
                continue
            if sc.globe:
                out += _r56_globe(s, tl, project_dir, target)
                continue
            if sc.split is not None:   # each panel's own camera_path, at the panel size
                for ps, pt in hyperframes.split_panels(s, tl):
                    out += _r56_path(ps, pt, project_dir, r56_min_px(pt.settings.width, pt.settings.height))
                continue
            if sc.camera_path:
                out += _r56_path(s, tl, project_dir, target)
                continue
            if sc.camera_preset == "static":
                out.append(Finding("R56", "fail", "HyperFrames map with camera_preset 'static' has no motion", s.seg_index))
                continue
            motion_at = lambda i, s=s: _hf_motion(s, tl, project_dir, i)
            cur, auto, field = sc.camera_intensity, True, "gsap_scene_config.camera_intensity"
        else:
            continue
        m = motion_at(cur)
        if m >= target:
            out.append(Finding("R56", "pass", f"motion {m:.0f}px >= {target:.0f}px at intensity {cur}", s.seg_index))
            continue
        if not auto:
            out.append(Finding("R56", "fail", f"motion {m:.0f}px < {target:.0f}px at intensity {cur}; auto_intensity is false", s.seg_index))
            continue
        r = _raise_intensity(motion_at, cur, target)
        if r is None:
            out.append(Finding("R56", "fail", f"motion {m:.0f}px < {target:.0f}px even at intensity {INTENSITY_MAX}; "
                               "use another preset, focus or source", s.seg_index))
            continue
        new_i, new_m = r
        if s.engine_type == NATIVE:
            s.camera_move.intensity = new_i
        else:
            s.gsap_scene_config.camera_intensity = new_i
        fixes.append({"seg": s.seg_index, "path": field, "from": cur, "to": new_i})
        out.append(Finding("R56", "fix", f"motion {m:.0f}px < {target:.0f}px: {field} {cur} -> {new_i} ({new_m:.0f}px)", s.seg_index))
    return out, fixes


def caption_events(tl: Timeline, words: list[CaptionWord], y_overrides: list | None = None) -> list[tuple]:
    return captions.compile_ass(words, tl, y_overrides)[1] if words else []


def r62_caption_margins(tl: Timeline, words: list[CaptionWord]) -> tuple[list[Finding], list[tuple]]:
    W, H = tl.settings.width, tl.settings.height
    st = profiles.caption_style(tl)
    try:
        events = caption_events(tl, words)
    except ValueError as e:
        return [Finding("L22", "fail", f"caption_style invalid: {e}")], []
    size = float(st.get("size", 84))
    margin_x = float(st.get("margin_x", 90))
    safe_top, safe_bottom = SAFE_TOP * H / 1920, H - SAFE_BOTTOM * H / 1920
    out = []
    for (a, b, text, fs, est_w, x, y) in events:
        if est_w > W - 2 * margin_x + 1:
            out.append(Finding("R62", "fail", f"caption '{text}' frames {a}-{b} est width {est_w:.0f}px > safe width {W - 2 * margin_x:.0f}px"))
        if fs < size * R62_MIN_FONT_FRAC:
            out.append(Finding("R62", "fail", f"caption '{text}' frames {a}-{b} shrunk to {fs:.0f}px (< {R62_MIN_FONT_FRAC:.0%} of {size:.0f}); "
                               "lower max_chars or shorten the text"))
        half = fs * CAPTION_BAND_HALF
        if y - half < safe_top or y + half > safe_bottom:
            out.append(Finding("R62", "fail", f"caption '{text}' frames {a}-{b} centre y={y:.0f} outside the vertical safe area"))
    return out or [Finding("R62", "pass", f"{len(events)} captions inside safe margins")], events


def _rect_overlap(a: dict, b: dict) -> bool:
    return (a["x"] + OVERLAP_TOL < b["x"] + b["w"] and b["x"] + OVERLAP_TOL < a["x"] + a["w"]
            and a["y"] + OVERLAP_TOL < b["y"] + b["h"] and b["y"] + OVERLAP_TOL < a["y"] + a["h"])


def visible(rects: list[dict]) -> list[dict]:
    """Rects on screen: opacity > 0.05, non-empty, not flying (`fly`, set by `mark_flying`)."""
    return [r for r in rects if r.get("opacity", 1) > 0.05 and r["w"] > 0 and r["h"] > 0 and not r.get("fly")]


FLY_IDS = {"label": "pin", "area": "area", "title": "title", "fig": "figure"}   # layout span key -> DOM id prefix


def flying(scene: dict, t: float) -> set[str]:
    """DOM ids of the fly-through texts (scene `layout.fly`) scaled away from 1 at time t: inside an entry
    [t_in, t_in + d_in) or an exit [t_out, t_out + d_out) of any of the text's spans. Empty without `layout.fly`."""
    ly = scene.get("layout") or {}
    if not ly.get("fly"):
        return set()
    out = set()
    for key, spans in (ly.get("els") or {}).items():
        kind, _, rest = key.partition(":")
        if kind not in FLY_IDS:
            continue
        rid = "title" if kind == "title" else f"{FLY_IDS[kind]}:{rest}"
        for (t_in, d_in, t_out, d_out) in spans:
            if t_in - 1e-4 <= t < t_in + d_in - 1e-4 or (t_out is not None and t_out - 1e-4 <= t < t_out + d_out - 1e-4):
                out.add(rid)
    return out


def mark_flying(res: dict, scene: dict) -> None:
    """Sets `fly` on the probed rects of texts in flight: a fly-through text crosses the frame by design, so the
    element checks (R63 safe area and overlaps, caption clearance, B01) read texts at scale 1 only."""
    for item in {id(i): i for i in res.get("times", []) + res.get("all_times", [])}.values():
        ids = flying(scene, item["t"])
        for r in item["rects"]:
            if r["id"] in ids:
                r["fly"] = True


def check_rects(rects: list[dict], W: int, H: int, t: float, seg: int) -> list[Finding]:
    """R63 element checks on one seek time: inside the safe area, no pairwise overlap.
    rects: {id, text, x, y, w, h, opacity}; elements with opacity <= 0.05 are ignored."""
    out = []
    sx, st, sb = SAFE_X * W / 1080, SAFE_TOP * H / 1920, H - SAFE_BOTTOM * H / 1920
    vis = visible(rects)
    for r in vis:
        if r["x"] < sx or r["x"] + r["w"] > W - sx or r["y"] < st or r["y"] + r["h"] > sb:
            out.append(Finding("R63", "fail", f"t={t:.2f}s {r['id']} '{r.get('text', '')[:30]}' at "
                               f"({r['x']:.0f},{r['y']:.0f},{r['w']:.0f}x{r['h']:.0f}) outside safe area", seg))
    for i in range(len(vis)):
        for j in range(i + 1, len(vis)):
            if _rect_overlap(vis[i], vis[j]):
                out.append(Finding("R63", "warn", f"t={t:.2f}s {vis[i]['id']} overlaps {vis[j]['id']}", seg))
    return out


_R63_TIME_RE = re.compile(r"^t=(\d+(?:\.\d+)?)s\s+([^\s]+)\s+(.*)$")


def dedupe_r63(findings: list[Finding]) -> list[Finding]:
    """Collapse repeated R63 element findings across seek times of the same (seg, element id, kind) into one finding
    reporting the time span `t=a-b s`."""
    grouped: dict[tuple, list[tuple[float, Finding, str]]] = {}
    order: list[tuple | Finding] = []
    for f in findings:
        if f.gate != "R63" or f.level not in ("fail", "warn"):
            order.append(f)
            continue
        m = _R63_TIME_RE.match(f.message)
        if not m:
            order.append(f)
            continue
        t_val, eid, rest = float(m.group(1)), m.group(2), m.group(3)
        kind = "outside safe area" if rest.endswith("outside safe area") else rest
        key = (f.seg, f.level, eid, kind)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((t_val, f, rest))
    out: list[Finding] = []
    for item in order:
        if isinstance(item, Finding):
            out.append(item)
            continue
        entries = grouped[item]
        if len(entries) == 1:
            out.append(entries[0][1])
            continue
        t0, t1 = min(e[0] for e in entries), max(e[0] for e in entries)
        _, first_f, first_rest = entries[0]
        eid = item[2]
        span = f"t={t0:.2f}s" if abs(t1 - t0) < 1e-4 else f"t={t0:.2f}-{t1:.2f}s"
        out.append(Finding("R63", first_f.level, f"{span} {eid} {first_rest}", first_f.seg))
    return out


def probe_times(s: Segment, fps: int) -> list[float]:
    """Seek times of the DOM probe: 30 %, 60 % and the last frame, plus every camera_path key frame, plus for each prop
    its first settled frame (after the enter), its middle frame and its last frame before the exit."""
    n = s.n_frames
    frames = {round(n * 0.3), round(n * 0.6), n - 1}
    if s.engine_type == HYPERFRAMES:
        sc = s.gsap_scene_config
        frames |= {min(n - 1, k.at_frame) for k in sc.camera_path or []}
        for _, p, f_in, f_out in props.flat(s):
            a = f_in + (max(1, core.sec_to_frame(p.enter.s, fps)) if p.enter else 0)
            b = f_out - 1 - (max(1, core.sec_to_frame(p.exit.s, fps)) if p.exit else 0)
            frames |= {min(n - 1, max(0, f)) for f in (a, (f_in + f_out) // 2, b)}
        for c in sc.flag_characters or []:
            frames |= {min(n - 1, max(0, f)) for f in (c.at_frame + 5, (c.at_frame + (c.out_frame or n)) // 2,
                                                       (c.out_frame or n) - 1)}
        if sc.cutaway:
            frames.add(min(n - 1, sc.cutaway.at_frame + 5))
        for ins in sc.insets or []:
            frames |= {min(n - 1, max(0, f)) for f in (ins.at_frame + 5,
                                                       (ins.at_frame + (ins.out_frame or n)) // 2,
                                                       (ins.out_frame or n) - 1)}
    return [f / fps for f in sorted(frames)]


def caption_box(ev: tuple, pad: float = 0.0) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of a compiled caption event; pad (caption_pad) extends the bottom for a deep shadow."""
    _, _, _, fs, est_w, x, y = ev
    half = fs * CAPTION_BAND_HALF
    return (x - est_w / 2, y - half, x + est_w / 2, y + half + pad)


def caption_pad(tl: Timeline) -> float:
    """Output px the caption ink reaches below the CAPTION_BAND_HALF band. Ink reach below the glyphs: soft shadow
    (layer 0) shadow_dy + its border (outline + 2) + 2 x shadow_blur; hard shadow: outline + shadow. The band absorbs
    CAPTION_REACH_BAND (the default style, 23.5 px); tests render libass ink to check it."""
    st = profiles.caption_style(tl)
    outline, shadow = float(st.get("outline", 5.0)), float(st.get("shadow", 3.0))
    if st.get("soft_shadow", True) and not st.get("box_bg", False):
        dy = st.get("shadow_dy")
        reach = (shadow * 1.5 if dy is None else float(dy)) + outline + 2.0 + 2.0 * float(st.get("shadow_blur", 6.0))
    else:
        reach = outline + max(0.0, shadow)
    return max(0.0, reach - CAPTION_REACH_BAND) * tl.settings.height / 1920


def max_bottom(tl: Timeline) -> float | None:
    """caption_style.max_bottom_y in output px (None = no limit). Raises ValueError on a non-number."""
    m = profiles.caption_style(tl).get("max_bottom_y")
    if m is None:
        return None
    if isinstance(m, bool) or not isinstance(m, (int, float)):
        raise ValueError(f"caption_style.max_bottom_y must be a number, got {m!r}")
    return float(m) * tl.settings.height / 1920


def z01_caption_bottom(tl: Timeline, events: list[tuple]) -> list[Finding]:
    """Z01: every caption box bottom (caption_box + caption_pad: glyphs, descenders, outline and shadow) at or above
    caption_style.max_bottom_y. No finding without the key."""
    try:
        lim = max_bottom(tl)
    except ValueError as e:
        return [Finding("Z01", "fail", str(e))]
    if lim is None:
        return []
    out, pad = [], caption_pad(tl)
    for ev in events:
        b = caption_box(ev, pad)[3]
        if b > lim + 1e-6:
            seg = next((s.seg_index for s in tl.segments if s.start_frame <= ev[0] < s.end_frame), None)
            out.append(Finding("Z01", "fail", f"caption '{ev[2]}' frames {ev[0]}-{ev[1]} at y={ev[6]:.0f}: bottom "
                               f"{b:.0f}px > max_bottom_y {lim:.0f}px", seg))
    return out or [Finding("Z01", "pass", f"{len(events)} caption bottoms <= {lim:.0f}px")]


def _gap(box: tuple, r: dict) -> float:
    """Distance in px between a caption box and an element rect (0 when they intersect)."""
    dx = max(0.0, max(box[0] - (r["x"] + r["w"]), r["x"] - box[2]))
    dy = max(0.0, max(box[1] - (r["y"] + r["h"]), r["y"] - box[3]))
    return (dx * dx + dy * dy) ** 0.5


def seg_events(events: list[tuple], s: Segment) -> list[tuple]:
    return [e for e in events if e[1] > s.start_frame and e[0] < s.end_frame]


def element_union(dom_res: dict) -> list[dict]:
    """All visible element rects over all probed seek times (an element counts wherever it was seen)."""
    return [r for item in dom_res["times"] for r in visible(item["rects"])]


def element_windows(dom_res: dict, s: Segment, fps: int) -> list[dict]:
    """Visible element rects, each held for the absolute frame window `frames` from the previous to the next probed
    seek time (segment start / end at the ends). Uses every probed time (R63 seeks and the R64 sample times), so a
    caption is only checked against the elements on screen, where they are, around its own frames: a pin that
    appears late, or that the camera carries through the caption band, no longer blocks every caption of the
    segment. A probe item without a time falls back to the whole segment (element_union)."""
    items = dom_res.get("all_times") or dom_res["times"]
    if not items or any("t" not in i for i in items):
        return element_union(dom_res)
    items = sorted(items, key=lambda i: i["t"])
    fr = [s.start_frame + round(i["t"] * fps) for i in items]
    out = []
    for k, item in enumerate(items):
        lo = fr[k - 1] if k else s.start_frame
        hi = fr[k + 1] + 1 if k + 1 < len(items) else s.end_frame
        out += [dict(r, frames=(lo, hi)) for r in visible(item["rects"])]
    return out


def focus_rects(s: Segment, tl: Timeline, project_dir: Path) -> list[dict]:
    """camera_move.focus_box projected into output px through the start and end camera windows (clipped to frame)."""
    cm = s.camera_move
    if s.engine_type != NATIVE or cm is None or not cm.focus_box or not (project_dir / s.source).is_file():
        return []
    W, H = tl.settings.width, tl.settings.height
    sw, sh = native.source_size(project_dir / s.source, s.is_still)
    a, b, _ = core.camera_plan(cm.preset, cm.intensity, float(sw), float(sh), float(W), float(H), cm.focus_box)
    y0, x0, y1, x1 = (v / 1000 for v in cm.focus_box)
    out = []
    for k, (wx, wy, ww, wh) in enumerate((a, b)):
        X0, X1 = (x0 * sw - wx) / ww * W, (x1 * sw - wx) / ww * W
        Y0, Y1 = (y0 * sh - wy) / wh * H, (y1 * sh - wy) / wh * H
        X0, X1, Y0, Y1 = max(0, X0), min(W, X1), max(0, Y0), min(H, Y1)
        if X1 > X0 and Y1 > Y0:
            out.append({"id": f"focus:{'start' if k == 0 else 'end'}", "text": "", "x": X0, "y": Y0, "w": X1 - X0,
                        "h": Y1 - Y0, "clear": 0.0})
    return out


def prop_rects(s: Segment, tl: Timeline, project_dir: Path, clear: float) -> list[dict]:
    """Caption avoid boxes of a segment's props (props.avoid_boxes: per frame range, `frames` absolute, end exclusive);
    [] when the segment has none or its scene does not build (T02, F01 and R63 report why)."""
    if s.engine_type != HYPERFRAMES or not props.has_props(s) or not (project_dir / s.gsap_scene_config.map_asset).is_file():
        return []
    try:
        return props.avoid_boxes(s, hyperframes.scene_data(s, tl, project_dir), clear)
    except (EngineError, OSError, ValueError):
        return []


def element_boxes(tl: Timeline, project_dir: Path, dom: dict[int, dict]) -> dict[int, list[dict]]:
    """Per segment: boxes captions must avoid, each with its required clearance `clear` (px).
    HyperFrames DOM element rects, props (boxes with the absolute frame range `frames` they hold for) and native declared
    overlays: caption_style.clearance (px at 1920 height). Camera focus_box frames the subject rather than drawn UI text,
    so it does not block captions."""
    clear = float(profiles.caption_style(tl)["clearance"]) * tl.settings.height / 1920
    out: dict[int, list[dict]] = {}
    for s in tl.segments:
        boxes = ([dict(r, clear=clear) for r in element_windows(dom[s.seg_index], s, tl.settings.fps)]
                 if s.seg_index in dom else [])
        for o in s.overlays:
            boxes.append({"id": f"overlay:{o.id}", "text": o.text or "", "x": o.box[0], "y": o.box[1], "w": o.box[2],
                          "h": o.box[3], "clear": clear})
        boxes += prop_rects(s, tl, project_dir, clear)
        if boxes:
            out[s.seg_index] = boxes
    return out


def _violation(box: tuple, rects: list[dict]) -> tuple[float, dict] | None:
    worst = None
    for r in rects:
        g = _gap(box, r)
        intersects = (box[0] < r["x"] + r["w"] and r["x"] < box[2] and box[1] < r["y"] + r["h"] and r["y"] < box[3])
        if (r["clear"] > 0 and g < r["clear"]) or (r["clear"] == 0 and intersects):
            if worst is None or g < worst[0]:
                worst = (g, r)
    return worst


def _rects_for(ev: tuple, tl: Timeline, boxes: dict[int, list[dict]]) -> list[dict]:
    return [r for s in tl.segments if s.seg_index in boxes and ev[1] > s.start_frame and ev[0] < s.end_frame
            for r in boxes[s.seg_index] if "frames" not in r or (ev[1] > r["frames"][0] and ev[0] < r["frames"][1])]


def r63_caption_clearance(tl: Timeline, events: list[tuple], boxes: dict[int, list[dict]]) -> list[Finding]:
    """R63 caption vs elements: every caption keeps its required clearance from every element box of every segment
    it is shown in (HyperFrames DOM boxes, props, native overlays: caption_style.clearance px)."""
    out = []
    for ev in events:
        v = _violation(caption_box(ev), _rects_for(ev, tl, boxes))
        if v:
            g, r = v
            seg = next(s.seg_index for s in tl.segments if s.start_frame <= ev[0] < s.end_frame) if ev[0] < tl.total_frames else None
            need = f"min {r['clear']:.0f}px" if r["clear"] > 0 else "must not intersect"
            out.append(Finding("R63", "fail", f"caption '{ev[2]}' frames {ev[0]}-{ev[1]} at y={ev[6]:.0f} is {g:.0f}px from "
                               f"{r['id']} '{r.get('text', '')[:24]}' ({need})", seg))
    return out


def place_captions(tl: Timeline, events: list[tuple], boxes: dict[int, list[dict]]) -> tuple[list[tuple], list[Finding]]:
    """Automatic caption placement. Captions are grouped by the segment their first frame falls in; a group that
    violates R63 gets one centre y: the y closest to the slot y inside the vertical safe area (bottom capped at
    caption_style.max_bottom_y) that satisfies R63 for every caption of the group against the boxes of every segment
    each caption is shown in.
    Returns (y_overrides [(seg.start_frame, seg.end_frame, y)], findings)."""
    H = tl.settings.height
    top, bottom = SAFE_TOP * H / 1920, H - SAFE_BOTTOM * H / 1920
    try:
        lim = max_bottom(tl)
    except ValueError:
        lim = None   # Z01 reports the invalid value
    if lim is not None:
        bottom = min(bottom, lim)
    band = profiles.caption_style(tl).get("place_band")   # [lo, hi] caption centre as a fraction of H
    lo_c, hi_c = (band[0] * H, band[1] * H) if band else (-1e9, 1e9)
    ovs, out, pad = [], [], caption_pad(tl)
    for s in tl.segments:
        evs = [e for e in events if s.start_frame <= e[0] < s.end_frame]
        rects = {id(e): _rects_for(e, tl, boxes) for e in evs}
        if not evs or not any(rects.values()):
            continue

        def ok(y: float) -> bool:
            for e in evs:
                box = caption_box((*e[:6], y))
                if box[1] < top or box[3] + pad > bottom or _violation(box, rects[id(e)]):
                    return False
            return True
        y0 = evs[0][6]
        if ok(y0):
            continue
        half = max(e[3] for e in evs) * CAPTION_BAND_HALF
        lo, hi = max(int(top + half), math.ceil(lo_c)), min(int(bottom - half), math.floor(hi_c))
        grid = set(range(lo, hi + 1, PLACE_STEP)) | ({lo, hi} if band and lo <= hi else set())
        cands = sorted(grid, key=lambda y: (abs(y - y0), y))
        y = next((c for c in cands if ok(c)), None)
        if y is None:
            where = f" inside caption_style.place_band {lo_c:.0f}-{hi_c:.0f}px" if band else ""
            out.append(Finding("R63", "fail", f"no caption position{where} satisfies R63 against all element boxes; "
                               "move pins, cards or overlays, or set caption_style.y_overrides", s.seg_index))
            continue
        ovs.append((s.start_frame, s.end_frame, float(y)))
        out.append(Finding("R63", "fix", f"captions starting in frames {s.start_frame}-{s.end_frame} moved y {y0:.0f} -> {y}", s.seg_index))
    return ovs, out


def r63_dom(tl: Timeline, project_dir: Path, workdir: Path, snaps: dict) -> tuple[list[Finding], dict[int, dict]]:
    """Builds each HyperFrames segment, seeks 3 times, checks element rects. Returns (findings, {seg: probe result});
    a probe result also carries "build_s" (build_dir wall seconds, the legibility ink probe included), "cached" and
    the probe phase "timing" (hyperframes.probe_dom)."""
    W, H, fps = tl.settings.width, tl.settings.height, tl.settings.fps
    hf_segs = [s for s in tl.segments if s.engine_type == HYPERFRAMES and (project_dir / s.gsap_scene_config.map_asset).is_file()]
    if not hf_segs:
        return [Finding("R63", "skip", "no HyperFrames segments")], {}
    dom: dict[int, dict] = {}

    def one(s: Segment) -> list[Finding]:
        if s.gsap_scene_config.split is not None:   # split screen: each panel probed at its own size
            fs = []
            for k, (ps, pt) in enumerate(hyperframes.split_panels(s, tl)):
                fs += panel(ps, pt, f"seg{s.seg_index:03d}_{'tb'[k]}", " (bottom panel)" if k else " (top panel)")
            return fs
        return panel(s, tl, f"seg{s.seg_index:03d}", "")

    def panel(s: Segment, tl: Timeline, name: str, where: str) -> list[Finding]:
        W, H = tl.settings.width, tl.settings.height
        try:
            t0 = time.perf_counter()
            skey = hyperframes.keys(s, tl, project_dir)[0]
            d, scene = hyperframes.build_dir(s, tl, project_dir, workdir / "hf" / name)
            (workdir / "hf" / f"{name}.key").write_text(skey)
            build_s = time.perf_counter() - t0
            times = probe_times(s, fps)
            lg = scene.get("legibility")
            # R64 also probes every element sample time, with background and glyph-mask screenshots
            all_t = sorted(set(times) | set(legibility.all_times(scene, lg))) if lg else times
            res = hyperframes.probe_dom(d, all_t, W, H, workdir / "snaps" / name, bg="bg" if lg else "", ref=skey)
            res["build_s"] = round(build_s, 3)
        except EngineError as e:
            return [Finding("R63", "fail", f"DOM probe failed: {str(e)[-300:]}", s.seg_index)]
        res["all_times"] = list(res["times"])
        if lg:
            res["legibility"] = {"lg": lg, "elements": legibility.elements(scene),
                                 "times": [i for i in res["times"] if i.get("clip") or i.get("line_clip")]}
            res["times"] = [i for i in res["times"] if any(abs(i["t"] - t) < 1e-3 for t in times)]   # probe t: 4 dp
        mark_flying(res, scene)
        dom.setdefault(s.seg_index, res)
        fs = []
        for item in res["times"]:
            fs += check_rects(item["rects"], W, H, item["t"], s.seg_index)
        fs = dedupe_r63(fs)
        if where:
            fs = [dataclasses.replace(f, message=f.message + where) for f in fs]
        if res["times"] and res["times"][-1].get("png"):
            snaps[s.seg_index] = res["times"][-1]["png"]
        return fs or [Finding("R63", "pass", f"{sum(len(visible(i['rects'])) for i in res['times'])} element rects clean at {len(times)} seeks", s.seg_index)]

    with ThreadPoolExecutor(max_workers=min(3, len(hf_segs))) as ex:
        return [f for fs in ex.map(one, hf_segs) for f in fs], dom


def dom_timing(dom: dict[int, dict], r64_s: float) -> dict:
    """Sub-check timing of layer2_dom, seconds summed over the HyperFrames segments (segments probe in parallel, so
    the sum can exceed the stage wall time): build (build dirs incl. the legibility ink probes), browser_load,
    r63_rects (seeks + DOM reads), snapshots (snap PNGs), bg_screenshots (text- and line-hidden backgrounds),
    glyph_masks (glyph and line masks), r64_numpy (R64 legibility pixel math, the call r64_s timed); probes and
    cache_hits count the segment probes and those the DOM probe cache answered."""
    ms = {k: sum((r.get("timing") or {}).get(k, 0) for r in dom.values() if not r.get("cached"))
          for k in ("load", "rects", "snaps", "bg", "masks")}
    return {"build": round(sum(r.get("build_s", 0.0) for r in dom.values()), 3),
            "browser_load": ms["load"] / 1000, "r63_rects": ms["rects"] / 1000, "snapshots": ms["snaps"] / 1000,
            "bg_screenshots": ms["bg"] / 1000, "glyph_masks": ms["masks"] / 1000, "r64_numpy": round(r64_s, 3),
            "probes": len(dom), "cache_hits": sum(bool(r.get("cached")) for r in dom.values())}


def _rgb_hex(css: str) -> str | None:
    m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)", css.strip())
    return "#%02x%02x%02x" % tuple(int(v) for v in m.groups()) if m else None


def e01_entity_colours(tl: Timeline, dom: dict[int, dict]) -> list[Finding]:
    """E01: every region fill the DOM probe painted shows its entity's one colour (entities.colours; a block face its
    entities.block_colour) exactly; two entities never share a colour. Text colour is not checked (R64 owns it). No
    finding when no region is painted."""
    col, theme = entities.colours(tl), profiles.of(tl).render_values("hf")
    out = []
    shared: dict[str, list[str]] = {}
    for e, c in col.items():
        shared.setdefault(c, []).append(e)
    out += [Finding("E01", "warn", f"entities {', '.join(es)} share colour {c}: add colours to theme.palette or "
                                   "theme.entities") for c, es in shared.items() if len(es) > 1]
    n = 0
    for seg, res in sorted(dom.items()):
        seen = set()
        for item in res.get("times", [])[:1]:
            for p in item.get("paints", []):
                if p["kind"] not in ("fill", "block"):
                    continue
                key = (p["entity"], p["kind"])
                if key in seen:
                    continue
                seen.add(key)
                n += 1
                want, got = col.get(p["entity"]), _rgb_hex(p["color"])
                if want and p["kind"] == "block":
                    want = entities.block_colour(want, theme)
                if got != want:
                    out.append(Finding("E01", "fail", f"fill of entity '{p['entity']}' painted {got or p['color']}, "
                                                      f"entity colour {want}", seg))
    if n and not any(f.level == "fail" for f in out):
        out.append(Finding("E01", "pass", f"{n} region fills match their entity colour "
                                          f"({len(col)} entities)"))
    return out


def b01_watermark(tl: Timeline, events: list[tuple], boxes: dict[int, list[dict]]) -> list[Finding]:
    """B01 (profile watermark only): the watermark text sits inside the safe area and intersects no element box
    (DOM element rects at the probed seeks, declared overlays, props) and no caption. With [print]
    every text box (DOM rects, overlays; not focus boxes) also keeps print.watermark_clear_px from it."""
    pl = watermark.placement(tl)
    if pl is None:
        return []
    W, H = tl.settings.width, tl.settings.height
    r = pl.text_rect()
    pr = profiles.of(tl).data.print
    gap = pr.watermark_clear_px * H / 1920 if pr is not None else 0.0
    rt = (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])
    out = []
    sx, st, sb = SAFE_X * W / 1080, SAFE_TOP * H / 1920, H - SAFE_BOTTOM * H / 1920
    if (r["x"] < sx - 0.5 or r["x"] + r["w"] > W - sx + 0.5 or r["y"] < st - 0.5
            or r["y"] + r["h"] > sb + 0.5):
        out.append(Finding("B01", "fail", f"watermark ({r['x']:.0f},{r['y']:.0f},{r['w']:.0f}x{r['h']:.0f}) leaves the "
                           "safe area; shorten watermark.text or lower watermark.px"))
    n = 0
    for seg, bs in sorted(boxes.items()):
        for b in bs:
            n += 1
            if _rect_overlap(r, b):
                out.append(Finding("B01", "fail", f"watermark overlaps {b['id']} '{b.get('text', '')[:30]}' at "
                                   f"({b['x']:.0f},{b['y']:.0f},{b['w']:.0f}x{b['h']:.0f})", seg))
            elif gap and not b["id"].startswith(("focus:", "prop:")) and (g := _gap(rt, b)) < gap - 0.5:
                out.append(Finding("B01", "fail", f"{b['id']} '{b.get('text', '')[:30]}' {g:.0f} px from the watermark "
                                   f"(print.watermark_clear_px {pr.watermark_clear_px:g})", seg))
    for e in events:
        x0, y0, x1, y1 = caption_box(e)
        if _rect_overlap(r, {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}):
            out.append(Finding("B01", "fail", f"watermark overlaps caption '{e[2]}' frames {e[0]}-{e[1]}"))
    tail = f" (>= {pr.watermark_clear_px:g} px)" if gap else ""
    return out or [Finding("B01", "pass", f"watermark ({r['x']:.0f},{r['y']:.0f},{r['w']:.0f}x{r['h']:.0f}) clear of "
                                          f"{n} element boxes{tail} and {len(events)} captions")]


def r64_rows(dom: dict[int, dict]) -> list[dict]:
    """R64 measurements: one row per adaptive text element per probed time inside its interval at opacity >= 0.9.
    The background is the probe's text-hidden screenshot under the element's glyph mask (dilated by the halo width);
    the text colour, halo colour, alpha, width and blur are the element's computed style and data-halo (`_halo`);
    vignette alpha and escalation step come from its data-ink-state; polarity = dark when the painted ink is darker
    than the background median Y. Dark rows also carry the counter check (legibility.counters on the painted frame)."""
    import numpy as np
    from PIL import Image
    rows = []
    for seg, res in sorted(dom.items()):
        meta = res.get("legibility")
        if not meta:
            continue
        lg, els = meta["lg"], {e["id"]: e for e in meta["elements"]}
        for item in meta["times"]:
            if not item.get("clip"):
                continue
            frames, png = None, None
            for r in item["rects"]:
                el, st = els.get(r["id"]), legibility.state(r)
                if not el or not st or not el["start"] - 1e-3 <= item["t"] <= el["end"] + 1e-3 or r.get("opacity", 1) < 0.9:
                    continue
                if frames is None:
                    frames = legibility.load_frames(item)
                hx = _halo(r)
                org = (item["clip"]["x"], item["clip"]["y"])
                bg, mask = frames
                s = legibility.measure(bg, mask, r, el, lg, item["t"], org, (hx["w"], hx["blur"]) if hx else None)
                ink, (halo, ha) = _rgb_hex(r["color"]), (hx["rgb"], hx["alpha"]) if hx else _stroke(r)
                if s is None or ink is None:
                    continue
                y = legibility.apca_y(s.rgb)
                pol = "dark" if float(legibility.apca_y(legibility.hex_rgb(ink))) < float(np.median(y)) else "light"
                cnt = None
                if pol == "dark" and item.get("png"):
                    if png is None:
                        png = np.asarray(Image.open(item["png"]).convert("RGB"))
                    oth = tuple((o, legibility.state(o)) for o in item["rects"] if o is not r and o.get("opacity", 0) > 0
                                and legibility.state(o))
                    cnt = legibility.counters(png, bg, mask, r, el, lg, org, st, oth)
                style = legibility.Style(pol, ink, legibility.rgb_hex(halo), ha, float(st["va"]), int(st["step"]))
                ev = legibility.evaluate(s, style, lg)
                rows.append({"seg": seg, "id": r["id"], "text": r.get("text", ""), "kind": el["kind"], "t": item["t"],
                             "polarity": pol, "ink": ink, "halo": legibility.rgb_hex(halo), "halo_alpha": ha,
                             "vignette_alpha": float(st["va"]), "step": int(st["step"]), "flag": bool(st.get("flag")),
                             "y": [round(float(v), 4) for v in np.percentile(y, [10, 50, 90])],
                             "lc": round(ev.lc, 1), "lc_adjacent": round(ev.lc_adjacent, 1), "wcag": round(ev.wcag, 2),
                             "target": legibility.target(el["kind"], lg), "wcag_target": legibility.wcag_target(el["kind"], lg),
                             "fail_below": legibility.target(el["kind"], lg) - lg["lc_fail_margin"], "floor": lg["lc_floor"],
                             "halo_em": round(hx["w"] / hx["px"], 4) if hx else None,
                             "blur_ratio": round(hx["blur"] / hx["w"], 4) if hx and hx["w"] > 0 else None,
                             "counters": cnt})
    return rows


def _halo(r: dict) -> dict | None:
    """Painted halo of a probed adaptive element: data-halo (w, blur, px) and the ::after colour (rgb, alpha)."""
    try:
        h = json.loads(r.get("halo") or "null")
    except ValueError:
        h = None
    m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", (r.get("halo_color") or "").strip())
    if not h or not m:
        return None
    return {**h, "rgb": [int(v) for v in m.groups()[:3]], "alpha": float(m.group(4) or 1)}


def r64_line_rows(dom: dict[int, dict]) -> list[dict]:
    """R64 line measurements: one row per visible [data-line] element (pin head, route, region outline, leader) at
    opacity >= 0.9 per probed time with line screenshots (legibility.line_lc)."""
    import numpy as np
    from PIL import Image
    rows = []
    for seg, res in sorted(dom.items()):
        meta = res.get("legibility")
        if not meta:
            continue
        lg = meta["lg"]
        for item in meta["times"]:
            if not item.get("line_clip"):
                continue
            bg = mk = None
            for ln in item.get("lines", []):
                if ln.get("opacity", 0) < 0.9 or ln["w"] <= 0 or ln["h"] <= 0:
                    continue
                if bg is None:
                    bg = np.asarray(Image.open(item["line_bg"]).convert("RGB"))
                    mk = np.asarray(Image.open(item["line_mask"]).convert("RGB"))
                cols = json.loads(ln.get("ink") or "[]")
                got = legibility.line_lc(bg, mk, ln, cols, lg, (item["line_clip"]["x"], item["line_clip"]["y"])) if cols else None
                if got is None:
                    continue
                rows.append({"seg": seg, "id": ln["id"], "kind": ln["id"].split(":")[0], "t": item["t"], "ink": cols,
                             "lc": round(got[0], 1), "ring_px": got[1], "target": lg["lc_line"],
                             "fail_below": lg["lc_line_fail"]})
    return rows


def r64_box_rows(dom: dict[int, dict]) -> list[dict]:
    """R64 box labels: one row per visible ([data-box], opacity >= 0.9) solid-box label per probed time: APCA |Lc| of
    its text colour on its opaque box background (times data-box-dim, the darkest factor of a box texture), target the
    element's data-box."""
    rows = []
    for seg, res in sorted(dom.items()):
        for item in res.get("times", []):
            for r in item.get("rects", []):
                if not r.get("box") or r.get("opacity", 0) < 0.9:
                    continue
                m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", (r.get("bg") or "").strip())
                ink = _rgb_hex(r["color"])
                if not m or float(m.group(4) or 1) < 0.99 or ink is None:
                    continue
                k = float(r.get("box_dim") or 1)
                bg = [round(int(v) * k) for v in m.groups()[:3]]
                lc = float(legibility.apca_lc(legibility.apca_y(legibility.hex_rgb(ink)), legibility.apca_y(bg)))
                rows.append({"seg": seg, "id": r["id"], "text": r.get("text", ""), "t": item["t"], "ink": ink,
                             "bg": legibility.rgb_hex(bg), "lc": round(abs(lc), 1), "target": float(r["box"])})
    return rows


def _stroke(r: dict) -> tuple[list[int], float]:
    """(stroke RGB, alpha) of a probed text element: its text stroke when painted under the fill, else alpha 0."""
    m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", (r.get("stroke") or "").strip())
    if not m or r.get("stroke_px", 0) <= 0 or not (r.get("paint_order") or "").startswith("stroke"):
        return [0, 0, 0], 0.0
    return [int(v) for v in m.groups()[:3]], float(m.group(4) or 1)


def r64_legibility(tl: Timeline, dom: dict[int, dict]) -> list[Finding]:
    """R64 (profile [legibility]): every adaptive text element (teardrop label, title, year, counter, area label) at
    every probed time inside its interval reaches APCA Lc >= target - lc_fail_margin at the hard-case percentile of
    its effective background (dark ink: dark_percentile; light text: light_percentile) and Lc >= lc_floor against its
    darkest (lightest) adjacent pixels; fail otherwise. Warn: WCAG ratio below wcag_label / wcag_title, or an element
    the chooser flagged. Targets: lc_title, lc_figure, lc_label, lc_area (r64_rows). Dark ink also fails on a painted
    halo outside halo_width_min_em..halo_width_max_em, blur > halo_blur_max_ratio x width, alpha outside
    halo_alpha..halo_alpha_max, or counters less than counter_open_min open. Lines (pins, routes, region outlines,
    leaders; r64_line_rows): fail below lc_line_fail, warn below lc_line. Solid-box labels (r64_box_rows): fail when
    the text on its box is below the box target (theme label_box_lc)."""
    if profiles.of(tl).legibility is None:
        return []
    rows = r64_rows(dom)
    out, worst = [], None
    lg0 = next((res["legibility"]["lg"] for res in dom.values() if res.get("legibility")), None)
    for w in rows:                                  # painted halo geometry and open counters (dark ink)
        if w["polarity"] != "dark" or w["halo_em"] is None or lg0 is None:
            continue
        tag = f"t={w['t']:.2f}s {w['id']} '{w['text'][:24]}'"
        if not lg0["halo_width_min_em"] - 1e-3 <= w["halo_em"] <= lg0["halo_width_max_em"] + 1e-3:
            out.append(Finding("R64", "fail", f"{tag}: halo width {w['halo_em']} em outside "
                               f"{lg0['halo_width_min_em']}-{lg0['halo_width_max_em']}", w["seg"]))
        if w["blur_ratio"] is not None and w["blur_ratio"] > lg0["halo_blur_max_ratio"] + 1e-3:
            out.append(Finding("R64", "fail", f"{tag}: halo blur {w['blur_ratio']} x width > "
                               f"{lg0['halo_blur_max_ratio']}", w["seg"]))
        if not lg0["halo_alpha"] - 0.01 <= w["halo_alpha"] <= lg0["halo_alpha_max"] + 0.01:
            out.append(Finding("R64", "fail", f"{tag}: halo alpha {w['halo_alpha']:.2f} outside "
                               f"{lg0['halo_alpha']}-{lg0['halo_alpha_max']}", w["seg"]))
        if w["counters"] and w["counters"][1] < lg0["counter_open_min"]:
            out.append(Finding("R64", "fail", f"{tag}: halo fills the counters ({w['counters'][1]:.0%} of "
                               f"{w['counters'][0]} counter px open < {lg0['counter_open_min']:.0%})", w["seg"]))
    lines = r64_line_rows(dom)
    for w in lines:
        txt = f"t={w['t']:.2f}s {w['id']} Lc {w['lc']} (colours {', '.join(w['ink'])})"
        if w["lc"] < w["fail_below"]:
            out.append(Finding("R64", "fail", f"{txt} < line Lc {w['fail_below']}", w["seg"]))
        elif w["lc"] < w["target"]:
            out.append(Finding("R64", "warn", f"{txt} below line target {w['target']}", w["seg"]))
    boxes = r64_box_rows(dom)
    for w in boxes:
        if w["lc"] < w["target"]:
            out.append(Finding("R64", "fail", f"t={w['t']:.2f}s {w['id']} '{w['text'][:24]}' {w['ink']} on box "
                               f"{w['bg']} Lc {w['lc']} < {w['target']:g}", w["seg"]))
    for w in rows:
        txt = (f"t={w['t']:.2f}s {w['id']} '{w['text'][:24]}' {w['polarity']} ink {w['ink']} Lc {w['lc']} "
               f"(adjacent {w['lc_adjacent']}, WCAG {w['wcag']}, step {w['step']})")
        if worst is None or w["lc"] - w["target"] < worst[0]:
            worst = (w["lc"] - w["target"], txt, w["seg"])
        if w["lc"] < w["fail_below"] or w["lc_adjacent"] < w["floor"]:
            out.append(Finding("R64", "fail", f"{txt} < Lc {w['fail_below']} (adjacent < {w['floor']})", w["seg"]))
        elif w["wcag"] < w["wcag_target"]:
            out.append(Finding("R64", "warn", f"{txt}: WCAG below {w['wcag_target']}", w["seg"]))
    for eid in sorted({(w["seg"], w["id"]) for w in rows if w["flag"]}):
        out.append(Finding("R64", "warn", f"{eid[1]}: no style reaches its Lc target at every sample (flagged)", eid[0]))
    for seg, res in sorted(dom.items()):            # every element measured at every one of its sample times
        meta = res.get("legibility")
        if not meta:
            continue
        for el in meta["elements"]:
            want = legibility.sample_times(el, meta["lg"]["samples"], tl.settings.fps)
            got = {round(w["t"], 3) for w in rows if w["seg"] == seg and w["id"] == el["id"]}
            miss = [t for t in want if not any(abs(t - g) < 1e-3 for g in got)]
            if miss:
                out.append(Finding("R64", "fail", f"{el['id']}: not measured at {len(miss)} of {len(want)} sample "
                                   f"times (first t={miss[0]:.2f}s): no adaptive ink state, no glyphs or hidden", seg))
    if (worst or boxes) and not any(f.level == "fail" for f in out):
        nc = sum(1 for w in rows if w["counters"])
        lw = min(lines, key=lambda w: w["lc"], default=None)
        bw = min(boxes, key=lambda w: w["lc"], default=None)
        out.append(Finding("R64", "pass", (f"{len(rows)} element samples >= target - margin ({nc} with open counters); "
                           f"lowest vs target: {worst[1]}" if worst else "no adaptive text") +
                           (f"; {len(lines)} line samples, lowest {lw['id']} Lc {lw['lc']}" if lw else "") +
                           (f"; {len(boxes)} box label samples, lowest {bw['id']} Lc {bw['lc']}" if bw else ""),
                           worst[2] if worst else bw["seg"]))
    return out
