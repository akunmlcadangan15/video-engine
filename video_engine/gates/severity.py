"""Gate severity: technical rules block a build, style and channel-advisory rules only warn unless required.

Classes:
  technical  every gate not in profiles.STYLE_GATE_IDS or the profile's `[gates] advisory` list (missing or invalid
             assets, broken output, decode errors, loudness, licence and credits, caption bounds, factual pins). Levels
             unchanged; fail blocks the build.
  style      profiles.STYLE_GATE_IDS plus any gate in the profile's `[gates] advisory` list. Required set = profile
             `[gates] require` + timeline `gates.require`. Required: levels unchanged (fail blocks). Not required:
             fail -> warn.
Intent and waivers (only for style/advisory gates outside the required set):
  - a P01 hold or N01 gap finding whose frame range starts inside a beat or segment marked `hold: "read"` -> info;
  - a finding of a gate listed in timeline `gates.waive` -> info.
A waive or a hold intent on a required gate changes nothing; a waive naming a required or technical gate adds one
warn finding (gate GW01) saying it was ignored. Lowered findings keep their original level in `waived`.
Two more ways to lower a technical gate:
  - timeline `gates.waive_with_reason`: the gates the registry marks waivable (L01, L06, O01) -> info, with the written
    reason in `waived` and in build_report.json `gates.waivers`;
  - draft mode (`engine build --mode draft`): a fail of any technical gate outside profiles.NEVER_ADVISORY -> warn.
"""
from __future__ import annotations

from .. import profiles
from ..gate_registry import WAIVABLE_WITH_REASON
from ..timeline import HYPERFRAMES, Timeline
from .core import Finding

HOLD_GATES = ("P01", "N01")   # style gates whose frame-ranged findings a hold:"read" intent lowers


def is_style(gate: str, tl: Timeline | None = None) -> bool:
    return gate in profiles.STYLE_GATE_IDS or (tl is not None and gate in profiles.of(tl).gates.advisory)


def required(tl: Timeline) -> set[str]:
    """Channel-required style gates plus the project's additions (a project can add, never remove)."""
    return set(profiles.of(tl).gates.require) | set(tl.gates.require if tl.gates else ())


def hold_ranges(tl: Timeline) -> list[tuple[int, int]]:
    """Absolute half-open frame ranges of every beat and segment marked hold: "read"."""
    out = []
    for s in tl.segments:
        if s.hold == "read":
            out.append((s.start_frame, s.end_frame))
        sc = s.gsap_scene_config if s.engine_type == HYPERFRAMES else None
        beats = (sc.beats or []) if sc else []
        for k, b in enumerate(beats):
            if b.hold == "read":
                end = beats[k + 1].at_frame if k + 1 < len(beats) else s.n_frames
                out.append((s.start_frame + b.at_frame, s.start_frame + end))
    return out


def waivers(tl: Timeline) -> dict[str, str]:
    """Technical gates the project lowers with a written reason (`gates.waive_with_reason`): gate -> reason."""
    return {w.gate: w.reason.strip() for w in (tl.gates.waive_with_reason if tl.gates else ())}


def apply(findings: list[Finding], tl: Timeline, *, draft: bool = False) -> list[Finding]:
    """Findings with the severity rules applied (new list; Finding objects are updated in place).
    draft: a draft build also lowers the fail of every technical gate that a profile could make advisory."""
    req = required(tl)
    holds = hold_ranges(tl)
    waive = set(tl.gates.waive if tl.gates else ())
    reasons = waivers(tl)
    out = list(findings)
    for g in sorted(waive):
        if g in req or not is_style(g, tl):
            why = "required by the channel" if g in req else "a technical gate"
            hint = f"; use gates.waive_with_reason (with a reason) for {g}" if g in WAIVABLE_WITH_REASON else ""
            out.append(Finding("GW01", "warn", f"project waive of {g} ignored: {g} is {why}{hint}"))
    for f in findings:
        # Negative-space detection emits advisory levels directly. An explicit requirement
        # must still win over local intent and project waivers, including in draft mode.
        if f.gate == "R38space" and f.gate in req and f.level in ("warn", "info"):
            f.level = "fail"
            f.waived = None
            f.message += "; R38space explicitly required: local negative-space intent does not waive this requirement"
        if f.level not in ("fail", "warn"):
            continue
        if f.gate in reasons and f.gate not in req:
            f.level, f.waived = "info", f"{f.level}: waived by the project with a reason (gates.waive_with_reason): {reasons[f.gate]}"
            continue
        if draft and f.level == "fail" and not is_style(f.gate, tl) and f.gate not in profiles.NEVER_ADVISORY:
            f.level, f.waived = "warn", "fail: technical gate lowered in a draft build (--mode draft)"
            continue
        if not is_style(f.gate, tl) or f.gate in req:
            continue
        orig = f.level
        if f.gate in waive:
            f.level, f.waived = "info", f"{orig}: waived by the project (gates.waive)"
        elif f.gate in HOLD_GATES and f.frames and any(a <= f.frames[0] < b for a, b in holds):
            f.level, f.waived = "info", f"{orig}: hold \"read\" intent at frame {f.frames[0]}"
        elif orig == "fail":
            f.level, f.waived = "warn", "fail: style gate not required by the channel"
    return out


def summary(findings: list[Finding], tl: Timeline) -> dict:
    """build_report.json `gates`: fails, warns and waived (lowered) findings listed separately."""
    def row(f: Finding) -> dict:
        return {"gate": f.gate, "class": "style" if is_style(f.gate, tl) else "technical", "level": f.level,
                "message": f.message, **({"seg": f.seg} if f.seg is not None else {}),
                **({"frames": list(f.frames)} if f.frames else {}), **({"waived": f.waived} if f.waived else {})}
    reasons = waivers(tl)
    return {"required": sorted(required(tl)),
            "segment_intents": [{"seg": s.seg_index,
                                  **({"intent": s.intent.model_dump()} if s.intent else {}),
                                  **({"hold": s.hold} if s.hold else {})}
                                 for s in tl.segments if s.intent or s.hold],
            "fails": [row(f) for f in findings if f.level == "fail"],
            "warns": [row(f) for f in findings if f.level == "warn"],
            "waived": [row(f) for f in findings if f.waived],
            **({"waivers": [{"gate": g, "reason": r} for g, r in sorted(reasons.items())]} if reasons else {})}
