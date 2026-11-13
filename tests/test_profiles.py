"""Channel profiles: loader, merge precedence, safety limits, timeline field, hashes. Labelled GOOD and BAD cases."""
import json

import pytest

from video_engine import audio, captions, config, hyperframes, native, profiles, timeline
from video_engine.util import EngineError

DEFAULT_TOML = (config.PROFILES_DIR / "default.toml").read_text()


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    """Isolated profiles dir holding the real default.toml; write(name, text) adds a channel profile."""
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text(DEFAULT_TOML)
    monkeypatch.setattr(config, "PROFILES_DIR", d)

    def write(name, text):
        (d / f"{name}.toml").write_text(text)
        return d / f"{name}.toml"
    return write


# default profile = engine values ------------------------------------------------------------------------------------
def test_good_default_profile_is_engine_values():
    p = profiles.load()
    assert p.name == "default"
    assert (p.audio.target_lufs, p.audio.true_peak, p.audio.lra, p.audio.tolerance_lu) == (-14.0, -1.5, 11.0, 0.2)
    assert p.caption_style == {"auto_place": True, "clearance": 160}


def test_good_timeline_without_profile_uses_default(project):
    _, tl = project()
    assert tl.profile is None and profiles.of(tl).name == "default"
    assert profiles.loudness(tl) == profiles.Loudness(-14.0, -1.5, 11.0, 0.2)


def test_good_old_timeline_round_trips_unchanged(project, tmp_path):
    _, tl = project()
    timeline.save(tl, tmp_path / "a.json")
    raw = json.loads((tmp_path / "a.json").read_text())
    tl2 = timeline.load(tmp_path / "a.json")
    timeline.save(tl2, tmp_path / "b.json")
    assert json.loads((tmp_path / "b.json").read_text()) == raw and "profile" not in raw


def test_good_unset_loudness_is_not_written(project, tmp_path):
    _, tl = project()
    timeline.save(tl, tmp_path / "t.json")
    ac = json.loads((tmp_path / "t.json").read_text()).get("audio_chain", {})
    assert not {"target_lufs", "true_peak", "lra"} & set(ac)


def test_good_geoverum_profile_selectable(project):
    _, tl = project(lambda d: d.update(profile="geoverum"))
    assert profiles.of(tl).name == "geoverum"


# merge precedence ---------------------------------------------------------------------------------------------------
def test_good_precedence_default_profile_project(pdir, project):
    pdir("chan", '[caption_style]\nclearance = 40\noutline = 2\n[audio]\ntarget_lufs = -15.0\n')
    _, tl = project(lambda d: d.update(profile="chan"))
    st = profiles.caption_style(tl)
    assert st["clearance"] == 40 and st["outline"] == 2 and st["auto_place"] is True      # profile over default
    assert profiles.loudness(tl).target_lufs == -15.0 and profiles.loudness(tl).true_peak == -1.5
    _, tl = project(lambda d: (d.update(profile="chan", caption_style={"outline": 4}),
                               d.setdefault("audio_chain", {}).update(target_lufs=-16.0)))
    assert profiles.caption_style(tl)["outline"] == 4 and profiles.loudness(tl).target_lufs == -16.0   # project wins
    assert captions.rust_style(tl) == {"outline": 4}


def test_bad_project_true_peak_cannot_loosen_the_global_limit(project):
    """docs/STRUCTURE.md: the true-peak safety limit may only be tightened. A project audio_chain.true_peak above the
    default profile's -1.5 dBTP used to pass straight into loudnorm and gate A01."""
    _, tl = project(lambda d: d.setdefault("audio_chain", {}).update(true_peak=-2.0))
    assert profiles.loudness(tl).true_peak == -2.0                                    # tighter: allowed
    _, tl = project(lambda d: d.setdefault("audio_chain", {}).update(true_peak=0.0))
    with pytest.raises(EngineError, match="looser than the global limit -1.5"):
        profiles.loudness(tl)


def test_good_profile_loudness_reaches_loudnorm(pdir, project):
    pdir("chan", '[audio]\ntarget_lufs = -15.0\ntrue_peak = -2.0\n')
    _, tl = project(lambda d: d.update(profile="chan"))
    assert "loudnorm=I=-15.0:TP=-2.0:LRA=11.0" in audio.norm_filter(tl, None)


def test_good_profile_tolerance_reaches_A01(pdir, project, tmp_path):
    pdir("tight", '[audio]\ntolerance_lu = 0.05\n')
    _, tl = project(lambda d: d.update(profile="tight"))
    L = profiles.loudness(tl)
    assert audio.loudness_findings({"I": -14.1, "TP": -3.0}, L.target_lufs, L.true_peak, L.tolerance_lu)[0].level == "fail"
    assert audio.loudness_findings({"I": -14.1, "TP": -3.0}, -14.0, -1.5, profiles.load().audio.tolerance_lu)[0].level == "pass"


# hash ---------------------------------------------------------------------------------------------------------------
def test_good_hash_stable_and_per_profile(pdir):
    f = pdir("chan", 'description = "a"\n')
    h = profiles.load("chan").blake3
    assert h == profiles.load("chan").blake3 and h != profiles.load().blake3
    f.write_text('description = "b"\n')
    assert profiles.load("chan").blake3 != h
    assert profiles.load("chan").report() == {"name": "chan", "blake3": profiles.load("chan").blake3}


def test_good_segment_cache_keys_follow_renderer_values_only(project):
    """Native keys differ exactly when the profile's segment encoder ([render] segment_*) differs (no other profile
    value reaches the native renderer); HyperFrames keys differ exactly when the profile theme values differ."""
    p, tl = project()
    _, tg = project(lambda d: d.update(profile="geoverum"))
    nat = lambda t: [native.plan(s, t, p).key for s in t.segments if s.engine_type == "NATIVE_RUST_FFMPEG"]
    assert profiles.of(tg).render.x264("segment") != profiles.of(tl).render.x264("segment")
    assert all(a != b for a, b in zip(nat(tl), nat(tg)))
    tg.profile = "default"
    assert nat(tg) == nat(tl)
    tg.profile = "geoverum"
    same = profiles.of(tl).render_values("hf") == profiles.of(tg).render_values("hf")
    assert (hyperframes.keys(tl.segments[2], tl, p) == hyperframes.keys(tg.segments[2], tg, p)) == same


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_unknown_profile_fails_validation(project):
    with pytest.raises(EngineError, match="profile 'nope' not found.*known: .*default"):
        project(lambda d: d.update(profile="nope"))


def test_bad_invalid_profile_name(project):
    with pytest.raises(EngineError, match="invalid"):
        project(lambda d: d.update(profile="../default"))


@pytest.mark.parametrize("text,why", [("[audio]\ntrue_peak = -1.0\n", "true_peak .* looser"),
                                      ("[audio]\ntolerance_lu = 0.5\n", "tolerance_lu .* looser")])
def test_bad_profile_loosens_global_safety_limit(pdir, text, why):
    pdir("loose", text)
    with pytest.raises(EngineError, match=why):
        profiles.load("loose")


@pytest.mark.parametrize("text,why", [("colour = 1\n", "Extra inputs"), ("[audio]\nlra = 0\n", "greater than 0"),
                                      ("[caption_style]\nclearance = -1\n", "clearance"),
                                      ("[caption_style]\nauto_place = 1\n", "auto_place"), ("[audio\n", "chan.toml")])
def test_bad_profile_file_rejected(pdir, text, why):
    pdir("chan", text)
    with pytest.raises(EngineError, match=why):
        profiles.load("chan")


# [legibility] keys with defaults --------------------------------------------------------------------------------------
LEG_DEFAULTED = ("lc_area", "water_ink_l", "water_ink_c", "water_ink_hue", "halo_width_min_em", "halo_width_max_em",
                 "halo_blur_ratio", "halo_blur_max_ratio", "counter_min_px", "counter_diff_max", "counter_open_min",
                 "region_casing_px", "leader_casing_px", "lc_line", "lc_line_fail", "line_ring_px", "line_percentile",
                 "line_mask_diff", "entity_text_l_min", "entity_text_c_scale", "glow_colour", "ladder_order",
                 "polarity_fallback", "lc_heading", "lc_note", "light_halo_alpha_min", "light_halo_alpha_max",
                 "light_halo_steps", "light_halo_colour", "light_halo_ink")


GEO_TOML = profiles.flatten("geoverum")


def _legibility_toml(drop=(), extra=""):
    """The geoverum [legibility] table without the keys in `drop`, plus `extra` lines."""
    i = GEO_TOML.index("\n[legibility]") + 1
    sec = GEO_TOML[i:GEO_TOML.index("\n[", i)].split("\n")
    keep = [ln for ln in sec if ln.split("=")[0].strip() not in drop]
    return "\n".join(keep) + "\n" + extra


def test_good_legibility_table_without_defaulted_keys_loads_with_defaults(pdir):
    pdir("chan", _legibility_toml(drop=LEG_DEFAULTED))
    pdir("geo", _legibility_toml())
    lg = profiles.load("chan").legibility.model_dump()
    geo = profiles.load("geo").legibility.model_dump()
    assert {k: lg[k] for k in LEG_DEFAULTED} == {k: profiles.Legibility.model_fields[k].default for k in LEG_DEFAULTED}
    assert {k: v for k, v in lg.items() if k not in LEG_DEFAULTED} == {k: v for k, v in geo.items() if k not in LEG_DEFAULTED}


def test_good_every_legibility_key_has_a_default():
    """No key is required: a channel lists the keys it changes and the engine baseline fills the rest."""
    assert [k for k, f in profiles.Legibility.model_fields.items() if f.is_required()] == []


def test_good_partial_legibility_table_falls_back_per_key(pdir):
    pdir("chan", "[legibility]\nlc_label = 80\n")
    lg, base = profiles.load("chan").legibility, profiles.Legibility()
    assert lg.lc_label == 80 and base.lc_label == 75
    assert lg.model_dump(exclude={"lc_label"}) == base.model_dump(exclude={"lc_label"})
    pdir("chan", _legibility_toml(drop=("lc_label",)))           # the full table minus one key: that key's baseline
    assert profiles.load("chan").legibility.lc_label == base.lc_label


def test_good_partial_sound_table_ducks_without_events(pdir):
    pdir("chan", "[sound]\nduck_db = -9.0\n")
    snd = profiles.load("chan").sound
    assert snd.duck_db == -9.0 and snd.events == {} and snd.kinds == {}


def test_bad_legibility_value_out_of_range_is_still_an_error(pdir):
    pdir("chan", "[legibility]\nlc_label = 500\n")
    with pytest.raises(EngineError, match="lc_label"):
        profiles.load("chan")


def test_bad_legibility_defaults_still_order_checked(pdir):
    pdir("chan", _legibility_toml(drop=LEG_DEFAULTED, extra="lc_line_fail = 35\n"))
    with pytest.raises(EngineError, match="lc_line_fail must not exceed legibility.lc_line"):
        profiles.load("chan")


def test_bad_strict_reference_style_framing_and_caption_style_reject_bad_keys():
    from pydantic import ValidationError
    from video_engine import profile_schema

    assert isinstance(profiles.load("geoverum").data.framing, profile_schema.FramingProfile)
    assert profile_schema.FramingProfile(portrait={"reveal": {"intensity": 1.5}})["portrait"]["reveal"]["intensity"] == 1.5
    assert isinstance(profiles.load("geoverum").data.caption_style, profile_schema.CaptionStyleProfile)
    with pytest.raises(ValidationError):
        profile_schema.ReferenceStyle(overlap={"allowed": "yes"})
    with pytest.raises(ValidationError):
        profile_schema.ReferenceStyle(unknown_group={"x": 1})
    with pytest.raises(ValidationError):
        profile_schema.FramingShotOverride(intensity=99)
    with pytest.raises(ValidationError):
        profile_schema.FramingProfile(portrait={"bogus_shot": {"intensity": 1.0}})
    with pytest.raises(ValidationError):
        profile_schema.CaptionStyleProfile(bogus_key=1)
    with pytest.raises(ValidationError):
        profile_schema.CaptionStyleProfile(place_band=[0.1, 0.2, 0.3])
