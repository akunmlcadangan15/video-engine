"""Profile `include`: a channel profile can pull in other .toml files of the profiles directory. Merge order:
default < included files (in list order) < the channel file. Labelled GOOD and BAD cases."""
import tomllib

import pytest

from video_engine import config, profiles
from video_engine.util import EngineError

DEFAULT_TOML = (config.PROFILES_DIR / "default.toml").read_text()

INLINE = """description = "inline"
[audio]
target_lufs = -13.0
[theme.entities]
Indonesia = "#B56A95"
[watermark]
text = "Chan"
"""
MAIN = """description = "inline"
include = ["chan/audio.toml", "chan/entities.toml"]
[watermark]
text = "Chan"
"""


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


def split(write, main=MAIN):
    write("chan.toml", main)
    write("chan/audio.toml", "[audio]\ntarget_lufs = -13.0\n")
    write("chan/entities.toml", '[theme.entities]\nIndonesia = "#B56A95"\n')


# ----------------------------------------------------------------------------------------------------------- good
def test_good_split_profile_equals_the_inline_profile_down_to_the_hash(pdir):
    pdir("inline.toml", INLINE)
    split(pdir)
    a, b = profiles.load("inline"), profiles.load("chan")
    assert b.data.model_dump() == {**a.data.model_dump()} and a.blake3 == b.blake3
    assert b.data.theme.entities == {"Indonesia": "#B56A95"} and b.audio.target_lufs == -13.0


def test_good_channel_file_wins_over_included_files_and_later_includes_win_over_earlier(pdir):
    pdir("chan.toml", 'include = ["x/a.toml", "x/b.toml"]\n[audio]\ntarget_lufs = -12.0\n')
    pdir("x/a.toml", "[audio]\ntarget_lufs = -13.0\nlra = 9.0\n")
    pdir("x/b.toml", "[audio]\nlra = 8.0\n")
    a = profiles.load("chan").audio
    assert (a.target_lufs, a.lra) == (-12.0, 8.0)          # channel > b > a


def test_good_editing_an_included_file_changes_the_loaded_profile(pdir):
    split(pdir)
    assert profiles.load("chan").audio.target_lufs == -13.0
    pdir("chan/audio.toml", "[audio]\ntarget_lufs = -12.5\n")
    assert profiles.load("chan").audio.target_lufs == -12.5      # the parse cache is keyed by file content


def test_good_a_profile_without_include_loads_as_before(pdir):
    pdir("plain.toml", INLINE)
    assert profiles.load("plain").watermark.text == "Chan"
    assert profiles.flatten("plain") == INLINE                  # nothing to inline


def test_good_flatten_inlines_the_included_files_and_parses_to_the_same_profile(pdir):
    split(pdir)
    flat = profiles.flatten("chan")
    assert "include" not in flat.split("[", 1)[0] and "[theme.entities]" in flat and "[audio]" in flat
    pdir("flat.toml", flat)
    assert profiles.load("flat").blake3 == profiles.load("chan").blake3


def test_good_flatten_inlines_rules_file_text_with_unicode_and_backslashes(pdir):
    """Flattened rules_file text parses back to the same rules, including unicode and backslashes."""
    rules = [
        "equals sign struck through into ≠",
        r"C:\users\geo \u2260 \1 \g<name>",
        'say "hello" then \\',
    ]
    pdir("chan/creative.md", "".join(f"- {rule}\n" for rule in rules))
    pdir("chan/extra.toml", '[watermark]\ntext = "Chan"\n')
    pdir("chan.toml", 'include = ["chan/extra.toml"]\n[draft]\nmin_seg_s = 1.5\nmax_seg_s = 4.0\nrules_file = "chan/creative.md"\n')
    flat = profiles.flatten("chan")
    assert tomllib.loads(flat)["draft"]["rules"] == rules
    pdir("flat.toml", flat)
    assert profiles.load("flat").draft.rules == rules
    assert profiles.load("chan").draft.rules == rules


def test_good_repository_geoverum_flatten_keeps_pure_brand_structure():
    flat = profiles.flatten("geoverum")
    raw = tomllib.loads(flat)
    assert "rules" not in raw["draft"] and "rules_file" not in raw["draft"]
    assert raw["caption_style"]["portrait"]["fixed_y"] == 1366
    assert raw["caption_style"]["landscape"]["fixed_y"] == 900
    assert profiles.load("geoverum").draft.rules == []


def test_good_rules_file_overrides_keep_resolved_rules_and_do_not_leak(pdir):
    """A rules_file profile accepts legal overrides. Resolved rules stay, and one call does not change another."""
    pdir("chan/creative.md", "- Default only ≠ optional\n- Max zoom {{camera.max_zoom}}\n")
    pdir("chan.toml", '[camera]\nmax_zoom = 8.0\n[draft]\nmin_seg_s = 1.5\nmax_seg_s = 4.0\nrules_file = "chan/creative.md"\n')
    base = profiles.load("chan")
    rules = list(base.draft.rules)
    assert base.draft.rules_file == "chan/creative.md" and any("≠" in r for r in rules)
    assert any("Max zoom 8" in r for r in rules)
    a = profiles.load("chan", {"camera": {"max_zoom": 10.0}})
    b = profiles.load("chan", {"theme": {"title_px": 96.0}})
    assert a.draft.rules == rules and b.draft.rules == rules
    assert a.draft.rules_file == b.draft.rules_file == "chan/creative.md"
    assert base.draft.rules == rules and profiles.load("chan").camera.max_zoom == 8.0
    assert profiles.load("chan").blake3 == base.blake3


def test_good_geoverum_overrides_do_not_leak():
    """Legal overrides on geoverum stay isolated across calls and orientations."""
    g_base = profiles.load("geoverum")
    indo = g_base.data.theme.entities["Indonesia"]
    ga = profiles.load("geoverum", {"theme": {"entities": {"Indonesia": "#010203"}}})
    gb = profiles.load("geoverum", {"theme": {"entities": {"Sekutu": "#445566"}}})
    assert ga.data.theme.entities["Indonesia"] == "#010203"
    assert ga.data.theme.entities["Sekutu"] == g_base.data.theme.entities["Sekutu"]
    assert gb.data.theme.entities["Sekutu"] == "#445566" and gb.data.theme.entities["Indonesia"] == indo
    assert g_base.data.theme.entities["Indonesia"] == indo and profiles.load("geoverum").blake3 == g_base.blake3


def test_good_overrides_keep_unicode_and_backslash_rules(pdir):
    rules = [
        "equals sign struck through into ≠",
        r"C:\users\geo \u2260 \1 \g<name>",
        'say "hello" then \\',
    ]
    pdir("ov/creative.md", "".join(f"- {rule}\n" for rule in rules))
    pdir("ov.toml", '[draft]\nmin_seg_s = 1.5\nmax_seg_s = 4.0\nrules_file = "ov/creative.md"\n')
    base = profiles.load("ov")
    over = profiles.load("ov", {"theme": {"entities": {"Indonesia": "#010203"}}})
    assert over.draft.rules == rules == base.draft.rules
    assert over.draft.rules_file == "ov/creative.md"
    assert over.data.theme.entities["Indonesia"] == "#010203"
    assert base.data.theme.entities.get("Indonesia") != "#010203"


def test_bad_overrides_reject_unknown_tables_and_illegal_colours():
    with pytest.raises(EngineError, match="cannot be overridden"):
        profiles.load("geoverum", {"draft": {"rules": ["nope"]}})
    with pytest.raises(EngineError, match="theme colour"):
        profiles.load("geoverum", {"theme": {"entities": {"Indonesia": "red"}}})


def test_bad_profile_file_with_rules_and_rules_file_is_rejected(pdir):
    pdir("both/creative.md", "- from the file\n")
    pdir("both.toml", '[draft]\nmin_seg_s = 1.5\nmax_seg_s = 4.0\nrules = ["inline"]\n'
                      'rules_file = "both/creative.md"\n')
    with pytest.raises(EngineError, match="mutually exclusive"):
        profiles.load("both")


def test_good_repository_geoverum_loads_from_its_include_files():
    repo = config.PROFILES_DIR
    text = profiles.path_of("geoverum").read_text()
    assert 'include = ["geoverum/' in text and (repo / "geoverum" / "layout.toml").is_file()
    flat = profiles.flatten("geoverum")
    assert "[layout.fly]" in flat and "[legibility]" in flat and "[print]" in flat and "[sound.events]" in flat
    assert "[theme.entities]" in flat and "\ninclude" not in flat
    for key in ("layout", "legibility", "print", "sound"):
        assert getattr(profiles.load("geoverum"), key) is not None, key
    assert profiles.load("geoverum").data.theme.entities["Indonesia"] == "#9C443B"


# ------------------------------------------------------------------------------------------------------------ bad
@pytest.mark.parametrize("rel, msg", [("../outside.toml", "leaves the profiles directory"),
                                      ("/etc/hosts.toml", "relative path"),
                                      ("sub/file.txt", "ending in .toml"),
                                      ("sub/missing.toml", "not found")])
def test_bad_include_paths_are_rejected(pdir, rel, msg):
    pdir("chan.toml", f'include = ["{rel}"]\n')
    with pytest.raises(EngineError, match=msg):
        profiles.load("chan")


def test_bad_include_must_be_a_list_of_strings(pdir):
    for bad in ('include = "a.toml"\n', "include = [1]\n"):
        pdir("chan.toml", bad)
        with pytest.raises(EngineError, match="list of file paths"):
            profiles.load("chan")


def test_bad_an_included_file_cannot_include_other_files(pdir):
    pdir("chan.toml", 'include = ["x/a.toml"]\n')
    pdir("x/a.toml", 'include = ["x/b.toml"]\n')
    pdir("x/b.toml", "")
    with pytest.raises(EngineError, match="cannot include other files"):
        profiles.load("chan")


def test_bad_the_default_profile_cannot_include(pdir):
    # `include` has to be a top-level key, so it goes before the first table
    (config.PROFILES_DIR / "default.toml").write_text('include = ["x/a.toml"]\n' + DEFAULT_TOML)
    with pytest.raises(EngineError, match="default profile cannot"):
        profiles.load()


def test_bad_a_toml_error_in_an_included_file_names_that_file(pdir):
    pdir("chan.toml", 'include = ["x/a.toml"]\n')
    pdir("x/a.toml", "[audio\n")
    with pytest.raises(EngineError, match=r"x/a\.toml"):
        profiles.load("chan")


def test_bad_unknown_keys_in_an_included_file_are_still_rejected(pdir):
    pdir("chan.toml", 'include = ["x/a.toml"]\n')
    pdir("x/a.toml", "[audio]\nnot_a_key = 1\n")
    with pytest.raises(EngineError, match="not_a_key"):
        profiles.load("chan")


def test_good_flatten_takes_a_multi_line_include_and_merges_repeated_tables(pdir):
    """Two layers may write the same table: the flattened text has it once, the higher layer's keys winning."""
    pdir("chan.toml", 'description = "chan"\ninclude = [\n  "x/a.toml",\n  "x/b.toml",\n]\n'
                      '[caption_style]\nsize = 60\n[theme]\npalette = [\n  "#111111",\n  "#222222",\n]\n')
    pdir("x/a.toml", '[caption_style]\nsize = 40      # lower layer\nmax_words = 3\n[audio]\ntarget_lufs = -13.0\n')
    pdir("x/b.toml", '[theme]\npalette = ["#999999"]\nroute_colour = "#abcdef"\n')
    flat = profiles.flatten("chan")
    assert flat.count("[caption_style]") == flat.count("[theme]") == 1 and "include" not in flat
    table = tomllib.loads(flat)
    assert table["caption_style"] == {"size": 60, "max_words": 3} and table["theme"]["palette"] == ["#111111", "#222222"]
    pdir("flat.toml", flat)
    assert profiles.load("flat").blake3 == profiles.load("chan").blake3
