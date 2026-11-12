"""Pre-flight gates. Every gate reads the timeline, project files and math only; no segment is rendered.

Layers and gates:
  layer 1 (static):  T02 timeline integrity, R07 credits, R14 licence, R44 duplicate text, L22 caption slot/timing
  layer 2 (math):    M01 source length, R56 camera motion (auto-raises intensity), R62 caption safe margins
  layer 2 (DOM):     R63 HyperFrames element margins/collisions at 3 seek times per segment
  layer 3 (visual):  R38 empty thirds and black frames on one snapshot per segment

Result levels: "fail" blocks the build, "fix" is an automatic timeline change (applied in memory; persisted by
`engine preflight --write-fixes`), "warn" does not block, "skip" means the gate had no input, or did not run when its
message starts with "not run: " (core.not_run); reports list every skip in `not_checked` (severity.not_checked).
"""
from .core import Finding, Report
from .runner import run_preflight

__all__ = ["Finding", "Report", "run_preflight"]
