"""Native drawn overlays (text_card, label, lower_third) and gate O01: good and bad cases."""
from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from video_engine import captions, native, overlays
from video_engine.gates import layer1, layer2
from video_engine.timeline import Overlay
from video_engine.util import EngineError

CARD = {"id": "card", "kind": "text_card", "box": [90, 300, 900, 300], "text": "Surabaya, 10 November 1945",
        "subtitle": "Pertempuran terbesar revolusi"}
LABEL = {"id": "lbl", "kind": "label", "box": [240, 1000, 600, 110], "text": "Jembatan Merah"}


def frame_rgb(path, idx, W=1080, H=1920) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{idx})", "-fps_mode", "passthrough",
                           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True, check=True).stdout[: W * H * 3]


def px(buf, x, y, W=1080):
    i = (y * W + x) * 3
    return tuple(buf[i:i + 3])


def test_good_fit_and_png_size(tmp_path):
    ov = Overlay(**CARD)
    lay = overlays.fit(ov)
    assert lay.title_px >= overlays.MIN_FONT_PX and lay.sub_lines
    from PIL import Image
    im = Image.open(overlays.render_png(ov, tmp_path / "c.png"))
    assert im.size == (900, 300) and im.mode == "RGBA" and im.getpixel((450, 150))[3] > 150
    assert im.getpixel((2, 150))[:3] == overlays.ACCENT[:3]           # accent bar on the left edge


def test_good_label_plate_hugs_the_text_in_theme_style():
    from video_engine import profiles
    th = profiles.load("geoverum").theme
    short, long = (overlays.render_im(Overlay(**dict(LABEL, text=t)), 1920, None, th) for t in ("JALAN", "JALUR KERETA API"))
    assert short.size == long.size == (600, 110)                       # the PNG keeps the overlay box size
    (x0, y0, x1, y1), (lx0, _, lx1, _) = short.getchannel("A").getbbox(), long.getchannel("A").getbbox()
    assert x1 - x0 < 260 and x1 - x0 < lx1 - lx0                         # plate width follows the text
    assert y1 - y0 < 0.8 * 110 and abs((x0 + x1) / 2 - 300) <= 1          # height follows the font, centred in the box
    fill = overlays._hex(th.label_box_colour)
    assert short.getpixel((x0 + 3, (y0 + y1) // 2)) == fill               # opaque theme colour (label_box_colour)
    assert overlays.fit(Overlay(**LABEL), max_px=th.label_box_px).title_px <= th.label_box_px   # font capped by theme


def test_bad_text_does_not_fit():
    ov = Overlay(id="x", kind="label", box=[100, 400, 160, 40], text="Teks yang terlalu panjang untuk kotak kecil")
    with pytest.raises(EngineError, match="does not fit"):
        overlays.fit(ov)


def test_bad_drawn_kind_needs_text_and_valid_box():
    with pytest.raises(ValidationError, match="needs text"):
        Overlay(id="x", kind="label", box=[100, 400, 300, 80])
    with pytest.raises(ValidationError, match="w, h > 0"):
        Overlay(id="x", kind="label", box=[100, 400, 0, 80], text="a")
    with pytest.raises(ValidationError, match="end_frame"):
        Overlay(id="x", kind="label", box=[100, 400, 300, 80], text="a", start_frame=10, end_frame=5)
    assert not Overlay(id="r", box=[1, 1, 2, 2]).drawn                # reserve (legacy) is never drawn


def test_good_O01_pass(project):
    _, tl = project(lambda d: d["segments"][0].update(overlays=[CARD, LABEL]))
    assert [f.level for f in layer1.o01_overlays(tl)] == ["pass"]


def test_bad_O01_safe_margin_scales_with_width_not_height(project):
    """At 1440x1920 the side margin is 80 px (60 x W/1080), so a box at x=70 leaves the safe area."""
    def mut(d):
        d["settings"].update(width=1440)
        d["segments"][0]["overlays"] = [{"id": "edge", "kind": "label", "box": [70, 300, 600, 110], "text": "Tepi"}]

    _, tl = project(mut)
    fs = layer1.o01_overlays(tl)
    assert any(f.level == "fail" and "safe area" in f.message for f in fs)


@pytest.mark.parametrize("ovs,msg", [
    ([dict(CARD, box=[10, 300, 900, 300])], "safe area"),
    ([dict(LABEL, box=[240, 1000, 120, 40], text="Teks panjang sekali tidak muat")], "does not fit"),
    ([CARD, dict(LABEL, box=[200, 400, 600, 110])], "overlap"),
    ([dict(LABEL, start_frame=60)], "outside the segment"),
    ([dict(LABEL, end_frame=61)], "outside the segment"),
    ([dict(LABEL, start_frame=30, end_frame=999)], "outside the segment"),
], ids=["safe-area", "fit", "overlap", "range", "end-past-length", "end-far-past-length"])
def test_bad_O01(project, ovs, msg):
    _, tl = project(lambda d: d["segments"][0].update(overlays=ovs))
    fs = layer1.o01_overlays(tl)
    assert any(f.level == "fail" and msg in f.message for f in fs)


def test_good_O01_overlap_allowed_when_not_simultaneous(project):
    ovs = [dict(CARD, end_frame=30), dict(LABEL, box=[200, 400, 600, 110], start_frame=30)]
    _, tl = project(lambda d: d["segments"][0].update(overlays=ovs))
    assert [f.level for f in layer1.o01_overlays(tl)] == ["pass"]


def test_bad_O01_on_hyperframes_segment(project):
    _, tl = project(lambda d: d["segments"][2].update(overlays=[LABEL]))
    assert any("only supported on native" in f.message for f in layer1.o01_overlays(tl))


@pytest.mark.parametrize("seg", [0, 1], ids=["camera-still", "encode-video"])
def test_good_overlay_drawn_in_box_and_timed(project, tmp_path, seg):
    lbl = dict(LABEL, start_frame=20, end_frame=50)
    p, plain = project()
    _, tl = project(lambda d: d["segments"][seg].update(overlays=[lbl]))
    s0, s1 = plain.segments[seg], tl.segments[seg]
    j0, j1 = native.plan(s0, plain, p), native.plan(s1, tl, p)
    assert j1.key != j0.key and j1.mode in ("camera", "encode")
    a, _, _ = native.render(j0, plain, tmp_path / "a", use_cache=False)
    b, _, _ = native.render(j1, tl, tmp_path / "b", use_cache=False)
    from video_engine.util import count_frames
    assert count_frames(b) == s1.n_frames
    cx, cy = 345, 1055                                              # on the plate, left of the text
    on_a, on_b = frame_rgb(a, 35), frame_rgb(b, 35)
    assert sum(abs(x - y) for x, y in zip(px(on_a, cx, cy), px(on_b, cx, cy))) > 30               # dark plate drawn
    assert px(on_b, 260, cy) == pytest.approx(px(on_a, 260, cy), abs=6)       # box beyond the plate stays clear
    assert px(on_b, 1000, 200) == pytest.approx(px(on_a, 1000, 200), abs=6)                      # outside box untouched
    off_a, off_b = frame_rgb(a, 5), frame_rgb(b, 5)                                                # before start_frame
    assert sum(abs(x - y) for x, y in zip(px(off_a, cx, cy), px(off_b, cx, cy))) < 20


def test_bad_overlay_fade_change_misses_the_native_cache(project):
    """BAD before the fix: `[motion] overlay_fade_s` is baked into the drawn-overlay fade but was missing from the
    native segment key, so a changed fade reused the stale cached segment."""
    lbl = dict(LABEL, start_frame=20, end_frame=50)
    keys = []
    for fade in (0.2, 0.9):
        p, tl = project(lambda d, f=fade: (d["segments"][0].update(overlays=[lbl]),
                                           d.update(profile_overrides={"motion": {"overlay_fade_s": f}})))
        keys.append(native.plan(tl.segments[0], tl, p).key)
    assert keys[0] != keys[1]


def test_good_snapshot_and_preview_include_overlay(project, tmp_path):
    p, tl = project(lambda d: d["segments"][1].update(overlays=[LABEL]))
    _, plain = project()
    j1, j0 = native.plan(tl.segments[1], tl, p), native.plan(plain.segments[1], plain, p)
    a, b = native.snapshot_rgb(j0, plain, 30, 270, 480), native.snapshot_rgb(j1, tl, 30, 270, 480)
    assert a != b
    from PIL import Image
    im = Image.open(native.preview_frame(j1, tl, 30, tmp_path / "pv.png"))
    assert im.size == (1080, 1920)


def test_good_caption_avoids_drawn_overlay(project, W):
    p, tl = project(lambda d: d["segments"][0].update(overlays=[dict(LABEL, box=[200, 1250, 680, 130])]))
    ws = W(("Halo", 5, 20))
    ev = captions.compile_ass(ws, tl)[1]
    boxes = layer2.element_boxes(tl, p, {})
    assert layer2.r63_caption_clearance(tl, ev, boxes)                        # BAD: label in the caption slot
    ovs, _ = layer2.place_captions(tl, ev, boxes)
    assert not layer2.r63_caption_clearance(tl, captions.compile_ass(ws, tl, ovs)[1], boxes)


# [print] date heading ----------------------------------------------------------------------------------------------
HEAD = {"id": "date", "kind": "heading", "on": "photo", "box": [140, 300, 800, 100], "text": "30 Oktober 1945"}


def geo(*ovs, seg=0):
    def mut(d):
        d["profile"] = "geoverum"
        d["segments"][seg].update(overlays=list(ovs))
    return mut


def test_good_heading_O01_T01_pass_and_png_inks(project, tmp_path):
    from PIL import Image
    from video_engine import profiles
    _, tl = project(geo(HEAD, dict(LABEL, box=[240, 1000, 600, 110])))
    fs = layer1.o01_overlays(tl)
    assert {(f.gate, f.level) for f in fs} == {("O01", "pass"), ("T01", "pass")}
    pr = profiles.of(tl).print
    for on, ink in (("light", pr.heading_ink), ("photo", pr.heading_light)):
        im = Image.open(overlays.render_png(Overlay(**dict(HEAD, on=on)), tmp_path / f"{on}.png", 1920, pr))
        solid = [p for p in im.getdata() if p[3] == 255]
        want = tuple(int(ink[i:i + 2], 16) for i in (1, 3, 5))
        assert solid and max(abs(a - b) for a, b in zip(max(solid, key=solid.count)[:3], want)) <= 2
        dark = [p for p in im.getdata() if 0 < p[3] < 255 and sum(p[:3]) < 60]           # the soft black shadow
        assert bool(dark) == (on == "photo")
    hl = overlays.heading_layout(Overlay(**HEAD), pr)
    assert abs(hl["base"] - (100 - overlays.HEADING_PAD)) < 1e-6 and abs(hl["rules"][1][1] - hl["rules"][1][0] - 120) < 1e-6


def test_good_heading_drawn_on_native_segment(project, tmp_path):
    p, tl = project(geo(HEAD))
    _, plain = project(geo())
    j0, j1 = native.plan(plain.segments[0], plain, p), native.plan(tl.segments[0], tl, p)
    assert j0.key != j1.key
    a, b = native.snapshot_rgb(j0, plain, 30, 1080, 1920), native.snapshot_rgb(j1, tl, 30, 1080, 1920)
    diff = [y for y in range(300, 400) if any(abs(a[i] - b[i]) > 40 for x in range(160, 920, 4)
                                              for i in [((y * 1080 + x) * 3)])]
    assert diff and min(diff) >= 340 and max(diff) <= 392          # caps and rules sit just above the baseline 384


@pytest.mark.parametrize("ov,msg", [
    (dict(HEAD, box=[140, 360, 800, 100]), "baseline y 444"),
    (dict(HEAD, box=[100, 300, 800, 100]), "not the frame centre"),
    (dict(HEAD, box=[240, 300, 600, 100]), "does not fit"),
], ids=["baseline", "centre", "fit"])
def test_bad_heading_O01(project, ov, msg):
    _, tl = project(geo(ov))
    assert any(f.level == "fail" and msg in f.message for f in layer1.o01_overlays(tl))


def test_bad_heading_needs_print_and_tone(project):
    _, tl = project(lambda d: d["segments"][0].update(overlays=[HEAD]))          # default profile: no [print]
    assert any(f.level == "fail" and "[print]" in f.message for f in layer1.o01_overlays(tl))
    with pytest.raises(ValidationError, match="`on`"):
        Overlay(**{k: v for k, v in HEAD.items() if k != "on"})
    with pytest.raises(ValidationError, match="`on`"):
        Overlay(**dict(LABEL, on="photo"))
    with pytest.raises(ValidationError, match="one line"):
        Overlay(**dict(HEAD, subtitle="x"))


def test_bad_T01_too_many_typefaces_at_once(project):
    _, tl = project(geo(HEAD, dict(LABEL, box=[240, 1000, 600, 110])))
    s = tl.segments[0]
    assert overlays.typefaces(s, 2) == [] and "2 typefaces (Cinzel, Inter)" in overlays.typefaces(s, 1)[0]
    _, tl = project(geo(dict(HEAD, end_frame=20), dict(LABEL, box=[240, 1000, 600, 110], start_frame=20)))
    assert overlays.typefaces(tl.segments[0], 1) == []                         # one after the other
