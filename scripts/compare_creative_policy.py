#!/usr/bin/env python3
"""Read-only shadow comparison of legacy R38/R56px pixel heuristics and current policy.

Reconstructs only the old blocking decisions from the same measured findings/thresholds.
It does NOT run the old whole engine, change the project, render or make a quality judgment.
Usage: python scripts/compare_creative_policy.py PROJECT VIDEO [--out REPORT.json]
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_engine import qa, timeline  # noqa: E402
from video_engine.gates import severity  # noqa: E402


def compare(tl, findings, measurements):
    """Compare per-segment blocking, not finding counts (new findings may be aggregated)."""
    legacy_blocked, current_blocked = set(), set()
    for f in findings:
        if ((f.gate == "R38" and f.level == "fail") or f.gate == "R38space"
                or (f.gate == "R56px" and "no visible motion" in f.message)):
            legacy_blocked.add(f.seg)
        if f.level == "fail":
            current_blocked.add(f.seg)
    return {
        "scope": "R38/R38space/R56px pixel heuristics only; legacy decisions reconstructed, not a full old-engine run",
        "human_quality_review": "not performed; fewer blocked segments is not proof of better quality",
        "legacy_blocking_segments": sorted(s for s in legacy_blocked if s is not None),
        "current_blocking_segments": sorted(s for s in current_blocked if s is not None),
        "newly_unblocked_segments": sorted(s for s in legacy_blocked - current_blocked if s is not None),
        "newly_blocked_segments": sorted(s for s in current_blocked - legacy_blocked if s is not None),
        "measurements": measurements,
        "segments": [{"seg": s.seg_index, "intent": s.intent.model_dump(exclude_none=True) if s.intent else None,
                      "hold": s.hold} for s in tl.segments],
        "findings": [asdict(f) for f in findings],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", type=Path)
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    tl = timeline.load(args.project)
    findings, measurements = qa.pixel_gates(args.video, tl)
    result = compare(tl, severity.apply(findings, tl), measurements)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if result["current_blocking_segments"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
