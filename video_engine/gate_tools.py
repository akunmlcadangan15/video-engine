"""Read-only gate tools behind `engine gates`: the registry as a table, the state of each gate in a channel profile,
the generated block of docs/GATES.md, and statistics over build reports.

Nothing here runs a gate or changes a file."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import gate_registry as reg

REPORTS = ("build_report.json", "build_report.failed.json")
MD_BEGIN = "<!-- BEGIN GENERATED: engine gates --format md -->"
MD_END = "<!-- END GENERATED: engine gates --format md -->"


# --- registry views ---------------------------------------------------------------------------------------------

def select(ids: list[str] | None = None, layer: str | None = None, kind: str | None = None) -> list[reg.Gate]:
    """Registry entries filtered by ids (any case), layer and class; unknown ids or layers raise ValueError."""
    if layer is not None and layer not in reg.LAYERS:
        raise ValueError(f"unknown layer {layer!r} (known: {', '.join(reg.LAYERS)})")
    if kind is not None and kind not in ("technical", "style"):
        raise ValueError(f"unknown class {kind!r} (known: technical, style)")
    wanted = [i.upper() if i.upper() in reg.BY_ID else i for i in ids or []]
    bad = [i for i in wanted if i not in reg.BY_ID]
    if bad:
        raise ValueError(f"unknown gate(s) {', '.join(bad)}; `engine gates` lists them")
    return [g for g in reg.GATES
            if (not wanted or g.id in wanted) and (layer is None or g.layer == layer)
            and (kind is None or g.kind == kind)]


def is_active(g: reg.Gate, p) -> bool:
    """True when profile `p` (profiles.Profile) switches the gate on. Data-dependent gates (props, snapshots) count
    as on: whether they find anything depends on the timeline."""
    gt = p.gates
    tables = {"H01": gt.hook, "C01": gt.cuts, "V01": gt.visual_type, "N01": gt.novelty, "P01": gt.camera,
              "K01": gt.keyword_return, "K02": gt.ending_keyword, "C02": gt.cta, "D01": gt.duration,
              "D02": gt.duration_exact, "S01": gt.silence}
    if g.id in tables:
        return tables[g.id] is not None
    if g.id == "P02":
        return gt.camera is not None and gt.camera.min_drift_per_s is not None
    if g.id in reg.ANTI_SLOP_SWITCH:
        return bool(getattr(gt.anti_slop, reg.ANTI_SLOP_SWITCH[g.id]))
    if g.id == "R64":
        return p.legibility is not None
    if g.id == "T01":
        return p.print is not None
    if g.id == "B01":
        return p.watermark is not None
    if g.id == "Z01":
        return "max_bottom_y" in p.caption_style
    if g.layer == "layout" and g.id.startswith("L"):
        return p.layout is not None
    if g.id == "A02":
        return bool(p.audio.require_source)
    if g.id == "AIQA":
        return False        # only with `--ai-qa`
    return True


def effect(g: reg.Gate, p) -> str:
    """What a fail of the gate does under profile `p`: off | blocks | warns | on request | meta."""
    if g.layer == "meta":
        return "meta"
    if g.id == "AIQA":
        return "on request"
    if not is_active(g, p):
        return "off"
    gt = p.gates
    if g.id in reg.ANTI_SLOP_SWITCH:
        fails = gt.anti_slop.level_for(g.id) == "fail"
        if g.kind == "technical":
            return "blocks" if fails else "warns"
        return "blocks" if fails and g.id in gt.require else "warns"
    if g.kind == "style":
        return "blocks" if g.id in gt.require else "warns"
    return "warns" if g.id in gt.advisory else "blocks"


def rows(gates: list[reg.Gate], profile=None) -> list[dict]:
    out = []
    for g in gates:
        r = {"id": g.id, "class": g.kind, "layer": g.layer, "title": g.title, "summary": g.summary,
             "switch": g.switch, "never_advisory": g.never_advisory}
        if profile is not None:
            r["effect"] = effect(g, profile)
        out.append(r)
    return out


def render_table(rs: list[dict]) -> str:
    """Aligned text table: id, class, layer, [effect,] title."""
    cols = ["id", "class", "layer"] + (["effect"] if rs and "effect" in rs[0] else []) + ["title"]
    width = {c: max(len(c), *(len(str(r[c])) for r in rs)) for c in cols} if rs else {c: len(c) for c in cols}
    line = lambda r: "  ".join(str(r[c]).ljust(width[c]) for c in cols).rstrip()   # noqa: E731
    head = line({c: c.upper() for c in cols})
    return "\n".join([head, "  ".join("-" * width[c] for c in cols), *(line(r) for r in rs)])


def render_detail(rs: list[dict]) -> str:
    """One block per gate: all fields."""
    blocks = []
    for r in rs:
        lines = [f"{r['id']}  {r['title']}", f"  class:   {r['class']}" + ("  (never advisory)" if r["never_advisory"] else ""),
                 f"  layer:   {r['layer']} ({reg.LAYERS[r['layer']]})", f"  rule:    {r['summary']}"]
        if r["switch"]:
            lines.append(f"  switch:  {r['switch']}")
        if "effect" in r:
            lines.append(f"  profile: {r['effect']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_markdown() -> str:
    """The generated gate index of docs/GATES.md: one table per layer, in pre-flight order."""
    out = [MD_BEGIN, ""]
    for layer, label in reg.LAYERS.items():
        gs = reg.in_layer(layer)
        if not gs:
            continue
        out += [f"### {label}", "", "| ID | Class | Name | Rule | Switch |", "|---|---|---|---|---|"]
        for g in gs:
            mark = " (never advisory)" if g.never_advisory else ""
            switch = f"`{g.switch}`" if g.switch else "always"
            out.append(f"| {g.id} | {g.kind}{mark} | {g.title} | {g.summary} | {switch} |")
        out.append("")
    out.append(MD_END)
    return "\n".join(out)


# --- statistics over build reports --------------------------------------------------------------------------------

def collect_reports(paths: list[str | Path]) -> list[Path]:
    """Report files named by `paths`. A path may be a report file, a project folder (reads out/), an out/ folder, or
    a folder of projects (reads each project's out/)."""
    found: list[Path] = []

    def from_out(out: Path) -> bool:
        hit = [out / n for n in REPORTS if (out / n).is_file()]
        found.extend(hit)
        return bool(hit)

    for raw in paths:
        p = Path(raw)
        if p.is_file():
            found.append(p)
        elif p.is_dir():
            if from_out(p) or from_out(p / "out"):
                continue
            for child in sorted(c for c in p.iterdir() if c.is_dir()):
                from_out(child / "out")
        else:
            raise FileNotFoundError(f"no such file or folder: {p}")
    return list(dict.fromkeys(found))


def stats(files: list[Path]) -> dict:
    """Counts per gate over the `gates` block of each report: fails, warns, waived (lowered) and the number of
    reports that mention it. A report without a `gates` block (a stage error) counts under `stage_errors`."""
    per: dict[str, Counter] = {}
    n = {"reports": 0, "ok": 0, "gate_failed": 0, "stage_errors": 0, "unreadable": 0}
    for f in files:
        try:
            rep = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            n["unreadable"] += 1
            continue
        if not isinstance(rep, dict):
            n["unreadable"] += 1
            continue
        n["reports"] += 1
        gates = rep.get("gates")
        if rep.get("ok") is True:
            n["ok"] += 1
        elif not isinstance(gates, dict) or not gates.get("fails"):
            n["stage_errors"] += 1
        else:
            n["gate_failed"] += 1
        if not isinstance(gates, dict):
            continue
        seen: set[str] = set()
        for key in ("fails", "warns", "waived"):
            for row in gates.get(key) or []:
                gid = row.get("gate") if isinstance(row, dict) else None
                if not gid:
                    continue
                per.setdefault(gid, Counter())[key] += 1
                seen.add(gid)
        for gid in seen:
            per[gid]["reports"] += 1
    table: list[dict[str, Any]] = [{"gate": g, "fails": c["fails"], "warns": c["warns"], "waived": c["waived"], "reports": c["reports"],
              "class": reg.BY_ID[g].kind if g in reg.BY_ID else "unknown"} for g, c in per.items()]
    table.sort(key=lambda r: (-(r["fails"] + r["warns"] + r["waived"]), r["gate"]))
    never = [g.id for g in reg.GATES if g.id not in per and g.layer != "meta"]
    return {"summary": n, "gates": table, "never_seen": never}


def render_stats(s: dict) -> str:
    n = s["summary"]
    head = (f"{n['reports']} report(s): {n['ok']} ok, {n['gate_failed']} failed on a gate, "
            f"{n['stage_errors']} failed with an engine error" + (f", {n['unreadable']} unreadable" if n["unreadable"] else ""))
    if not s["gates"]:
        return head + "\nno gate findings in these reports"
    cols = ["gate", "class", "fails", "warns", "waived", "reports"]
    width = {c: max(len(c), *(len(str(r[c])) for r in s["gates"])) for c in cols}
    line = lambda r: "  ".join(str(r[c]).ljust(width[c]) for c in cols).rstrip()   # noqa: E731
    body = [line({c: c.upper() for c in cols}), "  ".join("-" * width[c] for c in cols), *(line(r) for r in s["gates"])]
    tail = f"never in a finding: {', '.join(s['never_seen'])}" if s["never_seen"] else ""
    return "\n".join([head, "", *body, "", tail]).rstrip()
