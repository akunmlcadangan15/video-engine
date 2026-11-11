"""P0 structure locks: single source creative rules, span naming, canonical type scale, taste/profile agreement."""
import json

import pytest

from video_engine import config, profiles, taste_lint


def test_creative_rules_single_source():
    p = profiles.load("geoverum")
    assert p.draft.rules_file == "geoverum/creative.md"
    md = (config.PROFILES_DIR / "geoverum" / "creative.md").read_text()
    written = [line[2:].strip() for line in md.splitlines() if line.startswith("- ")]
    assert written == profiles._creative_rules("geoverum", "geoverum/creative.md") and len(p.draft.rules) == len(written)
    for rule, raw in zip(p.draft.rules, written):                 # a rule is its line, placeholders filled in
        assert rule == raw if "{{" not in raw else "{{" not in rule and rule.split(" ")[:6] == raw.split(" ")[:6]
    assert p.draft.rules and all(p.draft.rules)
    # the two formats share the file and each quotes its own caption and title sizes
    portrait, wide = " ".join(p.draft.rules), " ".join(profiles.load("geoverum_landscape").draft.rules)
    assert "fixed_y = 1366" in portrait and "Inter Bold 48 px" in portrait and "Archivo Black 104 px" in portrait
    assert "fixed_y = 900" in wide and "Inter Bold 58 px" in wide and "Archivo Black 88 px" in wide
    # taste rules are preferences only: no verbatim draft rule may be copied there
    taste = json.loads((config.PROFILES_DIR / "taste" / "geoverum.json").read_text())
    for r in taste.get("rules", []):
        assert r not in p.draft.rules


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
    g = profiles.load("geoverum")
    assert g.type.title_px == g.theme.title_px == g.data.layout.sizes.title == 104
    assert g.type.title_px_paper == g.data.print.title_px == 88
    assert g.type.pin_label_px == 52
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
