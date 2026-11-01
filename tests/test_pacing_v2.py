"""Stage 2 pacing: Geoverum draft segment length (0.8 s minimum, no cut limits) and optional C01 CutGate behaviour."""
from test_style_gates import layout

from video_engine import profiles
from video_engine.gates import style

CUTS = profiles.CutGate(warn_per_10s=0.41, fail_per_10s=1.65, first_warn_s=12.0, first_fail_s=3.0)


def test_good_geoverum_pacing_values_are_profile_driven(project):
    _, tl = project(layout(("map", 6, None), ("photo", 18, None)))
    p = profiles.of(tl)
    assert p.draft.min_seg_s == 0.8
    assert p.gates.cuts is None and p.gates.visual_type is None


def test_good_first_cut_between_fail_and_warn_is_a_warning(project):
    _, tl = project(layout(("map", 6, None), ("photo", 18, None)))
    fs = style.c01_cuts(tl, CUTS)
    first = next(f for f in fs if "first" in f.message)
    assert first.level == "warn" and "first hard cut at 6.00 s" in first.message


def test_bad_shot_change_before_the_profile_fail_threshold_fails(project):
    _, tl = project(layout(("map", 2, None), ("photo", 18, None)))
    fs = style.c01_cuts(tl, CUTS)
    assert any(f.level == "fail" and "2.00 s" in f.message and "first" in f.message for f in fs)
