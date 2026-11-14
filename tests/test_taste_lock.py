"""P9: the Geoverum choices stay locked in the tracked taste seed and in the profile, so the
draft director and the renderer agree (caption 48 px white without a box at about 0.71 H, automatic flag fit,
optional Stage 4, HyperFrames-First pacing without hard cut limits, and loudness)."""
import json

from video_engine import config, profiles


def _seed():
    return json.loads((config.PROFILES_DIR / "taste" / "geoverum.json").read_text())


def test_good_taste_seed_records_the_approved_choices_with_reasons():
    t = _seed()
    liked = " | ".join(e["what"] for e in t["liked"])
    rejected = " | ".join(e["what"] for e in t["rejected"])
    assert "48 px" in liked and "fit otomatis" in liked and "Stage 4" in liked and "3,0 s" in liked
    assert "−14 LUFS" in liked
    assert "92/104" in rejected and "Stage 4 sekaligus" in rejected and "pecah" in rejected
    assert all(e["why"].strip() for e in t["liked"] + t["rejected"])
    # Free-text creative mandates are not forced in the profile; this store keeps liked/rejected preferences
    # with machine-verified checks where applicable.


def test_good_geoverum_profile_matches_the_taste_lock():
    p_port = profiles.load("geoverum", orientation="portrait")
    p_land = profiles.load("geoverum", orientation="landscape")
    cs_port, cs_land = p_port.caption_style, p_land.caption_style
    assert (cs_port["size"], cs_port["fixed_y"], cs_port["outline"]) == (48, 1366, 0)
    assert (cs_land["size"], cs_land["fixed_y"], cs_land["outline"]) == (58, 900, 0)
    assert "box_bg" not in cs_port and cs_port["forbid_box"] is False and cs_land["forbid_box"] is False
    assert abs(cs_port["fixed_y"] / 1920 - 0.711) < 0.005
    assert p_port.data.flags.fit == "auto" and p_land.data.flags.fit == "auto"
    d = p_port.data
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
    # caption size: 84 / 92 / 104 rejected, so only 48 (the reference Shorts' caption size) is a profile candidate
    p_port = profiles.load("geoverum", orientation="portrait")
    p_land = profiles.load("geoverum", orientation="landscape")
    assert "84/92/104" in rejected and p_port.caption_style["ab_sizes"] == [48] and p_land.caption_style["ab_sizes"] == [58]
    assert d.caption_style["portrait"]["ab_sizes"] == [48] and d.caption_style["landscape"]["ab_sizes"] == [58]
