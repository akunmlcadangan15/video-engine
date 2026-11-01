"""Rust .ass compiler: default style (no box, 1-2 words, white bold Inter, outline + shadow), overrides."""
import pytest

import hypoverum_core_rs as core
from video_engine import captions


def style_line(ass):
    return next(l for l in ass.splitlines() if l.startswith("Style: Cap"))


def test_good_default_style_no_box(project, W):
    _, tl = project()
    ass, ev = captions.compile_ass(W(("Arek", 0, 10), ("Suroboyo", 10, 20), ("menolak", 20, 30)), tl)
    f = style_line(ass).split(",")
    assert f[1] == "Inter" and float(f[2]) == 84.0 and f[3] == "&H00FFFFFF" and f[7] == "-1"   # bold
    assert f[15] == "1" and float(f[16]) == 5.0                                                 # BorderStyle 1 = outline, no box
    ev_lines = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert ev_lines and all("\\1a&HFF&" in l and "\\blur" in l for l in ev_lines)                 # soft offset shadow layer
    assert all(1 <= len(e[2].split()) <= 2 for e in ev)


def test_good_one_or_two_words_per_event(project, W):
    _, tl = project()
    ws = W(*[(w, i * 8, i * 8 + 8) for i, w in enumerate("satu dua tiga empat lima enam tujuh".split())])
    ev = captions.compile_ass(ws, tl)[1]
    assert all(1 <= len(e[2].split()) <= 2 for e in ev) and sum(len(e[2].split()) for e in ev) == 7


def test_good_slot_positions(project, W):
    for slot, y in (("lower", 1344), ("mid", 960), ("upper", 461)):
        _, tl = project(lambda d, s=slot: d.update(caption_style={"slot": s}))
        assert captions.compile_ass(W(("a", 0, 10)), tl)[1][0][6] == pytest.approx(y, abs=1)


def test_good_y_override_only_in_range(project, W):
    _, tl = project()
    ev = captions.compile_ass(W(("a", 0, 10), ("b", 70, 80)), tl, [(60, 120, 1500.0)])[1]
    assert ev[0][6] == 1344 and ev[1][6] == 1500


def test_bad_unknown_style_key_raises(project, W):
    _, tl = project(lambda d: d.update(caption_style={"box": True}))
    with pytest.raises(Exception, match="box"):
        captions.compile_ass(W(("a", 0, 10)), tl)


def test_good_python_keys_not_passed_to_rust(project):
    _, tl = project(lambda d: d.update(caption_style={"auto_place": False, "y_overrides": [[0, 10, 900]]}))
    assert captions.rust_style(tl) in (None, {})
    assert captions.overrides(tl) == [(0, 10, 900.0)]


def test_good_word_escaping():
    ass, _ = core.compile_ass([("{x}", 0, 10, False)], 1080, 1920, 30, None, None)
    assert "{x}" not in ass.split("[Events]")[1].split("}", 1)[1]
