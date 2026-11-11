"""Geoverum portrait vs landscape must stay in step.

`geoverum_landscape.toml` repeats the channel tables of `geoverum.toml` because an included file cannot
include another file (see the comment at the top of that file). The two profiles are allowed to differ only in
orientation-specific tables; every other table must be identical, otherwise a channel change applied to one
profile silently misses the other. Allowed differences:
  - caption_style, description, format (orientation), watermark (position/size)
  - gates.duration (landscape allows longer videos)
  - layout, print (landscape geometry overrides from profiles/geoverum/landscape/)
"""
import json

from video_engine import profiles

ALLOWED = {"caption_style", "description", "format", "gates", "layout", "print", "watermark"}


def test_good_geoverum_landscape_differs_only_in_orientation_tables():
    a = json.loads(profiles.load("geoverum").data.model_dump_json())
    b = json.loads(profiles.load("geoverum_landscape").data.model_dump_json())
    # the two formats share one creative.md and each quotes its own caption and title sizes ({{table.key}})
    assert a["draft"].pop("rules") != b["draft"].pop("rules") and a["draft"] == b["draft"]
    drift = sorted(k for k in set(a) | set(b) if k not in ALLOWED and a.get(k) != b.get(k))
    assert drift == [], f"shared tables drifted between geoverum and geoverum_landscape: {drift}"


def test_good_geoverum_landscape_gates_differ_only_in_duration():
    a = json.loads(profiles.load("geoverum").data.model_dump_json())["gates"]
    b = json.loads(profiles.load("geoverum_landscape").data.model_dump_json())["gates"]
    drift = sorted(k for k in set(a) | set(b) if k != "duration" and a.get(k) != b.get(k))
    assert drift == [], f"gate tables drifted (only duration may differ): {drift}"
