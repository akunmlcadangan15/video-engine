"""Profile watermark: placement, PNG, final-mux overlay, gate B01."""
import subprocess

import numpy as np
import pytest
from PIL import Image

from video_engine import audio, config, profiles, watermark
from video_engine.gates import layer2


DEFAULT_TOML = (config.PROFILES_DIR / "default.toml").read_text()
WM = '[watermark]\ntext = "Geoverum"\n'


@pytest.fixture
def chan(tmp_path, monkeypatch, project):
    """chan(toml) -> (project_dir, Timeline) using channel profile 'chan' with that toml."""
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text(DEFAULT_TOML)
    monkeypatch.setattr(config, "PROFILES_DIR", d)

    def make(text=WM, mut=None):
        (d / "chan.toml").write_text(text)

        def m(doc):
            doc["profile"] = "chan"
            if mut:
                mut(doc)
        return project(m)
    return make


def _mux(tl, tmp_path):
    """final_mux of a 30-frame grey clip; returns the first output frame (H, W, 3) and the ffmpeg command."""
    src = tmp_path / "grey.mp4"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:s=1080x1920:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
    seen = []
    real = audio.run

    def spy(cmd, **kw):
        seen.append([str(c) for c in cmd])
        return real(cmd, **kw)
    audio.run = spy
    try:
        out = tmp_path / "final.mp4"
        audio.final_mux(tl, tmp_path, [src], None, out, threads=2)
    finally:
        audio.run = real
    png = tmp_path / "f0.png"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", out, "-frames:v", "1", png], check=True)
    return np.asarray(Image.open(png).convert("RGB")).astype(int), next(c for c in seen if "-an" in c), out


def _one_seg(doc):
    doc["segments"] = [dict(doc["segments"][0], end_frame=30)]


# GOOD ---------------------------------------------------------------------------------------------------------------
def test_good_watermark_sits_top_right_inside_safe_area(chan):
    _, tl = chan()
    r = watermark.placement(tl).text_rect()
    W, H = tl.settings.width, tl.settings.height
    assert abs((r["x"] + r["w"]) - (W - watermark.SAFE_X)) <= 1 and abs(r["y"] - watermark.SAFE_TOP) <= 1
    assert r["x"] > W / 2 and r["w"] > 100 and 20 < r["h"] < 60


def test_good_png_has_text_colour_opacity_and_shadow(chan, tmp_path):
    _, tl = chan(WM + 'colour = "#ffd84a"\nopacity = 0.8\n')
    pl = watermark.render(tl, tmp_path / "wm.png")
    a = np.asarray(Image.open(tmp_path / "wm.png").convert("RGBA")).astype(int)
    assert a.shape[:2] == (pl.h, pl.w)
    text = a[a[..., 3] >= 200]
    assert len(text) and abs(text[..., :3].mean(0) - [0xff, 0xd8, 0x4a]).max() < 30
    assert round(255 * 0.8) <= a[..., 3].max() < 255              # 80 % text over its shadow, never opaque
    dark = a[(a[..., 3] > 10) & (a[..., :3].sum(-1) < 60)]
    assert len(dark) > 50                                       # the soft shadow
    assert a[0, 0, 3] == 0 and a[-1, -1, 3] == 0                # transparent margin


def test_good_final_mux_burns_watermark_where_placed(chan, tmp_path):
    _, tl = chan(mut=_one_seg)
    f, cmd, _ = _mux(tl, tmp_path)
    r = watermark.placement(tl).text_rect()
    box = f[int(r["y"]):int(r["y"] + r["h"]), int(r["x"]):int(r["x"] + r["w"])]
    assert box.max() > 200                                      # white text on the grey clip
    assert abs(f[1400:1500, 100:300] - 0x40).max() <= 6         # elsewhere untouched
    assert any("overlay=x=" in c for c in cmd)
    assert not list(tmp_path.glob("*.watermark.png"))           # scratch PNG removed


def test_good_no_watermark_keeps_default_mux_graph(project, tmp_path):
    _, tl = project(_one_seg)
    assert profiles.of(tl).watermark is None and watermark.placement(tl) is None
    f, cmd, _ = _mux(tl, tmp_path)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:v]null,format=yuv420p[v]") and "overlay" not in graph
    assert abs(f - 0x40).max() <= 6


def test_good_B01_passes_when_clear(chan):
    _, tl = chan()
    fs = layer2.b01_watermark(tl, [], {0: [{"id": "pin:0", "x": 100, "y": 900, "w": 200, "h": 60}]})
    assert [f.level for f in fs] == ["pass"] and "1 element boxes" in fs[0].message


def test_good_B01_silent_without_watermark(project):
    _, tl = project()
    assert layer2.b01_watermark(tl, [], {0: [{"id": "x", "x": 0, "y": 0, "w": 1080, "h": 1920}]}) == []


# BAD ----------------------------------------------------------------------------------------------------------------
def test_bad_B01_element_under_watermark_fails(chan):
    _, tl = chan()
    r = watermark.placement(tl).text_rect()
    box = {"id": "pin:3", "text": "Kota", "x": r["x"] + 10, "y": r["y"] + 5, "w": 80, "h": 40}
    fs = layer2.b01_watermark(tl, [], {2: [box]})
    assert fs[0].level == "fail" and fs[0].gate == "B01" and "pin:3" in fs[0].message and fs[0].seg == 2


def test_bad_B01_caption_under_watermark_fails(chan):
    _, tl = chan()
    r = watermark.placement(tl).text_rect()
    ev = (0, 30, "Halo", 72.0, 120.0, r["x"] + 60, r["y"] + 15)    # (a, b, text, size, est_w, x, y)
    fs = layer2.b01_watermark(tl, [ev], {})
    assert fs[0].level == "fail" and "caption 'Halo'" in fs[0].message


def test_bad_B01_text_too_wide_for_safe_area_fails(chan):
    _, tl = chan('[watermark]\ntext = "GeoverumGeoverumGeoverumGeoverum"\npx = 120\n')
    fs = layer2.b01_watermark(tl, [], {})
    assert fs[0].level == "fail" and "safe area" in fs[0].message


@pytest.mark.parametrize("toml, msg", [
    ('[watermark]\ntext = "Geo"\ncolour = "white"\n', "#rrggbb"),
    ('[watermark]\ntext = "Geo\\nverum"\n', "one line"),
    ('[watermark]\ntext = ""\n', "at least 1"),
    ('[watermark]\ntext = "Geo"\npx = 400\n', "less than or equal"),
    ('[watermark]\ntext = "Geo"\nposition = "left"\n', "Extra inputs"),
])
def test_bad_watermark_schema(chan, toml, msg):
    with pytest.raises(Exception) as e:
        chan(toml)
        profiles.load("chan")
    assert msg in str(e.value)
