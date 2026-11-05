"""Landscape 16:9 output (docs/SPEC.md, Units; docs/PROFILES.md, Orientation): profile orientation, captions,
watermark corner, overlays, delivery metadata, draft slots, worker budget, HyperFrames scene units, H.264 level."""
from __future__ import annotations

import subprocess

import pytest

from video_engine import captions, deliver, draft, hyperframes, overlays, profiles, resources, streams, watermark
from video_engine.gates import layer2
from video_engine.units import Units, units
from video_engine.util import EngineError

# Pinned profile hashes: landscape support must not move a portrait profile's hash (segment caches stay valid).
PORTRAIT_HASHES = {
    "default": "6f0c27f0013377557dcd7c561b5aefc0a07b722a3a89f47080f0b72c76df1f64",
    "geoverum": "17fe5ea02236c995601240228af802d002d554a8338267543a97b4a048dcaa21",
}


def _landscape(W=1920, H=1080, profile=None, native_only=True, overlays_=None):
    def mut(d):
        d["settings"]["width"], d["settings"]["height"] = W, H
        if native_only:
            d["segments"] = d["segments"][:1]
            d["segments"][0]["end_frame"] = 60
        if profile:
            d["profile"] = profile
        if overlays_ is not None:
            d["segments"][0]["overlays"] = overlays_
    return mut


@pytest.mark.parametrize("name", sorted(PORTRAIT_HASHES))
def test_good_portrait_profile_hashes_do_not_move(name):
    assert profiles.load(name).blake3 == PORTRAIT_HASHES[name]


def test_good_landscape_profile_loads_with_its_orientation_and_geometry():
    p = profiles.load("geoverum_landscape")
    assert profiles.profile_orientation(p.data) == "landscape"
    assert p.caption_style["fixed_y"] == 900 and p.caption_style["size"] == 58
    assert p.watermark.corner == "bottom_right"
    assert p.data.layout.grid.columns == 12 and p.data.print.year_cy == 380
    assert profiles.profile_orientation(profiles.load("geoverum").data) == "portrait"   # inferred from its geometry
    assert profiles.profile_orientation(profiles.load().data) is None                  # default: either orientation


def test_bad_profile_orientation_and_frame_disagree(project):
    with pytest.raises(Exception, match="portrait profile but the frame 1920x1080 is landscape"):
        project(_landscape(profile="geoverum"))
    with pytest.raises(Exception, match="landscape profile but the frame 1080x1920 is portrait"):
        project(lambda d: d.update(profile="geoverum_landscape"))


def test_good_default_profile_accepts_both_orientations(project):
    _, tl = project(_landscape())
    assert units(tl).landscape


def test_bad_portrait_print_position_in_a_landscape_range(tmp_path, monkeypatch):
    from video_engine import config
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text('[format]\norientation = "landscape"\n[print]\nyear_cy = 560\n')
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError, match="print.year_cy = 560 outside the approved landscape range"):
        profiles.load("chan")
    (d / "chan.toml").write_text('[format]\norientation = "landscape"\n[caption_style]\nfixed_y = 1382\n')
    with pytest.raises(EngineError, match="fixed_y must be a number in 0..1080"):
        profiles.load("chan")


@pytest.mark.parametrize("W,H", [(1920, 1080), (2560, 1440), (3840, 2160)])
def test_good_landscape_caption_style_scales_by_k(project, W, H):
    _, tl = project(_landscape(W, H))
    k = H / 1080
    st = captions.effective_style(tl)
    assert st["size"] == pytest.approx(58 * k) and st["margin_x"] == pytest.approx(160 * k)
    assert st["outline"] == pytest.approx(5 * k)
    rs = captions.rust_style(tl)
    assert "clearance" not in rs and rs["size"] == pytest.approx(58 * k)


def test_good_portrait_caption_style_is_unchanged(project):
    _, tl = project()
    assert captions.effective_style(tl) == profiles.caption_style(tl)


@pytest.mark.parametrize("W,H", [(1920, 1080), (3840, 2160)])
def test_good_landscape_captions_pass_r62_inside_the_safe_area(project, W, H):
    from conftest import words
    _, tl = project(_landscape(W, H))
    ws = words(("Halo", 0, 20), ("dunia", 20, 40), ("ini", 40, 50), ("landscape", 50, 60))
    out, events = layer2.r62_caption_margins(tl, ws)
    assert [f.level for f in out] == ["pass"], out
    fs = events[0][3]
    assert fs == pytest.approx(58 * H / 1080, rel=0.02)


def test_bad_landscape_caption_too_wide_fails_r62(project):
    from conftest import words
    _, tl = project(lambda d: (_landscape()(d), d.update(caption_style={"max_chars": 200, "max_words": 30,
                                                                         "size": 140})))
    ws = words(*[(w, i * 2, i * 2 + 2) for i, w in enumerate(("panjang " * 25).split())])
    out, _ = layer2.r62_caption_margins(tl, ws)
    assert any(f.level == "fail" for f in out)


def test_good_landscape_watermark_sits_bottom_right_inside_the_safe_area(project):
    _, tl = project(_landscape(profile="geoverum_landscape", W=3840, H=2160))
    pl = watermark.placement(tl)
    r = pl.text_rect()
    sx, st, sb = Units.of(3840, 2160).safe()
    assert r["x"] + r["w"] <= 3840 - sx + 1 and r["y"] + r["h"] <= sb + 1
    assert r["y"] > 2160 / 2 and r["x"] > 3840 / 2
    assert watermark.corner(tl, profiles.of(tl).watermark) == "bottom_right"


def test_good_portrait_watermark_defaults_to_top_right(project):
    _, tl = project(lambda d: d.update(profile="geoverum"))
    pl = watermark.placement(tl)
    assert pl.y < 200 and pl.x > 540


def test_good_landscape_overlay_slot_passes_o01(project):
    box = draft._slot("lower_third", "bottom", _S(1920, 1080))
    _, tl = project(_landscape(overlays_=[{"id": "c", "kind": "lower_third", "text": "Judul", "box": box}]))
    assert overlays.check(tl.segments[0], tl) == []


def test_bad_portrait_overlay_slot_leaves_the_landscape_safe_area(project):
    _, tl = project(_landscape(overlays_=[{"id": "c", "kind": "lower_third", "text": "Judul",
                                           "box": [90, 1500, 800, 180]}]))
    errs = overlays.check(tl.segments[0], tl)
    assert any("leaves the safe area" in e and "1920x1080" in e for e in errs)


class _S:
    def __init__(self, w, h):
        self.width, self.height = w, h


@pytest.mark.parametrize("kind", sorted(draft.OVERLAY_SLOTS_LANDSCAPE))
@pytest.mark.parametrize("pos", ["top", "bottom"])
@pytest.mark.parametrize("W,H", [(1920, 1080), (3840, 2160)])
def test_good_landscape_draft_slots_lie_inside_the_safe_area(kind, pos, W, H):
    x, y, w, h = draft._slot(kind, pos, _S(W, H))
    sx, st, sb = Units.of(W, H).safe()
    assert x >= sx - 1 and x + w <= W - sx + 1 and y >= st - 1 and y + h <= sb + 1


def test_good_landscape_delivery_metadata_is_not_shorts(project):
    _, tl = project(_landscape())
    m = deliver.metadata(tl)
    assert m["platforms"] == ["youtube"] and m["youtube"]["shorts"] is False and "reels" not in m
    assert "#Shorts" not in m["youtube"]["hashtags"]
    _, tp = project()
    assert deliver.metadata(tp)["youtube"]["shorts"] is True


def test_good_worker_ram_scales_with_pixel_count():
    assert resources.ram_per_worker_gb(1080, 1920) == resources.RAM_PER_WORKER_GB
    assert resources.ram_per_worker_gb(1920, 1080) == resources.RAM_PER_WORKER_GB
    assert resources.ram_per_worker_gb(3840, 2160) == pytest.approx(4 * resources.RAM_PER_WORKER_GB)
    assert resources.ram_per_worker_gb(720, 1280) == resources.RAM_PER_WORKER_GB


def test_good_landscape_theme_px_scale_except_template_scaled_keys():
    th = {"title_px": 100, "label_box_radius_px": 3, "pin_colour": "#fff", "flag": True, "ratio": 0.5}
    out = hyperframes.landscape_theme(th, 2.0)
    assert out == {"title_px": 200, "label_box_radius_px": 3, "pin_colour": "#fff", "flag": True, "ratio": 0.5}
    assert hyperframes.landscape_theme(th, 1.0) is th


def test_good_h264_level_check(tmp_path):
    out = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-frames:v", "3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    lvl = streams.verify_level(out, "test")
    assert 0 < lvl <= streams.MAX_H264_LEVEL
    with pytest.raises(EngineError, match="above"):
        streams.verify_level(out, "test", max_level=lvl - 1)
