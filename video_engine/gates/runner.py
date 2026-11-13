"""Pre-flight runner: layer 1 -> layer 2 math -> layer 2 DOM -> caption placement -> props -> layout -> profile style gates
-> layer 3 snapshots.
Target wall time < 5 s."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import config, gate_registry, gate_tools, layout, profiles, props
from ..timeline import HYPERFRAMES, CaptionWord, Timeline
from . import geo, layer1, layer2, layer3, layout_gates, props_gates, severity, style
from .core import Finding, Report, not_run

LATER_LAYERS = ("math", "dom", "place", "layout", "props", "visual", "profile")   # pre-flight layers after layer 1
FINISHED_FILE = ("S01",)   # a profile-layer gate the build judges on the finished file, never in the pre-flight


def _not_run(tl: Timeline, layers: tuple[str, ...], why: str, ai_qa=None) -> list[Finding]:
    """One `not run` skip finding for every gate of `layers` that the profile switches on (gate_tools.effect;
    AIQA only when it was requested): the report then names each gate whose stage did not run."""
    p = profiles.of(tl)
    has = {"dom": any(s.engine_type == HYPERFRAMES for s in tl.segments),
           "props": any(props.has_props(s) for s in tl.segments)}
    out = []
    for g in gate_registry.GATES:
        if g.layer not in layers or g.id in FINISHED_FILE or not has.get(g.layer, True):
            continue
        eff = gate_tools.effect(g, p)
        if eff != "off" and (eff != "on request" or ai_qa is not None):
            out.append(not_run(g.id, why))
    return out


def _dom_gaps(tl: Timeline, project_dir: Path, dom: bool, dom_res: dict) -> list[Finding]:
    """What the DOM stage did not look at. DOM stage off: every DOM gate, plus the DOM halves of L01 (rendered
    boxes against the plan) and AS05/AS06 (built HTML). DOM stage on: per HyperFrames segment without a probe result
    (probe failed, or no toolchain; R63 says why), E01 and R64, which otherwise say nothing for that segment."""
    hf = [s for s in tl.segments if s.engine_type == HYPERFRAMES]
    if not hf:
        return []
    p = profiles.of(tl)
    on = {g: gate_tools.effect(gate_registry.BY_ID[g], p) != "off" for g in ("R64", "L01", "AS05", "AS06")}
    if not dom:
        why = "DOM stage off: HyperFrames scenes not probed in headless Chrome"
        out = _not_run(tl, ("dom",), why)
        if on["L01"] and any(s.gsap_scene_config and s.gsap_scene_config.beats for s in hf):
            out.append(Finding("L01", "skip", "rendered text boxes not compared with the layout plan: DOM stage off"))
        out += [Finding(g, "skip", "built HyperFrames HTML not scanned: DOM stage off") for g in ("AS05", "AS06") if on[g]]
        return out
    out = []
    for s in hf:
        if s.seg_index in dom_res or not (project_dir / s.gsap_scene_config.map_asset).is_file():
            continue
        out.append(not_run("E01", "no DOM probe result for this segment (see R63): region fills not checked", s.seg_index))
        if on["R64"]:
            out.append(not_run("R64", "no DOM probe result for this segment (see R63): text legibility not checked",
                               s.seg_index))
    return out


def run_preflight(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None = None, *,
                  workdir: Path | None = None, dom: bool = True, snapshots: bool = True, ai_qa=None,
                  stop_on_layer1: bool = True, draft: bool = False) -> Report:
    """Runs all gates. Mutates `tl` in memory for R56 fixes (listed in report.fixes).
    words: caption words (None = captions not available yet; caption gates report skip).
    ai_qa: AI provider for Layer 3 visual QA on the snapshots (None = off).
    workdir: scratch dir for DOM builds and snapshots; None = a dir of this call, removed before it returns.
    draft: draft mode (gates/severity.py): fails of technical gates that a profile may make advisory become warnings."""
    if workdir is not None:
        return _run(tl, Path(project_dir), words, workdir, dom, snapshots, ai_qa, stop_on_layer1, draft)
    own = config.workdir(tl.project_id)
    try:
        return _run(tl, Path(project_dir), words, own, dom, snapshots, ai_qa, stop_on_layer1, draft)
    finally:
        shutil.rmtree(own, ignore_errors=True)


def _run(tl: Timeline, project_dir: Path, words, workdir: Path, dom: bool, snapshots: bool, ai_qa,
         stop_on_layer1: bool, draft: bool = False) -> Report:
    layout.apply(tl, project_dir)   # beats scenes: derived camera_path and element frames before any gate reads them
    rep = Report()
    with rep.timed("layer1"):
        rep.extend(layer1.run_layer1(tl, project_dir, words))
    if stop_on_layer1 and any(f.gate == "T02" and f.level == "fail" for f in rep.findings):
        rep.extend(_not_run(tl, LATER_LAYERS, "pre-flight stopped after layer 1 because T02 failed", ai_qa))
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
            rep.extend(layer2.e01_entity_colours(tl, dom_res, project_dir))
            t64 = time.perf_counter()
            rep.extend(layer2.r64_legibility(tl, dom_res))
            rep.subtimings["layer2_dom"] = layer2.dom_timing(dom_res, time.perf_counter() - t64)
            rep.dom = dom_res
    else:
        dom_res = {}
    rep.extend(_dom_gaps(tl, project_dir, dom, dom_res))
    with rep.timed("layer2_place"):
        boxes = layer2.element_boxes(tl, project_dir, dom_res)
        clash = layer2.r63_caption_clearance(tl, events, boxes)
        if clash and profiles.caption_style(tl).get("auto_place", True):  # auto-place captions when enabled in effective caption_style
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
        built = ([d for s in tl.segments if s.engine_type == HYPERFRAMES
                  for d in ((workdir / "hf" / f"seg{s.seg_index:03d}_t", workdir / "hf" / f"seg{s.seg_index:03d}_b")
                            if s.gsap_scene_config and s.gsap_scene_config.split is not None
                            else (workdir / "hf" / f"seg{s.seg_index:03d}",))]
                 if dom else None)
        rep.extend(style.run_style(tl, project_dir, words, built))
    if snapshots:
        with rep.timed("layer3_snap"):
            fs, paths = layer3.r38_snapshots(tl, project_dir, dom_snaps, workdir / "snaps" / "gate")
            rep.extend(fs)
            rep.snapshots = paths
            cc = profiles.of(tl).gates.caption_contrast
            if cc is not None and words:
                rep.extend(layer3.r65_caption_contrast(tl, paths, events, cc.min_lc))
        if ai_qa is not None and paths:
            from ..ai import tasks
            with rep.timed("layer3_ai"):
                rep.extend(tasks.qa_snapshots(paths, ai_qa, meta=rep.ai,
                                              frame=(tl.settings.width, tl.settings.height)))
        elif ai_qa is not None:
            rep.extend([not_run("AIQA", "requested, but there is no snapshot to judge")])
    else:
        rep.extend(_not_run(tl, ("visual",), "layer 3 snapshots off", ai_qa))
    rep.findings = severity.apply(rep.findings, tl, draft=draft)
    rep.timings["total"] = round(sum(v for k, v in rep.timings.items()), 3)
    return rep
