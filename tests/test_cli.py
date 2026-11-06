"""CLI exit codes: validate, preflight (fail -> 1), deliver requires --dry-run, render alias."""
from video_engine import cli


def test_good_validate(project, capsys):
    p, _ = project()
    assert cli.main(["validate", str(p)]) == 0 and "180 frames" in capsys.readouterr().out


def test_bad_validate_schema(project, tmp_path):
    p, _ = project()
    (p / "master_timeline.json").write_text('{"project_id": "x"}')
    assert cli.main(["validate", str(p)]) != 0


def test_good_preflight_no_dom(project):
    p, _ = project()
    assert cli.main(["preflight", str(p), "--no-dom"]) == 0


def test_bad_preflight_gap(project):
    p, _ = project(lambda d: d["segments"][1].update(start_frame=61))
    assert cli.main(["preflight", str(p), "--no-dom"]) == 1


def test_bad_deliver_without_dry_run(project):
    import pytest
    p, _ = project()
    with pytest.raises(SystemExit) as e:
        cli.main(["deliver", str(p)])
    assert e.value.code == 2


def test_good_render_alias_parses():
    a = cli.parser().parse_args(["render", "x", "--no-cache"])
    assert a.fn == cli.cmd_build and a.no_cache


def test_bad_missing_project_is_a_clean_cli_error(tmp_path, capsys):
    assert cli.main(["validate", str(tmp_path / "missing")]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "Traceback" not in err
