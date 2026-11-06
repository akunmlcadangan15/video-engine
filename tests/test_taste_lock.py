"""P9: the Geoverum choices stay locked in the tracked taste seed and in the profile, so the
draft director and the renderer agree (caption 84 px white without a box at about 0.72 H, automatic flag fit,
optional Stage 4, HyperFrames-First pacing without hard cut limits, and loudness)."""
import json

from video_engine import config, profiles


def _seed():
    return json.loads((config.PROFILES_DIR / "taste" / "geoverum.json").read_text())


def test_good_taste_seed_records_the_approved_choices_with_reasons():
    t = _seed()
    liked = " | ".join(e["what"] for e in t["liked"])
    rejected = " | ".join(e["what"] for e in t["rejected"])
    assert "84 px" in liked and "fit otomatis" in liked and "Stage 4" in liked and "3,0 s" in liked
    assert "−14 LUFS" in liked
    assert "92/104" in rejected and "Stage 4 sekaligus" in rejected and "pecah" in rejected
    assert all(e["why"].strip() for e in t["liked"] + t["rejected"])
    # Free-text creative rules are not stored here: the single source is profiles/geoverum/creative.md
    # (loaded via [draft] rules_file). This store keeps liked/rejected preferences with machine checks.


def test_good_geoverum_profile_matches_the_taste_lock():
    p = profiles.load("geoverum")
    cs = p.caption_style
    assert (cs["size"], cs["fixed_y"], cs["outline"]) == (84, 1417, 0)
    assert "box_bg" not in cs and cs["forbid_box"] is False   # no box by default; an explicit box is allowed
    assert abs(cs["fixed_y"] / 1920 - 0.74) < 0.005
    assert p.data.flags.fit == "auto"
    d = p.data
    assert d.draft.min_seg_s == 3.0
    assert d.gates.cuts is None and d.gates.visual_type is None
    assert d.audio.target_lufs == -14.0 and d.audio.true_peak <= -1.0


def test_good_taste_and_profile_agree_on_transitions_peak_and_caption_sizes():
    """The taste seed and geoverum.toml state the same facts across transitions, peak loudness, and caption sizes."""
    t = _seed()
    liked = " | ".join(e["what"] for e in t["liked"])
    rejected = " | ".join(e["what"] for e in t["rejected"])
    d = profiles.load("geoverum")
    # page-turn / paper transitions: rejected in the taste store, disabled in the profile
    assert "page-turn" in rejected and "parchment" not in liked and "film-strip" not in liked
    # page-turn / paper transitions rejected; map_morph is a different mechanism (satellite -> schematic
    # morph, not a paper transition) and stays enabled for historical/schematic scenes
    assert d.data.transitions.page_turn is False and d.data.transitions.map_morph is False
    # true peak: the taste store quotes the profile value
    assert "−1,5 dBTP" in liked and d.data.audio.true_peak == -1.5
    # caption size: 92 / 104 rejected, so only 84 is a profile candidate
    assert "92/104" in rejected and d.caption_style["ab_sizes"] == [84]
