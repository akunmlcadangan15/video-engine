"""Profile resolution layers: `extends`, the [type] cascade, creative-rule placeholders, the caption_style key check,
key origins and the load memo. Labelled GOOD and BAD cases (default < extends chain < include < channel)."""
import json
import os
import re
import tomllib

import pytest

from video_engine import config, profile_schema, profiles
from video_engine.util import EngineError

DEFAULT_TOML = (config.PROFILES_DIR / "default.toml").read_text()


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    """Isolated profiles dir holding the real default.toml; write(rel, text) adds any file below it."""
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text(DEFAULT_TOML)
    monkeypatch.setattr(config, "PROFILES_DIR", d)

    def write(rel, text):
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f
    return write


# ------------------------------------------------------------------------------------------------------ extends
def test_good_extends_layers_base_includes_base_child_includes_child(pdir):
    pdir("base.toml", 'description = "base"\ninclude = ["base/a.toml"]\n[watermark]\ntext = "Base"\npx = 40\n'
                      '[theme]\nroute_colour = "#111111"\n')
    pdir("base/a.toml", '[theme]\nroute_colour = "#000000"\nroute_width_px = 9\n[audio]\ntarget_lufs = -13.0\n')
    pdir("child.toml", 'description = "child"\nextends = "base"\ninclude = ["child/a.toml"]\n[watermark]\npx = 28\n')
    pdir("child/a.toml", '[theme]\nroute_width_px = 11\n')
    base, child = profiles.load("base"), profiles.load("child")
    assert (child.theme.route_colour, child.theme.route_width_px) == ("#111111", 11)       # base file > base include
    assert (child.watermark.text, child.watermark.px) == ("Base", 28) and child.audio.target_lufs == -13.0
    assert child.data.description == "child" and child.blake3 != base.blake3
    pdir("same.toml", 'extends = "base"\n')                                               # nothing changed
    assert profiles.load("same").blake3 == base.blake3


def test_good_flatten_of_an_extending_profile_is_self_contained(pdir):
    pdir("base.toml", 'include = ["base/a.toml"]\n[watermark]\ntext = "Base"\n')
    pdir("base/a.toml", '[audio]\ntarget_lufs = -13.0\n')
    pdir("child.toml", 'extends = "base"\n[watermark]\npx = 28\n')
    flat = profiles.flatten("child")
    assert "extends" not in flat and "include" not in flat and flat.count("[watermark]") == 1
    pdir("flat.toml", flat)
    assert profiles.load("flat").blake3 == profiles.load("child").blake3


@pytest.mark.parametrize("files, msg", [
    ({"a.toml": 'extends = "b"\n', "b.toml": 'extends = "a"\n'}, "loops back to 'a'"),
    ({"a.toml": 'extends = "a"\n'}, "loops back to 'a'"),
    ({"a.toml": 'extends = "nope"\n'}, "names profile 'nope', which is not found"),
    ({"a.toml": 'extends = "default"\n'}, "already builds on the default profile"),
    ({"a.toml": 'extends = ["b"]\n'}, "must be the name of another profile"),
    ({"a.toml": 'include = ["x/p.toml"]\n', "x/p.toml": 'extends = "b"\n'}, "cannot include other files"),
])
def test_bad_extends(pdir, files, msg):
    for rel, text in files.items():
        pdir(rel, text)
    with pytest.raises(EngineError, match=re.escape(msg)):
        profiles.load("a")


def test_bad_default_profile_cannot_extend(pdir):
    pdir("default.toml", 'extends = "x"\n' + DEFAULT_TOML)
    with pytest.raises(EngineError, match="the default profile cannot `extends`"):
        profiles.load()


# -------------------------------------------------------------------------------------------------- type cascade
def test_good_type_sizes_reach_the_surfaces_the_channel_leaves_open(pdir):
    pdir("chan.toml", '[type]\ntitle_px = 120\npin_label_px = 60\ntitle_px_paper = 90\n[layout.grid]\nunit = 8\n'
                      '[print]\npaper_min_y = 0.5\n')
    p = profiles.load("chan")
    assert (p.theme.title_px, p.theme.pin_label_px) == (120, 60)
    assert (p.layout.sizes.title, p.layout.sizes.pin_label, p.print.title_px) == (120, 60, 90)


def test_good_a_surface_key_the_channel_writes_wins_and_absent_tables_stay_absent(pdir):
    pdir("chan.toml", '[type]\ntitle_px = 120\n[theme]\ntitle_px = 100\n')
    p = profiles.load("chan")
    assert p.theme.title_px == 100 and p.layout is None and p.print is None
    pdir("plain.toml", '[theme]\ntitle_px = 100\n')              # no [type] in the channel: nothing cascades
    assert profiles.load("plain").theme.title_px == 100 and profiles.load().theme.title_px == 96


def test_good_geoverum_sets_the_title_size_once():
    """[type] title_px is the one place: theme and the portrait layout follow it, the landscape layout sets its own."""
    core = tomllib.loads((config.PROFILES_DIR / "geoverum" / "core.toml").read_text())
    layout = tomllib.loads((config.PROFILES_DIR / "geoverum" / "layout.toml").read_text())
    assert core["type"] == {"title_px": 104} and "title_px" not in core["theme"] and "title" not in layout["layout"]["sizes"]
    p = profiles.load("geoverum", orientation="portrait")
    wide = profiles.load("geoverum", orientation="landscape")
    assert p.theme.title_px == p.layout.sizes.title == 104 and (wide.theme.title_px, wide.layout.sizes.title) == (104, 88)
    assert p.blake3 != wide.blake3


def test_bad_a_cascaded_size_is_validated_by_its_surface(pdir):
    pdir("chan.toml", '[type]\ntitle_px_paper = 60\n[print]\npaper_min_y = 0.5\n')     # print.title_px >= 84
    with pytest.raises(EngineError, match="print.title_px"):
        profiles.load("chan")


# ------------------------------------------------------------------------------------- creative rule placeholders
RULES_TOML = '[draft]\nmin_seg_s = 1.5\nmax_seg_s = 4.0\nrules_file = "chan/creative.md"\n[caption_style]\nsize = {size}\n'


def test_good_placeholders_take_the_effective_profile_values(pdir):
    pdir("chan/creative.md", "- captions at {{caption_style.size}} px, title {{ theme.title_px }} px, "
                             "drift {{theme.camera_drift_per_s}}, pins {{theme.pin_style}}, box {{caption_style.auto_place}}\n"
                             "- no placeholder here\n")
    pdir("chan.toml", RULES_TOML.format(size=44))
    pdir("wide.toml", 'extends = "chan"\n[caption_style]\nsize = 58\n')
    assert profiles.load("chan").draft.rules == ["captions at 44 px, title 96 px, drift 0.012, pins dot, box true",
                                                 "no placeholder here"]
    assert profiles.load("wide").draft.rules[0].startswith("captions at 58 px")       # the same file, its own value
    assert profiles._creative_rules("chan", "chan/creative.md")[0].startswith("captions at {{caption_style.size}}")
    assert tomllib.loads(profiles.flatten("wide"))["draft"]["rules"] == profiles.load("wide").draft.rules


@pytest.mark.parametrize("ref, msg", [("{{caption_style.sise}}", "not a key of the effective profile"),
                                      ("{{theme}}.x {{watermark.text}}", "not a key of the effective profile"),
                                      ("{{grade}}", "not set in this profile")])
def test_bad_placeholder_that_names_no_value(pdir, ref, msg):
    pdir("chan/creative.md", f"- size {ref}\n")
    pdir("chan.toml", RULES_TOML.format(size=44))
    with pytest.raises(EngineError, match=msg):
        profiles.load("chan")


def test_good_an_edited_rules_file_is_a_new_profile_in_the_same_process(pdir):
    f = pdir("chan/creative.md", "- first\n")
    pdir("chan.toml", RULES_TOML.format(size=44))
    a = profiles.load("chan")
    f.write_text("- first\n- second\n")
    b = profiles.load("chan")
    assert a.draft.rules == ["first"] and b.draft.rules == ["first", "second"] and a.blake3 != b.blake3


# ------------------------------------------------------------------------------------------- caption_style keys
@pytest.mark.parametrize("line, msg", [
    ("fixd_y = 1400", r"caption_style\.fixd_y: unknown key \(did you mean fixed_y\?\)"),
    ('size = "huge"', r"caption_style\.size must be a number"),
    ("bold = 1", r"caption_style\.bold must be true or false"),
    ("max_words = 2.5", r"caption_style\.max_words must be a whole number"),
    ('slot = "top"', r"caption_style\.slot must be one of lower, mid, upper"),
    ("font = 12", r"caption_style\.font must be text"),
])
def test_bad_caption_style_key_or_type_fails_at_load(pdir, line, msg):
    pdir("chan.toml", f"[caption_style]\n{line}\n")
    with pytest.raises(EngineError, match="profile 'chan': " + msg):
        profiles.load("chan")


def test_good_caption_key_tables_match_the_rust_style_and_the_python_reader():
    """The key tables are the Rust `style_from` list and the keys captions.py handles: neither side has a key the
    check does not know."""
    from video_engine import captions
    rust = (config.ENGINE_ROOT / "rust" / "hypoverum_core_rs" / "src" / "lib.rs").read_text()
    known = re.search(r"fn style_from.*?let known = \[(.*?)\];", rust, re.S).group(1)
    assert sorted(re.findall(r'"(\w+)"', known)) == sorted(profile_schema.CAPTION_RUST_KEYS)
    assert captions.PY_KEYS == profile_schema.CAPTION_PY_KEYS
    for name in profiles.names():
        for o in ("portrait", "landscape"):
            assert set(profiles.load(name, orientation=o).caption_style) <= set(captions.PY_KEYS) | set(profile_schema.CAPTION_RUST_KEYS)


def test_bad_reference_style_typo_fails_at_load(pdir):
    pdir("chan.toml", "[reference_style.overlap]\nalowed = true\n")
    with pytest.raises(EngineError, match="alowed"):
        profiles.load("chan")
    pdir("chan.toml", "[reference_style.overlaps]\nallowed = true\n")
    with pytest.raises(EngineError, match="overlaps"):
        profiles.load("chan")
    pdir("chan.toml", "[reference_style.overlap]\nallowed = true\n")
    assert profiles.load("chan").reference_style.overlap_allowed and not profiles.load().reference_style.overlap_allowed


# ------------------------------------------------------------------------------------------------------ origins
def test_good_origins_name_the_file_that_set_each_key(pdir):
    pdir("chan.toml", 'include = ["chan/a.toml"]\n[type]\ntitle_px = 120\n[theme]\nroute_colour = "#111111"\n')
    pdir("chan/a.toml", '[theme]\nroute_colour = "#000000"\nroute_width_px = 9\n')
    o = profiles.origins("chan")
    assert o["theme.route_colour"] == "chan.toml" and o["theme.route_width_px"] == "chan/a.toml"
    assert o["theme.whip_s"] == "default.toml" and o["camera.max_zoom"] == profiles.ENGINE_ORIGIN
    assert o["theme.title_px"] == "[type] title_px (chan.toml)"
    assert set(o) == set(profiles.origins()) | {k for k in o if k not in profiles.origins()}


def test_good_repository_profiles_have_an_origin_for_every_key():
    for name in profiles.names():
        for o in (None, "portrait", "landscape"):
            orig = profiles.origins(name, orientation=o)
            assert orig and all(orig.values())
    geo = profiles.origins("geoverum")
    assert geo["caption_style.font"] == "geoverum/core.toml" and geo["caption_style.portrait.fixed_y"] == "geoverum.toml"
    assert geo["caption_style.landscape.fixed_y"] == "geoverum.toml"
    assert profiles.origins("geoverum", orientation="portrait")["caption_style.fixed_y"] == "geoverum.toml"
    assert profiles.origins("geoverum", orientation="landscape")["caption_style.fixed_y"] == "geoverum.toml"


# --------------------------------------------------------------------------------------------------------- memo
def test_good_unchanged_files_are_served_from_the_memo_and_a_touched_file_is_read_again(pdir, monkeypatch):
    f = pdir("chan.toml", '[watermark]\ntext = "One"\n')
    old = 1_600_000_000
    for p in (f, f.parent / "default.toml"):
        os.utime(p, (old, old))                                  # settled timestamps: the memo may trust them
    first = profiles.load("chan")
    monkeypatch.setattr(profiles, "_layers", lambda name: pytest.fail("files were read again"))
    assert profiles.load("chan") is first                        # stat only
    monkeypatch.undo()
    monkeypatch.setattr(config, "PROFILES_DIR", f.parent)
    f.write_text('[watermark]\ntext = "Two"\n')
    os.utime(f, (old + 5, old + 5))
    assert profiles.load("chan").watermark.text == "Two"


def test_good_same_name_in_another_profiles_dir_is_another_profile(pdir, tmp_path, monkeypatch):
    pdir("chan.toml", '[watermark]\ntext = "One"\n')
    assert profiles.load("chan").watermark.text == "One"
    other = tmp_path / "other"
    other.mkdir()
    (other / "default.toml").write_text(DEFAULT_TOML)
    (other / "chan.toml").write_text('[watermark]\ntext = "Other"\n')
    monkeypatch.setattr(config, "PROFILES_DIR", other)
    assert profiles.load("chan").watermark.text == "Other"


# ------------------------------------------------------------------------------------------------ default.toml
def test_good_default_toml_states_the_code_defaults_it_repeats():
    """default.toml documents the engine values; where a model field has a default, the two must agree (one value,
    written twice only so the file reads as the full default profile)."""
    table = tomllib.loads(DEFAULT_TOML)
    minimal = json.loads(profiles.ProfileData.model_validate(
        {k: table[k] for k in ("audio", "draft", "theme")}).model_dump_json(by_alias=True))
    written = json.loads(profiles.ProfileData.model_validate(table).model_dump_json(by_alias=True))
    own = ("audio", "draft", "theme", "description", "caption_style")   # tables only default.toml defines
    drift = {k: (minimal[k], written[k]) for k in written if k not in own and minimal[k] != written[k]}
    assert drift == {}


# --------------------------------------------------------------------------------------------- multi-orientation
def test_good_dual_orientation_isolates_hashes_and_resolves_all_tables(pdir):
    body = (
        '[caption_style]\nfont = "Inter"\n'
        '[caption_style.portrait]\nsize = 48\nfixed_y = 1366\n'
        '[caption_style.landscape]\nsize = 58\nfixed_y = 900\n'
        '[watermark]\ntext = "Brand"\n'
        '[watermark.portrait]\npx = 40\n'
        '[watermark.landscape]\npx = 28\ncorner = "bottom_right"\n'
        '[layout.portrait.sizes]\ntitle = 104\n'
        '[layout.landscape.sizes]\ntitle = 88\n'
        '[print]\ntitle_px = 88\n'
        '[print.portrait]\nyear_cx = 540\nyear_cy = 560\nheading_baseline_y = 384\n'
        '[print.landscape]\nyear_cx = 960\nyear_cy = 380\nheading_baseline_y = 150\n'
    )
    pdir("dual.toml", body)
    p_port = profiles.load("dual", orientation="portrait")
    p_land = profiles.load("dual", orientation="landscape")
    assert (p_port.caption_style["size"], p_port.caption_style["fixed_y"]) == (48, 1366)
    assert (p_land.caption_style["size"], p_land.caption_style["fixed_y"]) == (58, 900)
    assert (p_port.watermark.px, p_port.watermark.corner) == (40, None)
    assert (p_land.watermark.px, p_land.watermark.corner) == (28, "bottom_right")
    assert p_port.layout.sizes.title == 104 and p_land.layout.sizes.title == 88
    assert p_port.print.year_cy == 560 and p_land.print.year_cy == 380

    # Changing landscape-only values changes unoriented and landscape hashes, never portrait hash.
    pdir("dual.toml", body.replace("px = 28", "px = 32"))
    assert profiles.load("dual", orientation="portrait").blake3 == p_port.blake3
    assert profiles.load("dual", orientation="landscape").blake3 != p_land.blake3


def test_bad_dual_orientation_invalid_branch_rejected_on_unoriented_load(pdir):
    pdir("badprint.toml", '[print]\ntitle_px = 88\n[print.landscape]\nyear_cy = 560\n')
    with pytest.raises(EngineError, match="landscape range"):
        profiles.load("badprint")

    pdir("badcap.toml", '[caption_style.landscape]\nfixed_y = 1382\n')
    with pytest.raises(EngineError, match=r"0\.\.1080"):
        profiles.load("badcap")

    pdir("badtype.toml", '[caption_style]\nportrait = 123\n')
    with pytest.raises(EngineError, match="caption_style.portrait must be a table"):
        profiles.load("badtype")


def test_good_cli_profile_orientation_flag(capsys):
    from video_engine import cli
    assert cli.main(["profile", "geoverum", "--orientation", "landscape", "--table", "caption_style"]) == 0
    out = json.loads(capsys.readouterr().out)["caption_style"]
    assert out["size"] == 58 and out["fixed_y"] == 900
    assert "portrait" not in out and "landscape" not in out
    with pytest.raises(SystemExit) as e:
        cli.main(["profile", "geoverum", "--orientation", "square"])
    assert e.value.code == 2
