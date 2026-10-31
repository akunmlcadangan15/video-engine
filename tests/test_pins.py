"""Pin themes: default dot pin (CSS variables at today's values) and the teardrop pin (bounce drop-in, shadow, label
beside the head in the theme label font)."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, profiles
from video_engine.gates import layer2

TEAR = {"pin_style": "teardrop", "pin_colour": "#e8322b", "label_font": "script"}
PATH = [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.2}, {"at_frame": 30, "focus": [0.5, 0.5], "zoom": 1.2},
        {"at_frame": 45, "focus": [0.5, 0.5], "zoom": 2.4}]          # probed on the key frames 30 and 45


def _pins(pins, path=None):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc["pins"] = pins
        sc["cards"] = []
        if path:
            sc["camera_path"] = path
    return mut


def _theme(monkeypatch, **kw):
    prof = profiles.load()
    data = prof.data.model_copy(update={"theme": prof.theme.model_copy(update=kw)})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))


def _probe(tl, p, out, times, snaps=False):
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, out / "hf")
    res = hyperframes.probe_dom(d, times, 1080, 1920, out / "snaps" if snaps else None)
    return d, sc, [{r["id"]: r for r in it["rects"]} | {"png": it["png"]} for it in res["times"]]


PIN = {"label": "Pusat", "x": 0.5, "y": 0.5, "at_frame": 0}


# good ---------------------------------------------------------------------------------------------------------------
def test_good_default_dot_pin_keeps_todays_look(project, tmp_path):
    p, tl = project(_pins([PIN]))
    d, sc, (it,) = _probe(tl, p, tmp_path, [1.0], snaps=True)
    assert "pinhead:0" not in it and "pin:0" in it
    assert sc["theme"]["pin_colour"] == "#ffd84a"
    a = it["anchor:pin:0"]
    px = np.asarray(Image.open(it["png"]).convert("RGB"))[round(a["y"]), round(a["x"])]
    assert np.abs(px.astype(int) - [255, 216, 74]).max() <= 6              # dot centre in the default pin colour
    assert sorted(f.name for f in (d / "assets").iterdir() if f.suffix in (".otf", ".ttf")) == sorted(hyperframes.HF_FONTS)


def test_good_teardrop_drops_in_with_bounce_and_lands_on_the_point(project, tmp_path, monkeypatch):
    _theme(monkeypatch, **TEAR)
    p, tl = project(_pins([{**PIN, "at_frame": 15}]))
    _, _, (mid, landed) = _probe(tl, p, tmp_path, [0.5 + 0.05, 0.5 + 0.7])
    a = landed["anchor:pin:0"]
    head = landed["pinhead:0"]
    assert head["y"] + head["h"] / 2 == pytest.approx(a["y"] - 31 * (head["h"] / 44), abs=1.5)  # head above the tip
    assert mid["pinhead:0"]["y"] < head["y"] - 50                                             # still falling


def test_good_teardrop_colours_and_white_dot(project, tmp_path, monkeypatch):
    _theme(monkeypatch, **TEAR)
    p, tl = project(_pins([PIN]))
    _, _, (it,) = _probe(tl, p, tmp_path, [1.5], snaps=True)
    img = np.asarray(Image.open(it["png"]).convert("RGB")).astype(int)
    h = it["pinhead:0"]
    cx, cy = round(h["x"] + h["w"] / 2), round(h["y"] + h["h"] / 2)
    assert np.abs(img[cy, cx] - [255, 255, 255]).max() <= 10                # white dot in the head centre
    ring = img[cy, round(h["x"] + h["w"] * 0.2)]
    assert np.abs(ring - [0xe8, 0x32, 0x2b]).max() <= 25                     # red body


def test_good_label_beside_head_constant_size_through_zoom(project, tmp_path, monkeypatch):
    _theme(monkeypatch, **TEAR)
    p, tl = project(_pins([PIN, {"label": "Kiri", "x": 0.45, "y": 0.6, "at_frame": 0, "label_side": "left"}], PATH))
    _, _, (z1, z2) = _probe(tl, p, tmp_path, [1.0, 1.6])
    for it in (z1, z2):
        a, lab = it["anchor:pin:0"], it["pin:0"]
        assert lab["x"] == pytest.approx(a["x"] + 34, abs=1.5)
        assert lab["y"] + lab["h"] / 2 == pytest.approx(a["y"] - 31, abs=1.5)
        a1, lab1 = it["anchor:pin:1"], it["pin:1"]
        assert lab1["x"] + lab1["w"] == pytest.approx(a1["x"] - 34, abs=1.5)
    assert (z1["pin:0"]["w"], z1["pin:0"]["h"]) == pytest.approx((z2["pin:0"]["w"], z2["pin:0"]["h"]), abs=1)


def test_good_script_font_only_in_theme_that_uses_it(project, tmp_path, monkeypatch):
    p, tl = project(_pins([PIN]))
    key0 = hyperframes.keys(tl.segments[2], tl, p)[0]
    _theme(monkeypatch, **{**TEAR, "label_font": "inter"})
    _, _, (inter,) = _probe(tl, p, tmp_path / "i", [1.0])
    _theme(monkeypatch, **TEAR)
    d, sc, (script,) = _probe(tl, p, tmp_path / "s", [1.0])
    assert (d / "assets" / "KaushanScript-Regular.ttf").is_file() and "EngScript" in (d / "index.html").read_text()
    assert abs(script["pin:0"]["w"] - inter["pin:0"]["w"]) > 5                 # a different face is really used
    assert hyperframes.keys(tl.segments[2], tl, p)[0] != key0


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_label_beside_off_frame_fails_R63(project, tmp_path, monkeypatch):
    _theme(monkeypatch, **TEAR)
    p, tl = project(_pins([{**PIN, "x": 0.97, "label": "Ujung Timur"}]))
    _, _, (it,) = _probe(tl, p, tmp_path, [1.5])
    rects = [r for k, r in it.items() if k != "png"]
    assert any("pin:0" in f.message and f.level == "fail" for f in layer2.check_rects(rects, 1080, 1920, 1.5, 2))


def test_bad_pin_colour_not_hex():
    with pytest.raises(ValidationError, match="#rrggbb"):
        profiles.Theme(**{**profiles.load().theme.model_dump(), "pin_colour": "red"})


def test_bad_label_side(project):
    with pytest.raises(ValidationError, match="'left' or 'right'"):
        project(_pins([{**PIN, "label_side": "up"}]))


@pytest.mark.parametrize("bounds", [[10, 0, -10, 5], [0, 10, 5, -10], [0, 0, 5, 0], [0, 10, 5, 10]])
def test_bad_map_bounds_need_south_north_west_east(project, bounds):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["map_bounds"] = bounds
    with pytest.raises(ValidationError, match="map_bounds"):
        project(mut)


def test_good_map_bounds_are_accepted(project):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["map_bounds"] = [-10, 100, 10, 130]
    _, tl = project(mut)
    assert tl.segments[2].gsap_scene_config.map_bounds == [-10, 100, 10, 130]
