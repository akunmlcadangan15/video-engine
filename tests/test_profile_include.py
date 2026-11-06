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


def test_good_repository_geoverum_flatten_keeps_creative_rules():
    flat = profiles.flatten("geoverum")
    assert tomllib.loads(flat)["draft"]["rules"] == profiles.load("geoverum").draft.rules


def test_good_repository_geoverum_loads_from_its_include_files():
    repo = config.PROFILES_DIR
    text = profiles.path_of("geoverum").read_text()
    assert 'include = ["geoverum/' in text and (repo / "geoverum" / "layout.toml").is_file()
    flat = profiles.flatten("geoverum")
    assert "[layout.fly]" in flat and "[legibility]" in flat and "[print]" in flat and "[sound.events]" in flat
    assert "[theme.entities]" in flat and "\ninclude" not in flat
    for key in ("layout", "legibility", "print", "sound"):
        assert getattr(profiles.load("geoverum"), key) is not None, key
    assert profiles.load("geoverum").data.theme.entities["Indonesia"] == "#B56A95"


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


def test_bad_flatten_needs_a_single_line_include(pdir):
    pdir("chan.toml", 'include = [\n  "x/a.toml",\n]\n')
    pdir("x/a.toml", "")
    assert profiles.load("chan").name == "chan"                  # loading accepts a multi-line list
    with pytest.raises(EngineError, match="single-line list"):
        profiles.flatten("chan")
