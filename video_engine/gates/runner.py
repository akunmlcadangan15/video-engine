"""Pre-flight runner: layer 1 -> layer 2 math -> layer 2 DOM -> caption placement -> props -> layout -> profile style gates
-> layer 3 snapshots.
Target wall time < 5 s."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import config, layout, profiles, props
from ..timeline import CaptionWord, Timeline
from . import geo, layer1, layer2, layer3, layout_gates, props_gates, severity, style
from .core import Report


def run_preflight(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None = None, *,
                  workdir: Path | None = None, dom: bool = True, snapshots: bool = True, ai_qa=None,
                  stop_on_layer1: bool = True) -> Report:
    """Runs all gates. Mutates `tl` in memory for R56 fixes (listed in report.fixes).
    words: caption words (None = captions not available yet; caption gates report skip).
    ai_qa: AI provider for Layer 3 visual QA on the snapshots (None = off).
    workdir: scratch dir for DOM builds and snapshots; None = a dir of this call, removed before it returns."""
    if workdir is not None:
        return _run(tl, Path(project_dir), words, workdir, dom, snapshots, ai_qa, stop_on_layer1)
    own = config.workdir(tl.project_id)
    try:
        return _run(tl, Path(project_dir), words, own, dom, snapshots, ai_qa, stop_on_layer1)
    finally:
        shutil.rmtree(own, ignore_errors=True)


def _run(tl: Timeline, project_dir: Path, words, workdir: Path, dom: bool, snapshots: bool, ai_qa,
         stop_on_layer1: bool) -> Report:
    layout.apply(tl, project_dir)   # beats scenes: derived camera_path and element frames before any gate reads them
    rep = Report()
    with rep.timed("layer1"):
        rep.extend(layer1.run_layer1(tl, project_dir, words))
    if stop_on_layer1 and any(f.gate == "T02" and f.level == "fail" for f in rep.findings):
        return rep
    with rep.timed("layer2_math"):
        rep.extend(layer2.m01_source_length(tl, project_dir))
        rep.extend(layer2.m02_map_resolution(tl, project_dir))
        rep.extend(layer2.m03_tilt_cover(tl, project_dir))
        rep.extend(layer2.m04_globe_resolution(tl, project_dir))
        rep.extend(layer2.g01_grade_luts(tl, project_dir))
        rep.extend(geo.run(tl))
        fs, fixes = layer2.r56_camera(tl, project_dir)
        rep.extend(fs)
        rep.fixes += fixes
        fs, events = layer2.r62_caption_margins(tl, words or [])
        rep.extend(fs if words is not None else [f for f in fs if f.level == "fail"])
    dom_snaps: dict[int, str] = {}
    if dom:
        with rep.timed("layer2_dom"):
            fs, dom_res = layer2.r63_dom(tl, project_dir, workdir, dom_snaps)
            rep.extend(fs)
            rep.extend(layer2.e01_entity_colours(tl, dom_res))
            t64 = time.perf_counter()
            rep.extend(layer2.r64_legibility(tl, dom_res))
            rep.subtimings["layer2_dom"] = layer2.dom_timing(dom_res, time.perf_counter() - t64)
            rep.dom = dom_res
    else:
        dom_res = {}
    with rep.timed("layer2_place"):
        boxes = layer2.element_boxes(tl, project_dir, dom_res)
        clash = layer2.r63_caption_clearance(tl, events, boxes)
        if clash and profiles.caption_style(tl)["auto_place"]:
            ovs, fs = layer2.place_captions(tl, events, boxes)
            rep.caption_y_overrides = ovs
            rep.fixes += [{"path": "caption_style.y_overrides", "add": list(o)} for o in ovs]
            events = layer2.caption_events(tl, words or [], ovs)
            rep.extend(fs + layer2.r63_caption_clearance(tl, events, boxes))
        else:
            rep.extend(clash)
        if words and not clash:
            rep.add("R63", "pass", "captions clear all element boxes")
        rep.extend(layer2.z01_caption_bottom(tl, events))
        rep.extend(layer2.b01_watermark(tl, events, boxes))
    if any(props.has_props(s) for s in tl.segments):
        with rep.timed("props"):
            rep.extend(props_gates.run(tl, project_dir, events, dom_res))
    with rep.timed("layout"):
        rep.extend(layout_gates.run(tl, project_dir, events, workdir if dom else None, dom_res if dom else None))
    with rep.timed("style"):
        rep.extend(style.run_style(tl, project_dir, words))
    if snapshots:
        with rep.timed("layer3_snap"):
            fs, paths = layer3.r38_snapshots(tl, project_dir, dom_snaps, workdir / "snaps" / "gate")
            rep.extend(fs)
            rep.snapshots = paths
        if ai_qa is not None and paths:
            from ..ai import tasks
            with rep.timed("layer3_ai"):
                rep.extend(tasks.qa_snapshots(paths, ai_qa, meta=rep.ai))
    rep.findings = severity.apply(rep.findings, tl)
    rep.timings["total"] = round(sum(v for k, v in rep.timings.items()), 3)
    return rep
