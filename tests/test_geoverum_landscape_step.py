"""Geoverum unifies 9:16 portrait and 16:9 landscape under one pure-brand profile (`profiles/geoverum.toml`).
Both orientations share all brand/look/sound/draft keys identically and diverge only in orientation-scoped
geometry/safe-area branches (`caption_style`, `format`, `layout`, `print`, `watermark`)."""
import json

from video_engine import gate_tools, gate_registry as reg, profiles

ORIENTATION_TABLES = {"caption_style", "format", "layout", "print", "watermark"}


def test_good_geoverum_portrait_and_landscape_share_non_orientation_tables():
    port_p = profiles.load("geoverum", orientation="portrait")
    land_p = profiles.load("geoverum", orientation="landscape")
    assert profiles.profile_orientation(profiles.load("geoverum").data) is None
    assert profiles.profile_orientation(port_p.data) == "portrait"
    assert profiles.profile_orientation(land_p.data) == "landscape"
    assert port_p.blake3 != land_p.blake3

    a = json.loads(port_p.data.model_dump_json())
    b = json.loads(land_p.data.model_dump_json())
    drift = [k for k in a if k not in ORIENTATION_TABLES and a[k] != b[k]]
    assert drift == [], f"shared tables drifted between portrait and landscape: {drift}"
    assert a["layout"]["sizes"]["title"] == 104 and b["layout"]["sizes"]["title"] == 88
    assert (a["caption_style"]["size"], a["caption_style"]["fixed_y"]) == (48, 1366)
    assert (b["caption_style"]["size"], b["caption_style"]["fixed_y"]) == (58, 900)
    assert (a["watermark"]["px"], a["watermark"]["corner"]) == (40, None)   # portrait defaults corner to top_right
    assert (b["watermark"]["px"], b["watermark"]["corner"]) == (28, "bottom_right")


def test_good_geoverum_has_no_creative_or_duration_gates():
    for o in (None, "portrait", "landscape"):
        p = profiles.load("geoverum", orientation=o)
        assert p.draft.rules == [] and p.draft.rules_file is None
        assert p.gates.require == [] and p.gates.advisory == []
        for tbl in ("duration", "duration_exact", "hook", "camera", "novelty",
                    "keyword_return", "ending_keyword", "cta", "cuts", "visual_type"):
            assert getattr(p.gates, tbl) is None, (o, tbl)
        assert p.gates.silence is not None and p.gates.caption_contrast is not None and p.gates.geo is not None
        assert gate_tools.effect(reg.BY_ID["S01"], p) == "warns"
        assert gate_tools.effect(reg.BY_ID["G02"], p) == "warns"
