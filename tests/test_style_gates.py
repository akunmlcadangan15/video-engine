"""Channel style gates (profile [gates.*]), Geoverum caption overrides, S01 silence, LRA, caption metrics, per-profile
draft prompt. Labelled GOOD and BAD cases."""
import subprocess
from types import SimpleNamespace

import pytest

from video_engine import audio, captions, config, draft, profiles, timeline
from video_engine.gates import layer2, run_preflight, style
from video_engine.util import EngineError

from conftest import words

FPS = 30
MAP = {"map_asset": "assets/map.jpg", "map_asset_id": "map", "camera_preset": "zoom_in"}
CUTS = profiles.CutGate(warn_per_10s=0.41, fail_per_10s=1.65, first_warn_s=12.0, first_fail_s=3.0)


def layout(*segs, profile="geoverum", vo=None, cs=None):
    """Timeline mutator. segs: (kind, seconds, extra dict); kind map | photo | video."""
    def mut(d):
        d["profile"] = profile
        out, f = [], 0
        for i, (kind, sec, extra) in enumerate(segs):
            n = round(sec * FPS)
            s = {"seg_index": i, "engine_type": "NATIVE_RUST_FFMPEG", "start_frame": f, "end_frame": f + n}
            if kind == "map":
                s.update(engine_type="HYPERFRAMES_GSAP",
                         gsap_scene_config={**MAP, "pins": [{"label": "Titik A", "x": 0.4, "y": 0.3}]})
            elif kind == "photo":
                s.update(source_image="assets/still.jpg", asset_id="still",
                         camera_move={"preset": "ken_burns_zoom_in", "intensity": 1.0})
            else:
                s.update(source_video="assets/clip_1080x1920_30.mp4", asset_id="clip")
            for k, v in (extra or {}).items():
                if k == "scene":
                    s["gsap_scene_config"].update(v)
                else:
                    s[k] = v
            out.append(s)
            f += n
        d["segments"] = out
        if vo:
            d["audio_chain"] = {"voiceover_path": vo}
        if cs:
            d["caption_style"] = cs
    return mut


def pins_every(sec: float, total: float, label="Titik"):
    return {"scene": {"pins": [{"label": f"{label} {i}", "x": 0.5, "y": 0.5, "at_frame": round(i * sec * FPS)}
                               for i in range(int(total / sec))]}}


def levels(fs, gate):
    return [f.level for f in fs if f.gate == gate]


def tone(path, lead_s=0.0, gap_at=None, gap_s=0.0, total=3.0):
    """Sine tone (-20 dB) with lead_s of digital silence and an optional silent gap."""
    expr = f"if(lt(t,{lead_s}),0,1)"
    if gap_at is not None:
        expr = f"{expr}*if(between(t,{gap_at},{gap_at + gap_s}),0,1)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={total}",
                    "-af", f"volume=-20dB,volume='{expr}':eval=frame", "-ar", "48000", str(path)], check=True)
    return path


# default profile: no style gates --------------------------------------------------------------------------------------
def test_good_default_profile_runs_no_style_gates(project, W):
    d, tl = project()
    assert profiles.of(tl).gates == profiles.StyleGates()
    assert style.run_style(tl, d, W(("Halo", 0, 10))) == []


def test_good_geoverum_runs_active_style_gates_and_no_cut_or_cadence_gate(project):
    d, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, None)))
    fs = style.run_style(tl, d, words(("Pagi", 0, 10), ("Titik", 10, 20)))
    assert {f.gate for f in fs} == {"H01", "C02", "N01", "K01", "K02", "D01", "P01", "P02", "P03"}
    assert not any("caption" in f.message and "per 10" in f.message for f in fs)


# H01 hook -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lead,lv", [(0.0, "pass"), (0.5, "warn"), (1.0, "fail")])
def test_h01_narration_start_good_and_bad(project, lead, lv):
    d, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, None)))
    tone(d / "vo.wav", lead_s=lead)
    tl.audio_chain.voiceover_path = "vo.wav"
    fs = [f for f in style.h01_hook(tl, d, words(("Pagi", 0, 10)), profiles.load("geoverum").gates.hook)
          if f.message.startswith("narration")]
    assert [f.level for f in fs] == [lv]


@pytest.mark.parametrize("start_s,lv", [(0.0, "pass"), (0.2, "pass"), (0.5, "warn"), (1.2, "fail")])
def test_h01_first_caption_good_and_bad(project, start_s, lv):
    d, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, None)))
    a = round(start_s * FPS)
    fs = [f for f in style.h01_hook(tl, d, words(("Pagi", a, a + 10)), profiles.load("geoverum").gates.hook)
          if f.message.startswith("first caption")]
    assert [f.level for f in fs] == [lv]


def test_bad_h01_opening_not_a_moving_map(project):
    g = profiles.load("geoverum").gates.hook
    d, tl = project(layout(("photo", 30, None), ("map", 30, pins_every(2, 30))))
    assert any(f.level == "fail" and "moving map" in f.message for f in style.h01_hook(tl, d, None, g))
    d, tl = project(layout(("map", 30, {"scene": {"camera_preset": "static"}}), ("photo", 30, None)))
    assert any(f.level == "fail" and "moving map" in f.message for f in style.h01_hook(tl, d, None, g))


@pytest.mark.parametrize("at_s,lv", [(0.4, "pass"), (2.0, "warn"), (3.5, "fail")])
def test_h01_first_graphic_good_and_bad(project, at_s, lv):
    pin = {"scene": {"pins": [{"label": "Titik", "x": 0.5, "y": 0.5, "at_frame": round(at_s * FPS)}]}}
    d, tl = project(layout(("map", 30, pin), ("photo", 30, None)))
    fs = [f for f in style.h01_hook(tl, d, None, profiles.load("geoverum").gates.hook) if "graphic" in f.message]
    assert [f.level for f in fs] == [lv]


def test_good_h01_default_pin_timing_is_the_renderer_timing(project):
    """The gate reads the same default appear frame the HyperFrames scene uses (0.4 s for the first pin)."""
    d, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    from video_engine import hyperframes
    sd = hyperframes.scene_data(tl.segments[0], tl, d)
    assert style.element_events(tl.segments[0], FPS)[0][0] == sd["pins"][0]["at_frame"] == 12


# C01 cut budget (when configured on a profile) ------------------------------------------------------------------------
def test_good_c01_one_late_cut(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    assert levels(style.c01_cuts(tl, CUTS), "C01") == ["pass", "pass"]


def test_good_c01_camera_moves_inside_a_segment_are_not_cuts(project):
    """Pans, zooms and pin reveals inside one map segment are no hard cut; only segment boundaries count."""
    _, tl = project(layout(("map", 60, pins_every(2, 60))))
    fs = style.c01_cuts(tl, CUTS)
    assert levels(fs, "C01") == ["pass"] and "0 hard cuts" in fs[0].message


def _hard_cuts_only(monkeypatch):
    """geoverum with [transitions] off: every boundary is a hard cut."""
    prof = profiles.load("geoverum")
    data = prof.data.model_copy(update={"transitions": profiles.TransitionsProfile()})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("geoverum", data, "x"))


def test_bad_c01_warn_rate_and_early_first_cut(project, monkeypatch):
    _, tl = project(layout(("map", 10, None), ("photo", 20, None), ("video", 15, None), ("photo", 15, None)))
    _hard_cuts_only(monkeypatch)
    fs = style.c01_cuts(tl, CUTS)   # 3 cuts / 60 s = 0.5 per 10 s, first at 10 s
    assert levels(fs, "C01") == ["warn", "warn"]


def test_bad_c01_fail_rate_and_first_cut_before_3_0_s(project):
    segs = [("map", 2, None)] + [("photo" if i % 2 else "video", 5, None) for i in range(11)]
    _, tl = project(layout(*segs))
    fs = style.c01_cuts(tl, CUTS)   # 11 cuts / 60 s = 1.83 per 10 s, first at 2 s
    assert levels(fs, "C01") == ["fail", "fail"]


# V01 visual type -------------------------------------------------------------------------------------------------------
def test_good_v01_cuts_change_visual_type(project):
    _, tl = project(layout(("map", 20, None), ("photo", 20, None), ("video", 20, None)))
    assert levels(style.v01_visual_type(tl), "V01") == ["pass"]


def test_good_v01_declared_visual_type_wins(project):
    _, tl = project(layout(("map", 20, None), ("photo", 20, None), ("photo", 20, {"visual_type": "archive"})))
    assert [s.kind for s in tl.segments] == ["map", "photo", "archive"]
    assert levels(style.v01_visual_type(tl), "V01") == ["pass"]


def test_bad_v01_same_type_adjacent_warns(project):
    _, tl = project(layout(("map", 20, None), ("photo", 20, None), ("photo", 20, None)))
    assert levels(style.v01_visual_type(tl), "V01") == ["warn"]


def test_bad_v01_cut_between_two_segments_of_one_map_fails(project):
    _, tl = project(layout(("map", 20, None), ("map", 20, None), ("photo", 20, None)))
    fs = style.v01_visual_type(tl)
    assert levels(fs, "V01") == ["fail"] and fs[0].seg == 1


def test_bad_visual_type_unknown_value(project):
    with pytest.raises(EngineError):
        project(layout(("map", 20, None), ("photo", 20, {"visual_type": "meme"})))


# N01 novelty -----------------------------------------------------------------------------------------------------------
def test_good_n01_new_element_every_2_s(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("map", 30, {**pins_every(2, 30),
                    "visual_type": "diagram"})))
    assert levels(style.n01_novelty(tl, profiles.load("geoverum").gates.novelty), "N01") == ["pass"]


def test_bad_n01_still_stretch_longer_than_3_s(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 4, None),
                           ("map", 26, {**pins_every(2, 26), "visual_type": "diagram"})))
    fs = style.n01_novelty(tl, profiles.load("geoverum").gates.novelty)
    assert levels(fs, "N01") == ["warn"] and fs[0].seg == 1 and "4.0 s" in fs[0].message


# K01 keyword return ------------------------------------------------------------------------------------------------------
def _end_label(text):
    return {"overlays": [{"id": "end", "kind": "label", "box": [140, 300, 800, 160], "text": text,
                          "start_frame": 0}]}


def test_good_k01_hook_keyword_back_at_the_end(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, _end_label("Surabaya hari ini"))))
    fs = style.k01_keyword_return(tl, words(("Surabaya", 0, 10)), profiles.load("geoverum").gates.keyword_return)
    assert levels(fs, "K01") == ["pass"] and "surabaya" in fs[0].message


def test_bad_k01_no_hook_keyword_at_the_end(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, _end_label("Selesai"))))
    fs = style.k01_keyword_return(tl, words(("Surabaya", 0, 10)), profiles.load("geoverum").gates.keyword_return)
    assert levels(fs, "K01") == ["warn"]


# K02 ending keyword on the final frame -----------------------------------------------------------------------------------
def test_good_k02_hook_keyword_still_on_screen_on_the_final_frame(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, _end_label("Surabaya hari ini"))))
    fs = style.k02_ending_keyword(tl, words(("Surabaya", 0, 10)), profiles.load("geoverum").gates.ending_keyword)
    assert levels(fs, "K02") == ["pass"] and "surabaya" in fs[0].message


def test_bad_k02_keyword_only_earlier_not_on_the_final_frame(project):
    gone = {"overlays": [{"id": "end", "kind": "label", "box": [140, 300, 800, 160], "text": "Surabaya",
                          "start_frame": 0, "end_frame": 20}]}
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, gone)))
    fs = style.k02_ending_keyword(tl, words(("Surabaya", 0, 10)), profiles.load("geoverum").gates.ending_keyword)
    assert levels(fs, "K02") == ["fail"] and "final" in fs[0].message


# C02 no CTA / end card ----------------------------------------------------------------------------------------------------
def test_good_c02_no_cta_phrase(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, _end_label("Surabaya hari ini"))))
    fs = style.c02_cta(tl, words(("Surabaya", 0, 10)), profiles.load("geoverum").gates.cta)
    assert levels(fs, "C02") == ["pass"]


def test_bad_c02_cta_in_captions_or_on_screen(project):
    _, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, _end_label("Jangan lupa subscribe"))))
    fs = style.c02_cta(tl, words(("Terima", 0, 10), ("kasih", 10, 20)), profiles.load("geoverum").gates.cta)
    assert levels(fs, "C02") == ["fail"] and "subscribe" in fs[0].message and "terima kasih" in fs[0].message


# D02 exact duration -------------------------------------------------------------------------------------------------------
def test_good_and_bad_d02_exact_duration(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    ok = profiles.ExactDurationGate(target_s=60.0, tolerance_s=0.0)
    bad = profiles.ExactDurationGate(target_s=45.0, tolerance_s=1.0)
    assert levels(style.d02_duration_exact(tl, ok), "D02") == ["pass"]
    assert levels(style.d02_duration_exact(tl, bad), "D02") == ["fail"]


# D01 duration ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sec,lv", [(45, "pass"), (90, "pass"), (30, "warn"), (100, "warn")])
def test_d01_duration_good_and_bad(project, sec, lv):
    _, tl = project(layout(("map", sec / 2, None), ("photo", sec / 2, None)))
    assert levels(style.d01_duration(tl, profiles.load("geoverum").gates.duration), "D01") == [lv]


# S01 silence on the final file ---------------------------------------------------------------------------------------
def test_good_s01_short_gap_passes(project, tmp_path):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    fs = style.silence_findings(tone(tmp_path / "a.wav", gap_at=1.0, gap_s=0.4), tl)
    assert levels(fs, "S01") == ["pass"]


def test_bad_s01_gap_longer_than_0_65_s_fails(project, tmp_path):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    fs = style.silence_findings(tone(tmp_path / "a.wav", gap_at=1.0, gap_s=1.0), tl)
    assert levels(fs, "S01") == ["fail"] and "at 1.0" in fs[0].message


def test_bad_s01_trailing_gap_without_silence_end_is_counted(tmp_path):
    gaps = style.silence_gaps(tone(tmp_path / "a.wav", gap_at=2.0, gap_s=2.0), -40.0, 0.65)
    assert len(gaps) == 1 and gaps[0][1] == pytest.approx(1.0, abs=0.05)


def test_bad_s01_ffmpeg_failure_is_a_gate_error(tmp_path):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")
    with pytest.raises(EngineError, match="S01 silencedetect failed"):
        style.silence_gaps(broken, -40.0, 0.65)


def test_good_s01_off_in_default_profile(project, tmp_path):
    _, tl = project()
    assert style.silence_findings(tone(tmp_path / "a.wav", gap_at=1.0, gap_s=1.0), tl) == []


# LRA and caption metrics (report only) -------------------------------------------------------------------------------
def test_good_loudness_reports_lra(tmp_path):
    m = audio.loudness(tone(tmp_path / "a.wav", total=4.0))
    assert set(m) == {"I", "TP", "LRA"} and isinstance(m["LRA"], float)


def test_good_caption_metrics_are_numbers_only(project):
    _, tl = project()   # 180 frames = 6 s
    m = captions.metrics([(0, 30, "a"), (30, 90, "b"), (90, 120, "c")], tl)
    assert m == {"events": 3, "per_10s": 5.0, "mean_s": 1.33}
    assert captions.metrics([], tl) == {"events": 0, "per_10s": 0.0, "mean_s": None}


# Geoverum captions ---------------------------------------------------------------------------------------------------
GEO_WORDS = words(("Pagi", 0, 8), ("sepuluh", 8, 16), ("November", 16, 30), ("seribu", 30, 40), ("sembilan", 40, 50))


def test_good_geoverum_caption_style_fixed_y_two_words(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    txt, ev = captions.compile_ass(GEO_WORDS, tl)
    style_line = next(l for l in txt.splitlines() if l.startswith("Style:")).split(",")
    assert style_line[1:3] == ["Inter", "84"] and style_line[7] == "-1"               # font, size, bold on
    assert style_line[15] == "1" and style_line[16] == "0"                            # no box, no outline
    assert all(e[6] == 1382.0 for e in ev) and all(len(e[2].split()) <= 2 for e in ev)
    assert "\\1c" not in txt                                                            # no per-word highlight


def test_good_project_y_override_beats_profile_fixed_y(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None), cs={"y_overrides": [[0, 20, 1100]]}))
    ev = captions.compile_ass(GEO_WORDS, tl)[1]
    assert ev[0][6] == 1100.0 and ev[-1][6] == 1382.0


def test_good_default_captions_not_fixed(project):
    _, tl = project()
    assert "fixed_y" not in profiles.caption_style(tl)
    assert captions.overrides(tl, None) == []


def test_bad_geoverum_caption_on_an_element_still_fails_r63(project):
    """Clearance is 0: a caption intersecting an element box fails R63 before placement."""
    box = {"overlays": [{"id": "low", "kind": "reserve", "box": [100, 1340, 880, 80]}]}
    _, tl = project(layout(("photo", 30, box), ("map", 30, None)))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    fs = layer2.r63_caption_clearance(tl, ev, layer2.element_boxes(tl, None, {}))
    assert fs and all(f.level == "fail" for f in fs)
    assert profiles.caption_style(tl)["place_band"] == [0.60, 0.76]


def test_good_geoverum_caption_shifts_up_inside_band_past_a_pinhead(project):
    """GOOD: a pinhead-sized box just under the fixed caption y (1382): Geoverum auto placement lifts the captions,
    and the new centre stays inside the approved band 0.60-0.76 H (1152-1459 px)."""
    box = {"overlays": [{"id": "pinhead", "kind": "reserve", "box": [500, 1395, 80, 60]}]}
    _, tl = project(layout(("photo", 30, box), ("map", 30, None)))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    boxes = layer2.element_boxes(tl, None, {})
    assert layer2.r63_caption_clearance(tl, ev, boxes)
    ovs, fs = layer2.place_captions(tl, ev, boxes)
    assert ovs and levels(fs, "R63") == ["fix"]
    assert all(0.60 * 1920 <= y <= 0.76 * 1920 and y < 1382 for _, _, y in ovs)
    assert layer2.r63_caption_clearance(tl, layer2.caption_events(tl, GEO_WORDS, ovs), boxes) == []


def test_bad_geoverum_band_too_narrow_fails_instead_of_leaving_band(project):
    """BAD: a box covering the whole band 1152-1459 (plus caption height): no y inside the band clears it, so
    placement fails rather than moving the caption out of the approved band."""
    box = {"overlays": [{"id": "wide", "kind": "reserve", "box": [60, 1080, 960, 400]}]}
    _, tl = project(layout(("photo", 30, box), ("map", 30, None)))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    ovs, fs = layer2.place_captions(tl, ev, layer2.element_boxes(tl, None, {}))
    assert ovs == [] and levels(fs, "R63") == ["fail"] and "place_band" in fs[0].message


def test_bad_place_band_out_of_range(tmp_path, monkeypatch):
    from video_engine import config
    from video_engine.util import EngineError
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[caption_style]\nplace_band = [0.8, 0.7]\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError, match="place_band"):
        profiles.load("chan")


# Z01 caption bottom limit (Shorts safe zone) --------------------------------------------------------------------------
def ink_rows(tl, ws, tmp_path):
    """Burns the compiled .ass with the final-mux subtitle filter over mid grey; returns (event, first ink row, last
    ink row) per caption from the rendered frame (any pixel differing from the background)."""
    import numpy as np
    from PIL import Image
    txt, ev = captions.compile_ass(ws, tl)
    d = tmp_path / "ink"
    d.mkdir()
    (d / "captions.ass").write_text(txt)
    vf, cwd, env = audio.subtitle_filter(d / "captions.ass")
    S = tl.settings
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={S.width}x{S.height}:r={S.fps}",
                    "-vf", vf, "-frames:v", str(ev[-1][1]), "-pix_fmt", "rgb24", "f_%03d.png"], cwd=cwd, env=env, check=True)
    out = []
    for e in ev:
        a = np.asarray(Image.open(d / f"f_{(e[0] + e[1]) // 2 + 1:03d}.png")).astype(int)
        bg = np.median(a, axis=(0, 1))  # lavfi mid-grey lands on 126 or 128 by ffmpeg build; never assume one
        rows = np.where((np.abs(a - bg).max(axis=2) >= 1).any(axis=1))[0]
        out.append((e, int(rows.min()), int(rows.max())))
    return out


def test_good_geoverum_caption_bottom_within_max_bottom_y(project):
    """GOOD: Geoverum fixed_y 1382 keeps every caption box bottom <= max_bottom_y 1480 (Z01 pass)."""
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    assert profiles.caption_style(tl)["max_bottom_y"] == 1480
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    assert levels(layer2.z01_caption_bottom(tl, ev), "Z01") == ["pass"]
    assert all(layer2.caption_box(e)[3] <= 1480 for e in ev)


def test_good_rendered_caption_ink_inside_caption_box(project, tmp_path):
    """GOOD: the libass-rendered ink (glyphs, descenders, soft shadow) of Geoverum captions ends inside the caption box
    Z01 checks, and at or above 1480 px."""
    ws = words(("gypsy", 0, 10), ("jq", 10, 20), ("Agustus", 30, 40), ("(Jepang)", 40, 50))
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    for e, top, bottom in ink_rows(tl, ws, tmp_path):
        box = layer2.caption_box(e)
        assert box[1] <= top and bottom <= box[3] <= 1480, (e[2], top, bottom, box)


def test_bad_fixed_y_1500_exceeds_max_bottom_y_1480(project):
    """BAD: a caption centre 1500 under max_bottom_y 1480 fails Z01 for every caption."""
    _, tl = project(layout(("map", 30, None), ("photo", 30, None), cs={"fixed_y": 1500}))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    fs = layer2.z01_caption_bottom(tl, ev)
    assert fs and len(fs) == len(ev) and all(f.level == "fail" and "max_bottom_y 1480" in f.message for f in fs)


def test_bad_project_y_override_below_max_bottom_y_fails_z01(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None), cs={"y_overrides": [[0, 20, 1500]]}))
    fs = layer2.z01_caption_bottom(tl, layer2.caption_events(tl, GEO_WORDS, []))
    assert fs and all(f.level == "fail" and "y=1500" in f.message for f in fs)


def test_bad_z01_preflight_fails_geoverum_build_at_1500(project, W):
    d, tl = project(layout(("photo", 30, None), ("photo", 30, None), cs={"fixed_y": 1500}))
    rep = run_preflight(tl, d, GEO_WORDS, dom=False, snapshots=False)
    assert "fail" in levels(rep.findings, "Z01")


def test_good_default_profile_has_no_max_bottom_y(project):
    _, tl = project()
    assert "max_bottom_y" not in profiles.caption_style(tl)
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    assert layer2.z01_caption_bottom(tl, ev) == []


def test_good_placement_shift_never_below_max_bottom_y(project):
    """GOOD: an element box over the caption band; automatic placement moves the captions, never below max_bottom_y."""
    box = {"overlays": [{"id": "band", "kind": "reserve", "box": [100, 1280, 880, 200]}]}
    cs = {"auto_place": True, "clearance": 40, "place_band": [0.3, 0.72]}   # wider than the Geoverum band
    _, tl = project(layout(("photo", 30, box), ("map", 30, None), cs=cs))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    boxes = layer2.element_boxes(tl, None, {})
    assert layer2.r63_caption_clearance(tl, ev, boxes)
    ovs, fs = layer2.place_captions(tl, ev, boxes)
    assert ovs and levels(fs, "R63") == ["fix"]
    ev2 = layer2.caption_events(tl, GEO_WORDS, ovs)
    assert layer2.r63_caption_clearance(tl, ev2, boxes) == []
    assert levels(layer2.z01_caption_bottom(tl, ev2), "Z01") == ["pass"]
    assert ovs[0][2] < 1280   # moved up: below the box the bottom would pass 1480


def test_bad_placement_without_room_above_max_bottom_y_fails(project):
    """BAD: the element box covers everything from the safe top to max_bottom_y; the only clear room is below the
    limit, so placement fails instead of pushing the caption there."""
    box = {"overlays": [{"id": "wall", "kind": "reserve", "box": [60, 100, 960, 1400]}]}
    _, tl = project(layout(("photo", 30, box), ("map", 30, None), cs={"auto_place": True, "clearance": 0}))
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    ovs, fs = layer2.place_captions(tl, ev, layer2.element_boxes(tl, None, {}))
    assert ovs == [] and levels(fs, "R63") == ["fail"]


def test_bad_max_bottom_y_out_of_range(tmp_path, monkeypatch):
    d = tmp_path / "p"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[caption_style]\nmax_bottom_y = 0\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError, match="max_bottom_y"):
        profiles.load("chan")


def test_bad_fixed_y_out_of_range(tmp_path, monkeypatch):
    d = tmp_path / "p"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[caption_style]\nfixed_y = 2500\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError, match="fixed_y"):
        profiles.load("chan")


@pytest.mark.parametrize("gates", [
    "[gates.hook]\nvo_warn_s = 0.9\nvo_fail_s = 0.8\ncaption_warn_s = 0.2\ncaption_fail_s = 1.0\n"
    "graphic_warn_s = 1.5\ngraphic_fail_s = 3.0\n",
    "[gates.cuts]\nwarn_per_10s = 0.4\nfail_per_10s = 1.6\nfirst_warn_s = 5\nfirst_fail_s = 6.5\n",
    "[gates.duration]\nmin_s = 90\nmax_s = 45\n",
    "[gates.novelty]\nmax_gap_s = 3\nextra = 1\n",
    "[gates.cadence]\nmax_s = 0.8\n",
])
def test_bad_style_gate_tables_rejected(tmp_path, monkeypatch, gates):
    d = tmp_path / "p"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text(gates)
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError):
        profiles.load("chan")


# per-profile draft prompt --------------------------------------------------------------------------------------------
def _inp(profile):
    return SimpleNamespace(settings=timeline.Settings(), assets=[], credit_lines=[], total_frames=900, words=[],
                           pause_report=SimpleNamespace(segments=[(0, 900)]), script="x", profile=profile)


def test_good_default_draft_prompt_keeps_engine_rules():
    p = draft.prompt(_inp(None))
    assert "Length 1.5-7.0 s each (45-210 frames)." in p and "\n6. " not in p


def test_good_geoverum_draft_prompt_adds_channel_rules():
    p = draft.prompt(_inp("geoverum"))
    rules = profiles.load("geoverum").draft.rules
    assert "Length 0.8-30.0 s each (24-900 frames)." in p
    assert all(f"\n{6 + i}. {r}" in p for i, r in enumerate(rules)) and p.endswith("matching the schema.")


def test_bad_draft_range_inverted(tmp_path, monkeypatch):
    d = tmp_path / "p"
    d.mkdir()
    (d / "default.toml").write_text((config.PROFILES_DIR / "default.toml").read_text())
    (d / "chan.toml").write_text("[draft]\nmin_seg_s = 8\nmax_seg_s = 7\n")
    monkeypatch.setattr(config, "PROFILES_DIR", d)
    with pytest.raises(EngineError):
        profiles.load("chan")


# preflight wiring ---------------------------------------------------------------------------------------------------
def test_good_preflight_reports_style_gates_for_geoverum_only(project):
    d, tl = project(layout(("map", 30, pins_every(2, 30)), ("photo", 30, None)))
    rep = run_preflight(tl, d, None, dom=False, snapshots=False)
    assert {"H01", "D01"} <= {f.gate for f in rep.findings} and "style" in rep.timings
    d, tl = project()
    rep = run_preflight(tl, d, None, dom=False, snapshots=False)
    assert not {"H01", "C01", "V01", "N01", "K01", "D01"} & {f.gate for f in rep.findings}


# GOOD / BAD: Geoverum caption outline + drop shadow; Z01 counts the shadow reach -------------------------------------
def test_good_geoverum_caption_thin_outline_and_straight_soft_shadow(project):
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    txt, ev = captions.compile_ass(GEO_WORDS, tl)
    shadow = [l for l in txt.splitlines() if l.startswith("Dialogue: 0,")]
    assert shadow and all("\\3a&H73&" in l and "\\blur4" in l and "\\pos(540,1385)" in l for l in shadow)   # 0 3 px, .55
    assert all("BorderStyle" not in l for l in shadow) and layer2.caption_pad(tl) == 0
    assert levels(layer2.z01_caption_bottom(tl, ev), "Z01") == ["pass"]


def test_bad_deep_caption_shadow_is_counted_by_z01(project, tmp_path):
    """BAD: a 60 px shadow drop at fixed_y 1382 puts shadow ink below 1480: caption_pad extends the box and Z01 fails,
    and the rendered ink stays inside the padded box."""
    _, tl = project(layout(("map", 30, None), ("photo", 30, None), cs={"shadow_dy": 60}))
    pad = layer2.caption_pad(tl)
    assert pad > 25
    ev = layer2.caption_events(tl, GEO_WORDS, [])
    assert set(levels(layer2.z01_caption_bottom(tl, ev), "Z01")) == {"fail"}
    for e, _, bottom in ink_rows(tl, words(("gypsy", 0, 10)), tmp_path):
        assert bottom <= layer2.caption_box(e, pad)[3]


def test_good_default_caption_style_has_no_pad(project):
    _, tl = project(layout(("map", 30, None), profile="default"))
    assert layer2.caption_pad(tl) == 0.0


# GOOD / BAD: no captions (a video without narration) ------------------------------------------------------------------
def test_good_empty_captions_pass_or_skip_every_caption_gate(project):
    d, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    ev = layer2.caption_events(tl, [], [])
    assert ev == [] and levels(layer2.z01_caption_bottom(tl, ev), "Z01") == ["pass"]
    fs, ev2 = layer2.r62_caption_margins(tl, [])
    assert ev2 == [] and levels(fs, "R62") == ["pass"]
    st = style.run_style(tl, d, [])
    assert not [f for f in st if f.gate in ("H01", "W01", "L22") and f.level == "fail"]


def test_bad_empty_captions_still_fail_an_invalid_max_bottom_y(project):
    _, tl = project(layout(("map", 30, None), cs={"max_bottom_y": "low"}))
    assert levels(layer2.z01_caption_bottom(tl, []), "Z01") == ["fail"]


def test_good_late_pin_does_not_block_earlier_captions(project):
    """GOOD: R63 caption clearance is time-resolved. A pinhead first seen at t=20 s in the caption band holds from
    the previous probe (t=3 s) on: captions before that clear; a caption shown while the pin is there fails."""
    _, tl = project(layout(("map", 30, None), ("photo", 30, None)))
    pin = {"id": "pinhead:2", "text": "", "x": 500, "y": 1340, "w": 44, "h": 44, "opacity": 1}
    items = [{"t": 1.0, "rects": []}, {"t": 3.0, "rects": []}, {"t": 20.0, "rects": [pin]}]
    dom = {0: {"times": items, "all_times": items}}
    boxes = layer2.element_boxes(tl, None, dom)
    assert [r["frames"] for r in boxes[0] if r["id"] == "pinhead:2"] == [(90, 900)]
    assert layer2.r63_caption_clearance(tl, layer2.caption_events(tl, GEO_WORDS, []), boxes) == []
    late = words(("Malaysia", 600, 620))
    fs = layer2.r63_caption_clearance(tl, layer2.caption_events(tl, late, []), boxes)
    assert levels(fs, "R63") == ["fail"] and "pinhead:2" in fs[0].message
