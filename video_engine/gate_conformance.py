"""Gate-to-profile conformance reporting and backward-compatible inspection helpers.

The gate architecture uses a two-tier enforcement model defined in :mod:`video_engine.gate_registry`:
- ``KERNEL_GATE_IDS``: the 8 non-negotiable integrity gates that always block a build on failure.
- ``LINT_GATE_IDS``: style, layout, margin, and collision gates that emit warnings by default unless listed in ``require``.

This module provides compatibility helpers (``check_profile``, ``check_timeline_gates``, and ``mandatory_report``)
used by ``video_engine.gate_tools`` and CLI reporting.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import gate_registry as reg


@dataclass(frozen=True)
class Issue:
    level: str      # "error" | "warn"
    gate: str
    message: str


def check_profile(p, extra_require: list[str] | None = None) -> list[Issue]:
    """Validate profile gate configuration. Known gate IDs are validated by ``profiles.StyleGates``."""
    return []


def check_timeline_gates(p, project_gates) -> list[Issue]:
    """Validate timeline-level gate overrides. Known gate IDs are validated by ``timeline.ProjectGates``."""
    return []


def mandatory_report(p) -> dict:
    """Return a structured summary of kernel, active lint, and advisory gates for a profile."""
    try:
        advisory = set(getattr(getattr(p, "gates", None), "advisory", None) or [])
    except Exception:
        advisory = set()
    known = set(reg.KNOWN_GATE_IDS)
    lint_or_known = set(reg.LINT_GATE_IDS) | known
    return {
        "mandatory_on": list(reg.KERNEL_GATE_IDS),
        "mandatory_switch_off": [],
        "profile_active": list(reg.LINT_GATE_IDS),
        "flexible_advisory": sorted(advisory & lint_or_known),
    }


def errors(issues: list[Issue]) -> list[str]:
    return [f"{i.gate}: {i.message}" for i in issues if i.level == "error"]


def warnings(issues: list[Issue]) -> list[str]:
    return [f"{i.gate}: {i.message}" for i in issues if i.level == "warn"]
