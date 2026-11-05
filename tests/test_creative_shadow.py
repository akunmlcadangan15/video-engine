"""Shadow audit compares only the two reconstructed pixel policies, not full old builds."""
from pathlib import Path
import runpy
from types import SimpleNamespace

from video_engine.gates.core import Finding

compare = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "compare_creative_policy.py"))["compare"]
TL = SimpleNamespace(segments=[])


def test_shadow_negative_space_and_static_motion_unblock_without_quality_claim():
    findings = [Finding("R38space", "info", "intentional third", 0),
                Finding("R56px", "info", "no visible motion (mean luma diff 0.00 < 0.5)", 0)]
    result = compare(TL, findings, {"samples": 18})
    assert result["legacy_blocking_segments"] == [0]
    assert result["current_blocking_segments"] == []
    assert result["newly_unblocked_segments"] == [0]
    assert "not performed" in result["human_quality_review"]
    assert "not a full old-engine run" in result["scope"]


def test_shadow_black_and_expected_freeze_remain_blocking():
    findings = [Finding("R38", "fail", "black frame", 0),
                Finding("R56px", "fail", "no visible motion (mean luma diff 0.00 < 0.5)", 1)]
    result = compare(TL, findings, {})
    assert result["legacy_blocking_segments"] == result["current_blocking_segments"] == [0, 1]
    assert result["newly_unblocked_segments"] == []


def test_insufficient_expected_motion_is_new_proof_requirement_not_legacy_freeze():
    result = compare(TL, [Finding("R56px", "fail", "too few to verify expected motion", 0)], {})
    assert result["legacy_blocking_segments"] == []
    assert result["current_blocking_segments"] == [0]
    assert result["newly_blocked_segments"] == [0]
