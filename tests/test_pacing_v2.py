"""Stage 2 pacing: Geoverum draft segment length and first-cut warn/fail thresholds come from the profile."""
from test_style_gates import layout

from video_engine import profiles
from video_engine.gates import style


def test_good_geoverum_pacing_values_are_profile_driven(project):
    _, tl = project(layout(("map", 6, None), ("photo", 18, None)))
    p = profiles.of(tl)
    assert p.draft.min_seg_s == 2.5
    assert (p.gates.cuts.first_warn_s, p.gates.cuts.first_fail_s) == (12.0, 3.0)


def test_good_first_cut_between_fail_and_warn_is_a_warning(project):
    _, tl = project(layout(("map", 6, None), ("photo", 18, None)))
    fs = style.c01_cuts(tl, profiles.of(tl).gates.cuts)
    first = next(f for f in fs if "first" in f.message)
    assert first.level == "warn" and "first hard cut at 6.00 s" in first.message


def test_bad_shot_change_before_the_profile_fail_threshold_fails(project):
    _, tl = project(layout(("map", 2, None), ("photo", 18, None)))
    fs = style.c01_cuts(tl, profiles.of(tl).gates.cuts)
    assert any(f.level == "fail" and "2.00 s" in f.message and "first" in f.message for f in fs)
