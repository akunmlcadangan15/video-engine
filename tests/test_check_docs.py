"""scripts/check_docs.py: the documentation checker finds broken links, anchors, index gaps and a stale gate index."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A tiny documentation tree; the checker is pointed at it."""
    (tmp_path / "docs").mkdir()
    files = {"README.md": "# Title\n\nSee [guide](docs/GUIDE.md#setup-steps) and [index](docs/README.md).\n",
             "docs/README.md": "# Index\n\n[guide](GUIDE.md)\n",
             "docs/GUIDE.md": "# Guide\n\n## Setup steps\n\n## `engine x` (flag)\n\nBack to [top](#guide).\n"}
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    monkeypatch.setattr(check_docs, "MD_FILES", [tmp_path / n for n in files])
    return tmp_path


def run(fn, *a):
    errors: list[str] = []
    fn(errors, *a)
    return errors


# ----------------------------------------------------------------------------------------------------------- good
def test_good_slug_follows_the_github_rule():
    assert check_docs.slug("Setup steps") == "setup-steps"
    assert check_docs.slug("`engine gates` (flag)") == "engine-gates-flag"
    assert check_docs.slug("Cache-key rules") == "cache-key-rules"
    assert check_docs.slug("Draft builds: --mode") == "draft-builds---mode"
    assert check_docs.slug("[Link](x.md) text") == "link-text"


def test_good_anchors_number_duplicate_headings_and_skip_code_fences(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# One\n## Same\n## Same\n```\n# not a heading\n```\n", encoding="utf-8")
    assert check_docs.anchors(f) == {"one", "same", "same-1"}


def test_good_a_consistent_tree_has_no_problems(repo):
    assert run(check_docs.check_links) == [] and run(check_docs.check_index) == []


# ------------------------------------------------------------------------------------------------------------ bad
def test_bad_a_missing_target_and_a_missing_anchor_are_reported(repo):
    (repo / "README.md").write_text("[a](docs/NOPE.md) [b](docs/GUIDE.md#no-such) [c](#nope)\n", encoding="utf-8")
    errors = run(check_docs.check_links)
    assert len(errors) == 3
    assert any("NOPE.md" in e and "does not exist" in e for e in errors)
    assert any("#no-such" in e for e in errors) and any("#nope" in e for e in errors)


def test_good_external_links_code_spans_and_fenced_code_are_ignored(repo):
    (repo / "README.md").write_text("[x](https://example.org/a#b) `[y](missing.md)`\n```\n[z](missing.md)\n```\n",
                                    encoding="utf-8")
    assert run(check_docs.check_links) == []


def test_bad_a_document_missing_from_the_index_is_reported(repo):
    (repo / "docs" / "EXTRA.md").write_text("# Extra\n", encoding="utf-8")
    (repo / "docs" / "folder").mkdir()
    errors = run(check_docs.check_index)
    assert any("EXTRA.md" in e for e in errors) and any("folder/" in e for e in errors)


def test_bad_a_code_comment_that_names_a_missing_document_is_reported(repo):
    (repo / "video_engine").mkdir()
    (repo / "video_engine" / "x.py").write_text("# see docs/GUIDE.md and docs/MISSING.md\n", encoding="utf-8")
    errors = run(check_docs.check_code_refs)
    assert errors == ["video_engine/x.py:1: names docs/MISSING.md, which does not exist"]


# ---------------------------------------------------------------------------------------------- the real tree
def test_good_the_repository_documentation_passes_every_check(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["check_docs.py"])
    assert check_docs.main() == 0, capsys.readouterr().err
