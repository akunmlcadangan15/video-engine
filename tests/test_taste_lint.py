"""Taste lint (video_engine/taste_lint.py, `engine taste CHANNEL --lint`): taste entries with a `check` are compared
with the merged profile; entries without one are only counted."""
import json

import pytest

from video_engine import cli, config, director, profiles, taste_lint
from video_engine.taste_lint import CheckError

DATA = {"caption_style": {"size": 84, "ab_sizes": [84], "forbid_box": True}, "audio": {"true_peak": -1.5},
        "gates": {"cta": {"phrases": ["subscribe"]}, "cuts": None}, "list": [10, 20]}


def entry(what, *checks, **extra):
    return {"what": what, "why": "because", **({"check": list(checks)} if checks else {}), **extra}


def c(key, op, value):
    return {"key": key, "op": op, "value": value}


# ----------------------------------------------------------------------------------------------------------- good
@pytest.mark.parametrize("check", [c("caption_style.size", "==", 84), c("caption_style.size", "!=", 92),
                                   c("audio.true_peak", "<=", -1.5), c("audio.true_peak", "<", -1.0),
                                   c("audio.true_peak", ">=", -2), c("audio.true_peak", ">", -2),
                                   c("caption_style.size", "in", [84, 92]), c("caption_style.ab_sizes", "==", [84]),
                                   c("gates.cta", "exists", True), c("gates.cuts", "exists", False),
                                   c("gates.missing", "exists", False), c("list[1]", "==", 20),
                                   c("list.0", "==", 10)])
def test_good_checks_that_hold_are_ok(check):
    assert taste_lint.evaluate(check, DATA)[0] == "ok"


@pytest.mark.parametrize("check, status", [(c("caption_style.size", "==", 92), "mismatch"),
                                           (c("audio.true_peak", "<=", -2.0), "mismatch"),
                                           (c("caption_style.size", "in", [1, 2]), "mismatch"),
                                           (c("gates.cta", "exists", False), "mismatch"),
                                           (c("caption_style.nope", "==", 1), "unknown-key"),
                                           (c("gates.cuts", "==", 1), "unknown-key"),     # None counts as nothing there
                                           (c("list[5]", "==", 1), "unknown-key"),
                                           (c("caption_style.size", "<", "text"), "mismatch")])   # incomparable types
def test_bad_checks_that_fail_say_how(check, status):
    assert taste_lint.evaluate(check, DATA)[0] == status


def test_good_lint_reports_results_unchecked_entries_and_rules():
    taste = {"rules": ["a", "b"], "liked": [entry("size", c("caption_style.size", "==", 84)), entry("free text")],
             "rejected": [entry("cta", c("gates.cta", "exists", True), c("gates.cuts", "exists", False))]}
    rep = taste_lint.lint(taste, DATA)
    assert [r["status"] for r in rep["results"]] == ["ok", "ok", "ok"] and taste_lint.ok(rep)
    assert rep["unchecked"] == ["free text"] and rep["rules"] == 2
    text = taste_lint.render(rep, "chan", "chan")
    assert "3 of 3 checks ok; 1 entries without a check and 2 free-text rules" in text
    assert text.count("[rejected] cta") == 1                      # the entry is named once, then its checks


def test_good_a_single_check_object_is_accepted_like_a_list():
    taste = {"liked": [{"what": "w", "why": "y", "check": c("caption_style.size", "==", 84)}], "rejected": []}
    assert taste_lint.ok(taste_lint.lint(taste, DATA))


def test_good_parse_check_reads_key_op_value_with_json_values():
    assert taste_lint.parse_check("caption_style.size == 84") == c("caption_style.size", "==", 84)
    assert taste_lint.parse_check("audio.true_peak <= -1.5") == c("audio.true_peak", "<=", -1.5)
    assert taste_lint.parse_check('audio.require_source == "epidemic"')["value"] == "epidemic"
    assert taste_lint.parse_check("caption_style.ab_sizes in [[84]]")["value"] == [[84]]
    assert taste_lint.parse_check("gates.cta exists true") == c("gates.cta", "exists", True)
    assert taste_lint.parse_check("transitions.page_turn == false")["value"] is False


# ------------------------------------------------------------------------------------------------------------ bad
@pytest.mark.parametrize("text", ["", "caption_style.size", "caption_style.size = 84", "x == ", "x ~ 1",
                                  "x == epidemic", "x exists 3", "x in 3"])
def test_bad_parse_check_rejects_malformed_checks(text):
    with pytest.raises(CheckError):
        taste_lint.parse_check(text)


@pytest.mark.parametrize("bad", [{"key": "a", "op": "==", "value": 1, "extra": 1}, {"key": "a", "op": "=="},
                                 {"key": "", "op": "==", "value": 1}, {"key": "a", "op": "??", "value": 1}, "a == 1"])
def test_bad_validate_rejects_malformed_check_objects(bad):
    with pytest.raises(CheckError):
        taste_lint.validate(bad)


def test_bad_a_malformed_check_in_the_store_is_reported_not_raised():
    taste = {"liked": [{"what": "w", "why": "y", "check": [{"key": "a", "op": "??", "value": 1}]}], "rejected": []}
    rep = taste_lint.lint(taste, DATA)
    assert rep["results"][0]["status"] == "bad-check" and not taste_lint.ok(rep)
    assert "BAD CHECK" in taste_lint.render(rep, "c", "p")


# ------------------------------------------------------------------------------------- store, profile and CLI
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE_TASTE_DIR", str(tmp_path / "taste"))
    return tmp_path / "taste"


def test_good_record_feedback_keeps_checks_and_a_new_entry_replaces_the_old_one(store):
    director.record_feedback("geoverum", "liked", "size 84", "approved", [c("caption_style.size", "==", 84)])
    d = director.load_taste("geoverum")
    assert d["liked"][0]["check"] == [c("caption_style.size", "==", 84)]
    director.record_feedback("geoverum", "rejected", "size 84", "changed my mind")
    d = director.load_taste("geoverum")
    assert d["liked"] == [] and "check" not in d["rejected"][0]
    with pytest.raises(CheckError):
        director.record_feedback("geoverum", "liked", "x", "y", [{"key": "a", "op": "??", "value": 1}])


def test_good_the_prompt_text_of_the_taste_store_ignores_checks(store):
    director.record_feedback("geoverum", "liked", "size 84", "approved", [c("caption_style.size", "==", 84)])
    assert "check" not in director.taste_text(director.load_taste("geoverum"))


def test_good_the_geoverum_store_agrees_with_the_geoverum_profile():
    taste = json.loads((config.PROFILES_DIR / "taste" / "geoverum.json").read_text())
    data = json.loads(profiles.load("geoverum").data.model_dump_json())
    rep = taste_lint.lint(taste, data)
    assert rep["results"] and taste_lint.ok(rep), taste_lint.render(rep, "geoverum", "geoverum")


def test_good_cli_lint_exit_codes_and_check_option(store, capsys):
    assert cli.main(["taste", "geoverum", "--like", "size 84", "--why", "approved", "--check",
                     "caption_style.size == 84", "--check", "caption_style.box_bg exists false"]) == 0
    capsys.readouterr()
    assert cli.main(["taste", "geoverum", "--lint"]) == 0
    assert "2 of 2 checks ok" in capsys.readouterr().out
    assert cli.main(["taste", "geoverum", "--reject", "size 92", "--why", "too heavy", "--check",
                     "caption_style.size == 92"]) == 0                          # the profile says 84: a mismatch
    capsys.readouterr()
    assert cli.main(["taste", "geoverum", "--lint"]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out and "profile has 84" in out and "1 of 3 checks" not in out and "2 of 3 checks ok" in out


def test_bad_cli_taste_option_combinations_are_clean_errors(store, capsys):
    assert cli.main(["taste", "geoverum", "--lint", "--like", "x", "--why", "y"]) == 1
    assert cli.main(["taste", "geoverum", "--check", "a == 1"]) == 1
    assert cli.main(["taste", "geoverum", "--profile", "default"]) == 1
    assert cli.main(["taste", "geoverum", "--like", "x", "--why", "y", "--check", "not a check"]) == 1
    err = capsys.readouterr().err
    assert "cannot be combined" in err and "belongs to --like" in err and "--profile is for --lint" in err
    assert "must read KEY OP VALUE" in err
    assert cli.main(["taste", "nosuchchannel", "--lint"]) == 1                 # no such profile
    assert "not found" in capsys.readouterr().err
