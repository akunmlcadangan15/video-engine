"""R64 for plain labels on imagery: light text gets a dark glyph-halo escalation (light_halo_alpha_min..max) after
its glow + shadow rung, so bright flags, clouds or deserts under a light label no longer leave it unreadable.
Profiles without the keys keep the single light style."""
import numpy as np
import pytest

from video_engine import legibility, profiles


def _sample(rgb):
    px = np.tile(np.array(rgb, dtype=float), (400, 1))
    xy = np.stack(np.meshgrid(np.arange(20), np.arange(20)), -1).reshape(-1, 2).astype(float)
    return legibility.Sample(0.0, px, xy, (0, 0, 20, 20), px[:80].copy(), xy[:80].copy())


def test_good_default_profile_keeps_one_light_style():
    lg = profiles.load("default").data.legibility
    if lg is None:
        pytest.skip("default profile has no [legibility]")
    assert len(legibility.ladder("light", {"kind": "label"}, lg.model_dump(), "#ffffff")) == 1


def test_good_geoverum_light_ladder_escalates_with_one_dark_halo_colour():
    lg = profiles.load("geoverum").data.legibility.model_dump()
    lad = legibility.ladder("light", {"kind": "label"}, lg, "#f1e8d4")
    assert [s.halo_alpha for s in lad] == [0.0, 0.6, 0.725, 0.85, 0.85]
    assert {s.halo for s in lad} == {lg["shadow_colour"]} and {s.ink for s in lad[:-1]} == {lad[0].ink}
    assert lad[-1].ink == lg["light_halo_ink"] and lad[-1].step == 2


def test_good_white_ink_rung_lifts_contrast_over_a_bright_region():
    lg = profiles.load("geoverum").data.legibility.model_dump()
    s = _sample([245, 245, 240])
    lad = legibility.ladder("light", {"kind": "label"}, lg, "#f1e8d4")
    assert legibility.evaluate(s, lad[-1], lg).lc > legibility.evaluate(s, lad[-2], lg).lc


def test_good_bright_flag_under_a_light_label_passes_at_a_halo_rung():
    lg = profiles.load("geoverum").data.legibility.model_dump()
    s = _sample([230, 225, 215])                        # a white flag stripe / cloud under the label
    lad = legibility.ladder("light", {"kind": "label"}, lg, "#f1e8d4")
    assert not legibility.passes(legibility.evaluate(s, lad[0], lg), "label", lg)
    st = legibility._first_passing(lad, [s], "label", lg)
    assert st is not None and st.halo_alpha > 0


def test_bad_light_halo_min_above_max_is_rejected():
    raw = profiles.load("geoverum").data.legibility.model_dump()
    with pytest.raises(ValueError, match="light_halo_alpha_min"):
        profiles.Legibility(**{**raw, "light_halo_alpha_min": 0.8, "light_halo_alpha_max": 0.5})
