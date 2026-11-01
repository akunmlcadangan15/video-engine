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
    assert "84 px" in liked and "fit otomatis" in liked and "Stage 4" in liked and "0,8 s" in liked
    assert "−14 LUFS" in liked
    assert "92/104" in rejected and "Stage 4 sekaligus" in rejected and "pecah" in rejected
    assert all(e["why"].strip() for e in t["liked"] + t["rejected"])
    assert any("84 px white" in r and "1382" in r and "never a box" in r for r in t["rules"])


def test_good_geoverum_profile_matches_the_taste_lock():
    p = profiles.load("geoverum")
    cs = p.caption_style
    assert (cs["size"], cs["fixed_y"], cs["forbid_box"], cs["outline"]) == (84, 1382, True, 0)
    assert abs(cs["fixed_y"] / 1920 - 0.72) < 0.005
    assert p.data.flags.fit == "auto"
    d = p.data
    assert d.draft.min_seg_s == 0.8
    assert d.gates.cuts is None and d.gates.visual_type is None
    assert d.audio.target_lufs == -14.0 and d.audio.true_peak <= -1.0
