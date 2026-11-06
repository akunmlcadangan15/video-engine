"""Stage 2 caption style (Geoverum): Inter Bold, no outline, soft shadow, no box by default, fixed_y 1417 and A/B sizes."""
import pytest
from test_style_gates import layout

from video_engine import captions, profiles
from video_engine.util import EngineError

WORDS = [("Pagi", 0, 8), ("sepuluh", 8, 16), ("November", 16, 30), ("seribu", 30, 40)]


def _style_line(ass: str) -> list[str]:
    return next(l for l in ass.splitlines() if l.startswith("Style:")).split(",")


def test_good_geoverum_inter_bold_no_outline_no_box_fixed_y(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    st = profiles.caption_style(tl)
    assert st["font"] == "Inter" and st["bold"] is True
    assert st["size"] == 84 and st["outline"] == 0 and st["soft_shadow"] is True
    assert "box_bg" not in st and st["fixed_y"] == 1417 and st["max_bottom_y"] == 1480   # no box by default
    assert st["forbid_box"] is False                     # a caption box is an optional per-segment style choice, not blocked
    txt, ev = captions.compile_ass([type("W", (), {"word": w, "start_frame": a, "end_frame": b, "highlight": False})()
                                    for w, a, b in WORDS], tl)
    f = _style_line(txt)
    assert f[1] == "Inter" and float(f[2]) == 84.0 and f[7] == "-1"      # bold on
    assert f[3].upper() == "&H00FFFFFF"                                  # white fill
    assert f[15] == "1" and float(f[16]) == 0.0                        # BorderStyle 1 (no box), outline 0
    assert all(e[6] == 1417.0 for e in ev)
    assert all(len(e[2].split()) <= 3 for e in ev)
    assert st['primary'] == 'FFFFFF' and st['highlight_colour'] == 'FFFFFF'
    assert all("BorderStyle" not in l for l in txt.splitlines() if l.startswith("Dialogue"))
    assert "\\1c&H" in txt                                           # active_word_highlight=true: per-word highlight
    assert all("\\blur4" in l and "\\3a&H73&" in l for l in txt.splitlines() if l.startswith("Dialogue: 0,"))


def test_good_ab_sizes_reach_the_ass_compiler_but_stay_python_only(project):
    # The profile lists 84 px in ab_sizes; an explicit timeline override
    # of the size is still valid and reaches the .ass compiler.
    for size in (84, 92, 104):
        _, tl = project(layout(("map", 30, None), cs={"size": size}))
        assert profiles.caption_style(tl)["ab_sizes"] == [84]
        assert "ab_sizes" not in captions.rust_style(tl)
        ev = captions.compile_ass([type("W", (), {"word": "Halo", "start_frame": 0, "end_frame": 10,
                                                   "highlight": False})()], tl)[1]
        assert ev and ev[0][3] == float(size)


def test_bad_forbid_box_rejects_box_bg_from_profile_or_timeline(project, strict_geoverum):
    with pytest.raises(EngineError, match="forbid_box"):
        project(layout(("map", 30, None), cs={"box_bg": True}))
    _, tl = project(layout(("map", 30, None)))
    tl.caption_style = {"box_bg": True}
    with pytest.raises(EngineError, match="forbid_box"):
        captions.compile_ass([type("W", (), {"word": "Halo", "start_frame": 0, "end_frame": 10,
                                              "highlight": False})()], tl)


def test_good_geoverum_allows_a_caption_box_from_the_timeline(project):
    _, tl = project(layout(("map", 30, None), cs={"box_bg": True}))
    assert profiles.caption_style(tl)["box_bg"] is True
    assert captions.compile_ass([type("W", (), {"word": "Halo", "start_frame": 0, "end_frame": 10,
                                                 "highlight": False})()], tl)[1]


def test_good_default_caption_style_is_unchanged(project):
    _, tl = project()
    st = profiles.caption_style(tl)
    assert "forbid_box" not in st and "ab_sizes" not in st and "fixed_y" not in st
    assert captions.overrides(tl, None) == []
