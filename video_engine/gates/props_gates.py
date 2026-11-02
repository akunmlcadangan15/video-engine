"""Props gates (gsap_scene_config.props, beats[].props) of HyperFrames map segments.

F01 asset: the file exists, decodes as PNG or WebP and has an alpha channel with transparent pixels; a 3D model prop
    renders (models.py) to a sprite that passes the same check.
F02 resolution: source width (model props: the sprite size) / largest on-screen width over the shown frames (camera zoom, depth and pop included):
    fail below RES_FAIL, warn below RES_WARN.
F03 clear: on every shown frame (opacity > 0.05) the prop box (props.box: rotated ink box plus shadow reach) does not
    intersect (layer2.OVERLAP_TOL) a pin head, label, title, figure, card, heading, note or area label, nor a caption
    shown on that frame. Beats segments: the layout items of every frame; every segment: the DOM element rects at the
    probe seeks (layer2.probe_times holds three frames per prop); captions: the compiled caption events after placement.
F04 safe area: on every shown frame the prop box lies inside the R63 safe area (layer2.SAFE_X, SAFE_TOP, SAFE_BOTTOM); a map
prop with `offscreen` only on its first shown frame (the camera may carry it out afterwards).
Credits (R07) and licences (R14) cover props through layer1.visual_refs; timing through T02.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .. import hyperframes, layout as lay, props
from ..timeline import HYPERFRAMES, Segment, Timeline
from ..util import EngineError
from . import layer2
from .core import Finding

RES_FAIL = 1.0
RES_WARN = 1.5
PROP_FORMATS = {"PNG", "WEBP"}
CLEAR_KINDS = {"title", "year", "time", "heading", "note", "area", "label", "head"}   # layout items props stay clear of
MAX_LINES = 6


def f01_asset(path: Path) -> str | None:
    """Problem with a prop image, None when it is a PNG or WebP with transparent pixels."""
    if not path.is_file():
        return "file not found"
    try:
        with Image.open(path) as im:
            fmt = im.format
    except OSError as e:
        return f"not a decodable image ({e})"
    if fmt not in PROP_FORMATS:
        return f"format {fmt}; props are PNG or WebP"
    _, _, alpha, ink = props.image_info(path)
    if not alpha:
        return "no transparent pixels (needs an alpha channel)"
    if ink is None:
        return "fully transparent"
    return None


def _hit(b: tuple, r: tuple) -> bool:
    t = layer2.OVERLAP_TOL
    return b[0] + t < r[2] and r[0] + t < b[2] and b[1] + t < r[3] and r[1] + t < b[3]


def _fmt(b: tuple) -> str:
    return "(" + ",".join(f"{v:.0f}" for v in b) + ")"


def check_segment(s: Segment, tl: Timeline, project_dir: Path, events: list[tuple], dom: dict | None) -> list[Finding]:
    out: list[Finding] = []
    entries = props.flat(s)
    bad = False
    for i, (where, p, _, _) in enumerate(entries):
        try:
            img = props.sprite(p, project_dir, tl.settings.fps)[0] if props.is_model(p) else project_dir / p.asset
            why = f01_asset(img)
            if why and props.is_model(p):
                why = f"rendered sprite: {why}"
        except EngineError as e:
            why = str(e)
        if why:
            out.append(Finding("F01", "fail", f"{where} '{p.asset}': {why}", s.seg_index))
            bad = True
    if bad:
        return out
    nm = sum(props.is_model(p) for _, p, _, _ in entries)
    out.append(Finding("F01", "pass", f"{len(entries)} prop images with alpha"
                       + (f" ({nm} rendered from 3D models)" if nm else ""), s.seg_index))
    try:
        scene = hyperframes.scene_data(s, tl, project_dir)
    except EngineError as e:
        return out + [Finding("F01", "fail", str(e), s.seg_index)]
    W, H, cam, fps = scene["width"], scene["height"], scene["camera"], scene["fps"]
    sx, st, sb = layer2.SAFE_X * W / 1080, layer2.SAFE_TOP * H / 1920, H - layer2.SAFE_BOTTOM * H / 1920
    plan = lay.plan(s, tl, project_dir) if lay.is_beats(s, tl) else None
    dom_at = {}
    dom_ok = plan is not None or bool(dom and dom.get("times"))
    for item in (dom or {}).get("times", []):
        dom_at.setdefault(round(item["t"] * fps), []).extend(
            r for r in layer2.visible(item["rects"]) if not r["id"].startswith("anchor:"))
    seg_ev = layer2.seg_events(events, s)
    lines = {"F02": [], "F03": [], "F04": []}
    warn = []
    for (where, p, _, _), e in zip(entries, scene["props"]):
        tag = f"{where} '{p.asset}'"
        shown = list(props.frames(e, cam, W, H))
        if not shown:
            continue
        wmax = max(stt["w"] for _, stt, _ in shown)
        ratio = e["size"][0] / wmax
        msg = f"{tag}: source {e['size'][0]} px wide, shown up to {wmax:.0f} px ({ratio:.2f}x)"
        if ratio < RES_FAIL:
            lines["F02"].append(msg + f"; needs >= {RES_FAIL:g}x")
        elif ratio < RES_WARN:
            warn.append(msg + f"; below {RES_WARN:g}x")
        out_f = [(f, b) for f, _, b in shown if b[0] < sx or b[2] > W - sx or b[1] < st or b[3] > sb]
        if p.offscreen and p.anchor == "map":   # carried off by the camera: only its first shown frame must be inside
            out_f = out_f[:1] if out_f and out_f[0][0] == shown[0][0] else []
        if out_f:
            f, b = out_f[0]
            lines["F04"].append(f"{tag}: box {_fmt(b)} outside the safe area {_fmt((sx, st, W - sx, sb))} on "
                                f"{len(out_f)} frames from frame {f}")
        hits: dict[str, tuple[int, int, tuple]] = {}
        for f, _, b in shown:
            obs = []
            if plan is not None:
                obs += [(i.key, i.box) for i in lay.frame_items(plan, f)
                        if i.kind in CLEAR_KINDS and i.box is not None and i.opacity > 0.05]
            obs += [(r["id"], (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])) for r in dom_at.get(f, [])]
            fa = s.start_frame + f
            obs += [(f"caption '{ev[2]}'", layer2.caption_box(ev)) for ev in seg_ev if ev[0] <= fa < ev[1]]
            for k, r in obs:
                if _hit(b, r):
                    f0, n, rb = hits.get(k, (f, 0, r))
                    hits[k] = (f0, n + 1, rb)
        for k, (f0, n, rb) in hits.items():
            lines["F03"].append(f"{tag} overlaps {k} {_fmt(rb)} on {n} frames from frame {f0}")
    desc = {"F02": "prop source resolution", "F03": "props clear of texts, pins, cards and captions",
            "F04": "props inside the safe area"}
    for g, ls in lines.items():
        if ls:
            out += [Finding(g, "fail", m, s.seg_index) for m in ls[:MAX_LINES]]
        elif g == "F02" and warn:
            out += [Finding("F02", "warn", m, s.seg_index) for m in warn[:MAX_LINES]]
        elif g == "F03" and not dom_ok:
            out.append(Finding("F03", "skip", "DOM probe unavailable: prop contact with pins, labels and cards is "
                               "not verified", s.seg_index))
        else:
            out.append(Finding(g, "pass", f"{desc[g]}: {len(entries)} props clean", s.seg_index))
    return out


def run(tl: Timeline, project_dir: Path, events: list[tuple], dom: dict[int, dict]) -> list[Finding]:
    """Props gates of every HyperFrames segment with props (map present); none without props."""
    out = []
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES or not props.has_props(s):
            continue
        if not (project_dir / s.gsap_scene_config.map_asset).is_file():
            continue
        out += check_segment(s, tl, project_dir, events, dom.get(s.seg_index))
    return out
