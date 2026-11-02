"""util.code_hash / util.source_digest: Python sources count by behaviour (syntax tree without docstrings), so editing
comments, docstrings or formatting of a keyed module keeps cached renders; any change of behaviour changes the key."""
import pytest

from video_engine import hyperframes, legibility, native, util

from test_stage_keys import edited_copies, hf_key, native_keys

BASE = '''"""Module docstring."""
import math


def area(r):
    """Area of a circle."""
    return math.pi * r * r   # comment


class Box:
    """A box."""
    W = 3
'''


def _mk(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def digest(tmp_path, text, name="m.py"):
    f = tmp_path / name
    f.write_text(text)
    return util.source_digest(f)


# ----------------------------------------------------------------------------------------------------------- good
@pytest.mark.parametrize("edit", [
    lambda t: t + "\n# a new comment\n",
    lambda t: t.replace("# comment", "# another comment"),
    lambda t: t.replace('"""Area of a circle."""', '"""Circle area, rewritten."""'),
    lambda t: t.replace('"""Module docstring."""', '"""Something else entirely."""'),
    lambda t: t.replace('"""A box."""\n', ""),                                  # a removed docstring
    lambda t: t.replace("math.pi * r * r", "math.pi*r*r"),                         # spacing
    lambda t: t.replace("\n\n\n", "\n\n\n\n\n"),                                   # blank lines
    lambda t: t.replace("import math", "import math  # used for pi"),
    lambda t: t.replace("(r)", "(\n    r,\n)"),                                    # wrapped parameters
    lambda t: t.replace('"""Area of a circle."""', "'''Area of a circle.'''"),   # quote style
])
def test_good_cosmetic_edits_keep_the_digest(tmp_path, edit):
    assert digest(tmp_path, edit(BASE), "b.py") == digest(tmp_path, BASE, "a.py")


def test_good_a_function_with_only_a_docstring_keeps_a_valid_body(tmp_path):
    a = digest(tmp_path, 'def f():\n    """Only a docstring."""\n')
    b = digest(tmp_path, 'def f():\n    """Another docstring."""\n')
    assert a == b


def test_good_non_python_files_and_broken_python_are_hashed_byte_for_byte(tmp_path):
    assert digest(tmp_path, "a\n", "t.txt") != digest(tmp_path, "a \n", "t.txt")
    assert digest(tmp_path, "def (:\n", "bad.py") != digest(tmp_path, "def (: \n", "bad.py")


def test_good_the_memo_follows_the_file(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("X = 1\n")
    a = util.source_digest(f)
    f.write_text("X = 22\n")                       # a different size: the memo key changes
    assert util.source_digest(f) != a


def test_good_code_hash_takes_the_files_in_order(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 1\n")
    one = util.code_hash([tmp_path / "a.py", tmp_path / "b.py"])
    assert one == util.code_hash([tmp_path / "a.py", tmp_path / "b.py"])
    assert one != util.code_hash([tmp_path / "b.py", tmp_path / "a.py"])


# ------------------------------------------------------------------------------------------------------------ bad
@pytest.mark.parametrize("edit", [
    lambda t: t.replace("math.pi * r * r", "math.pi * r"),
    lambda t: t.replace("def area(r)", "def area(radius)").replace("* r * r", "* radius * radius"),
    lambda t: t.replace("W = 3", "W = 4"),
    lambda t: t + "\nX = 1\n",
    lambda t: t.replace("import math", "import math\nimport os"),
    lambda t: t.replace("return math.pi * r * r", "return math.pi * r * r + 0.0"),
    lambda t: t.replace("class Box:", "class Crate:"),
])
def test_bad_a_change_of_behaviour_changes_the_digest(tmp_path, edit):
    assert digest(tmp_path, edit(BASE), "b.py") != digest(tmp_path, BASE, "a.py")


def test_bad_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        util.code_hash([tmp_path / "missing.py"])


# ------------------------------------------------------------------------------------------- real cache keys
def test_good_a_comment_in_a_keyed_module_keeps_the_native_and_hyperframes_keys(project, monkeypatch, tmp_path):
    p, tl = project()
    nk, hk = native_keys(p, tl), hf_key(p, tl)
    monkeypatch.setattr(native, "CODE", edited_copies(_mk(tmp_path, "n"), native.CODE, line="# only a comment"))
    monkeypatch.setattr(hyperframes, "CODE", edited_copies(_mk(tmp_path, "h"), hyperframes.CODE, line="# only a comment"))
    assert native_keys(p, tl) == nk and hf_key(p, tl) == hk


def test_bad_a_statement_in_a_keyed_module_changes_the_keys(project, monkeypatch, tmp_path):
    p, tl = project()
    nk, hk = native_keys(p, tl), hf_key(p, tl)
    monkeypatch.setattr(native, "CODE", edited_copies(_mk(tmp_path, "n"), native.CODE))
    monkeypatch.setattr(hyperframes, "CODE", edited_copies(_mk(tmp_path, "h"), hyperframes.CODE))
    assert all(a != b for a, b in zip(nk, native_keys(p, tl))) and hf_key(p, tl) != hk


def test_good_the_legibility_digest_is_the_source_digest_of_the_module():
    assert legibility.code_hash() == util.source_digest(legibility.__file__)
