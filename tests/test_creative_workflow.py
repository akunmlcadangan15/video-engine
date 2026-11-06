"""Creative defaults remain flexible while draft intent is validated and preserved."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from video_engine import director, draft, profiles, timeline
from video_engine.ai.base import schema_errors

from test_draft import good_plan, proj  # noqa: F401 (fixture)


def test_channel_direction_distinguishes_identity_defaults_and_alternatives():
    rules = profiles.load("geoverum").draft.rules
    assert any(r.startswith("Identity:") for r in rules)
    assert any(r.startswith("Default, not a mandate:") for r in rules)
    assert any(r.startswith("Alternatives:") for r in rules)
    assert any("deliberate static shots, negative space" in r for r in rules)
    assert not any(r.startswith("Open on a moving map") for r in rules)


def test_storyboard_and_taste_treat_preferences_as_preferences(proj):
    inp = draft.collect(proj)
    taste = director.load_taste("geoverum")
    taste["rules"] = ["The camera never holds still"]
    text = director.prompt(inp, taste)
    assert "historical preferences, not universal constraints" in text
    assert "segment intent take precedence" in text
    assert "storytelling strategy, pacing and use of evidence" in text
    assert "footage-led opener" in text
    assert "Do not invent" not in director.guidance("REVIEW")  # review is evidence-led, not a style blacklist
    assert "not itself a defect" in director.guidance("REVIEW")


def test_draft_static_intent_schema_and_roundtrip(proj):
    inp = draft.collect(proj)
    plan = good_plan()
    intent = {"motion": "static", "negative_space": True, "reason": "Hold the archival evidence for reading"}
    plan["segments"][0].update(camera_preset="static", intent=intent, creative_autofix=False)
    assert not schema_errors(plan, draft.schema(inp))
    tl, _, _ = draft.assemble(inp, plan)
    assert tl.segments[0].intent.model_dump(exclude_none=True) == intent
    assert tl.segments[1].intent is None
    assert tl.segments[0].camera_move.preset == "static"
    assert tl.segments[0].camera_move.auto_intensity is False
    assert tl.segments[0].creative_autofix is False
    assert timeline.Timeline.model_validate_json(tl.model_dump_json()).segments[0].intent == tl.segments[0].intent
    req = director.review_parts(tl, inp, [], {})[0].text
    assert "Hold the archival evidence for reading" in req and "intent:" in req
    text = draft.prompt(inp)
    assert "Suggested length" in text and "creative_autofix is false by default" in text
    assert "black frames, broken sources" in text


@pytest.mark.parametrize("intent", [
    {"motion": "floating", "reason": "Unsupported motion"},
    {"motion": "static", "reason": "   "},
    {"reason": "No intent choice declared"},
    {"negative_space": "yes", "reason": "Not a boolean"},
    {"negative_space": True, "reason": "Valid purpose", "waive_all": True},
])
def test_bad_draft_intent_is_rejected_not_dropped(proj, intent):
    inp = draft.collect(proj)
    plan = copy.deepcopy(good_plan())
    plan["segments"][0]["intent"] = intent
    with pytest.raises((ValidationError, ValueError)):
        draft.assemble(inp, plan)


def test_explicit_creative_autofix_survives_draft_assembly(proj):
    inp = draft.collect(proj)
    plan = good_plan()
    plan["segments"][0]["creative_autofix"] = True
    tl, _, _ = draft.assemble(inp, plan)
    assert tl.segments[0].creative_autofix is True
    assert tl.segments[1].creative_autofix is False
