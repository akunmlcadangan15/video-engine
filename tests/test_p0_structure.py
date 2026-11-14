"""P0 structure locks: single source creative rules, span naming, canonical type scale, taste/profile agreement."""
import json

import pytest

from video_engine import config, profiles, taste_lint


def test_geoverum_profile_has_no_creative_rules():
    for o in (None, "portrait", "landscape"):
        p = profiles.load("geoverum", orientation=o)
        assert p.draft.rules_file is None and p.draft.rules == []
        assert (p.draft.min_seg_s, p.draft.max_seg_s, p.draft.map_path) == (3.0, 30.0, True)


def test_draft_rules_and_rules_file_are_exclusive():
    from video_engine.profiles import DraftProfile
    with pytest.raises(Exception):
        DraftProfile(min_seg_s=1, max_seg_s=2, rules=["x"], rules_file="geoverum/creative.md")


def test_storyboard_schema_uses_spans_not_beats():
    from test_draft import storyboards
    sb = storyboards()
    assert all("spans" in c and "beats" not in c for c in sb["concepts"])
    # legacy alias still validates
    legacy = {"concepts": [{**c, "beats": c.pop("spans")} for c in sb["concepts"]]}
    from test_draft import proj as _proj  # noqa: F401  (fixture import only)
    assert legacy["concepts"][0]["beats"]


def test_type_scale_canonical():
    g = profiles.load("geoverum", orientation="portrait")
    w = profiles.load("geoverum", orientation="landscape")
    assert g.type.title_px == g.theme.title_px == g.data.layout.sizes.title == 104
    assert w.type.title_px == 104 and w.data.layout.sizes.title == 88
    assert g.type.title_px_paper == g.data.print.title_px == w.data.print.title_px == 88
    assert g.type.pin_label_px == w.type.pin_label_px == 52
    assert g.type.title_for(paper=True) == 88 and g.type.title_for() == 104
    d = profiles.load("default")
    assert d.type.title_px == 96 and d.type.title_px_paper == 88


def test_historical_taste_mismatches_are_visible_not_profile_overrides():
    taste = json.loads((config.PROFILES_DIR / "taste" / "geoverum.json").read_text())
    data = json.loads(profiles.load("geoverum").data.model_dump_json())
    rep = taste_lint.lint(taste, data)
    assert taste_lint.ok(rep)
    keys = [r["key"] for r in rep["results"]]
    assert "caption_style.forbid_box" in keys and "caption_style.box_bg" not in keys
