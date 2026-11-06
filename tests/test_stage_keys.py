"""Stage cache keys cover every input of their product: engine sources, ffmpeg, x264
threads, Pillow, captions (.ass) and fonts, both neighbouring segments of a transition. BAD = an input changed and the
old product must not be reused (the key must change); GOOD = an unrelated change keeps every visual key."""
import shutil
from pathlib import Path

import pytest

from video_engine import audio, grade, hyperframes, manifest, native, profiles, toolchain, transitions

from conftest import engine_clip

OVERLAY = {"id": "t", "box": [100, 100, 600, 200], "kind": "label", "text": "Uji"}


def native_keys(p, tl):
    return [native.plan(s, tl, p).key for s in tl.segments[:2]]


def hf_key(p, tl):
    return hyperframes.keys(tl.segments[2], tl, p)[0]


def edited_copies(tmp_path, files, line="_EDITED = 1"):
    """Copies of engine source files with one appended line. The default line is a statement (a change of behaviour);
    cache keys ignore comments, docstrings and formatting (util.code_hash)."""
    out = []
    for f in files:
        dst = tmp_path / "code" / Path(f).name
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(f, dst)
        with open(dst, "a") as fh:
            fh.write(f"\n{line}\n")
        out.append(dst)
    return tuple(out)


# native segments ----------------------------------------------------------------------------------------------------
def test_bad_ffmpeg_upgrade_invalidates_native_keys(project, monkeypatch):
    p, tl = project()
    k0 = native_keys(p, tl)
    monkeypatch.setattr(toolchain, "ffmpeg_version", lambda exe: "ffmpeg version 99")
    assert all(a != b for a, b in zip(k0, native_keys(p, tl)))


@pytest.mark.parametrize("i", [0, 1])
def test_bad_native_or_overlay_source_edit_invalidates_native_keys(project, monkeypatch, tmp_path, i):
    p, tl = project()
    k0 = native_keys(p, tl)
    code = list(native.CODE)
    code[i] = edited_copies(tmp_path, [code[i]])[0]
    monkeypatch.setattr(native, "CODE", tuple(code))
    assert all(a != b for a, b in zip(k0, native_keys(p, tl)))


def test_bad_x264_thread_count_invalidates_native_keys(project, monkeypatch):
    p, tl = project()
    k0 = native_keys(p, tl)
    monkeypatch.setattr(native.resources, "X264_THREADS", 3)
    assert all(a != b for a, b in zip(k0, native_keys(p, tl)))


def test_bad_pillow_version_invalidates_drawn_overlay_key_only(project, monkeypatch):
    import PIL
    p, tl = project(lambda d: d["segments"][0].update(overlays=[OVERLAY]))
    k0 = native_keys(p, tl)
    monkeypatch.setattr(PIL, "__version__", "0.0.1")
    k1 = native_keys(p, tl)
    assert k1[0] != k0[0] and k1[1] == k0[1]


# HyperFrames segments ------------------------------------------------------------------------------------------------
def test_bad_hyperframes_source_edit_invalidates_hf_key(project, monkeypatch, tmp_path):
    p, tl = project()
    k0 = hf_key(p, tl)
    monkeypatch.setattr(hyperframes, "CODE", edited_copies(tmp_path, hyperframes.CODE))
    assert hf_key(p, tl) != k0


def test_bad_legibility_source_edit_invalidates_legibility_segment_key(project, monkeypatch):
    p, tl = project()
    geo = profiles.load("geoverum")
    monkeypatch.setattr(profiles, "of", lambda _tl: geo)
    assert "legibility" in hyperframes.scene_data(tl.segments[2], tl, p)
    k0 = hf_key(p, tl)
    monkeypatch.setattr(hyperframes.legibility, "code_hash", lambda: "edited")
    assert hf_key(p, tl) != k0


# grade ----------------------------------------------------------------------------------------------------------------
def test_bad_grade_source_or_ffmpeg_change_invalidates_grade_key(project, monkeypatch, tmp_path):
    p, tl = project()
    src = p / "assets" / "still.jpg"
    k0 = grade._key(tl.segments[0], [], [], src)
    monkeypatch.setattr(grade, "__file__", str(edited_copies(tmp_path, [grade.__file__])[0]))
    k1 = grade._key(tl.segments[0], [], [], src)
    monkeypatch.setattr(toolchain, "ffmpeg_version", lambda exe: "ffmpeg version 99")
    k2 = grade._key(tl.segments[0], [], [], src)
    assert len({k0, k1, k2}) == 3


# composite (captions, fonts, transitions) ----------------------------------------------------------------------------
def _composite_key(tmp_path, seg_files, ass, extra_inputs=()):
    out, concat = tmp_path / "composite.mp4", tmp_path / "c.txt"
    cmd = ["ffmpeg", *[str(f) for f in [*seg_files, *extra_inputs]], "-vf", f"subtitles={ass}", str(out)]
    return audio.composite_key(cmd, {"segments": seg_files, "threads": 6}, ass, out, concat)


def test_bad_caption_style_or_font_change_invalidates_composite_key(tmp_path, monkeypatch):
    seg = tmp_path / "s.mp4"
    seg.write_bytes(b"segment")
    ass = tmp_path / "captions.ass"
    ass.write_text("Style: Default,Inter,84")
    k0 = _composite_key(tmp_path, [seg], ass)
    ass.write_text("Style: Default,Inter,100")               # caption size (the kit's caps_em) changed
    k1 = _composite_key(tmp_path, [seg], ass)
    font = tmp_path / "Inter.otf"
    font.write_bytes(b"font-1")
    monkeypatch.setattr(manifest, "font_files", lambda: [font])
    k2 = _composite_key(tmp_path, [seg], ass)
    font.write_bytes(b"font-22")
    k3 = _composite_key(tmp_path, [seg], ass)
    assert len({k0, k1, k2, k3}) == 4


def test_bad_transition_follows_both_neighbouring_segments(project, monkeypatch, tmp_path):
    """A whip at boundary 1|2 is drawn from the tail of segment 1 and the head of segment 2: changing either
    changes that clip and the composite key; the clip of boundary 0|1 does not depend on segment 2."""
    def small(d):
        d["settings"].update(width=216, height=384)
    p, tl = project(small)
    prof = profiles.load()
    data = prof.data.model_copy(update={"transitions": profiles.TransitionsProfile(kind="zoom_whip")})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("default", data, "x"))
    segs = [engine_clip(tmp_path / f"s{i}.mp4", 60, (216, 384), src=s)
            for i, s in enumerate(["testsrc2", "smptebars", "mandelbrot"])]
    ass = tmp_path / "captions.ass"
    ass.write_text("x")

    def run(files, wd):
        wd.mkdir()
        clips = transitions.render(tl, files, wd)
        return [manifest.sha256(c, fresh=True) for c, _ in clips], _composite_key(wd, files, ass, [c for c, _ in clips])

    c0, k0 = run(segs, tmp_path / "a")
    alt = engine_clip(tmp_path / "s2b.mp4", 60, (216, 384), src="rgbtestsrc")
    c1, k1 = run([segs[0], segs[1], alt], tmp_path / "b")
    alt0 = engine_clip(tmp_path / "s1b.mp4", 60, (216, 384), src="rgbtestsrc")
    c2, k2 = run([segs[0], alt0, segs[2]], tmp_path / "c")
    assert len(c0) == 2
    assert c1[0] == c0[0] and c1[1] != c0[1] and k1 != k0       # next segment changed: boundary 1|2 redrawn
    assert c2[0] != c0[0] and c2[1] != c0[1] and k2 != k0       # middle segment is a neighbour of both boundaries


def test_good_transition_clip_bytes_are_reproducible(tmp_path):
    import numpy as np
    frames = [np.full((64, 36, 3), i * 40, np.uint8) for i in range(5)]
    a = transitions.write_clip(frames, tmp_path / "a.mkv", 30)
    b = transitions.write_clip(frames, tmp_path / "b.mkv", 30)
    assert manifest.sha256(a, fresh=True) == manifest.sha256(b, fresh=True)


# audio ---------------------------------------------------------------------------------------------------------------
def test_bad_audio_source_edit_invalidates_audio_key(project, monkeypatch, tmp_path):
    _, tl = project()
    out = tmp_path / "a.m4a"
    cmd = ["ffmpeg", "-i", "x.wav", str(out)]
    k0 = audio.audio_key(tl, cmd, out, True)
    monkeypatch.setattr(audio, "__file__", str(edited_copies(tmp_path, [audio.__file__])[0]))
    assert audio.audio_key(tl, cmd, out, True) != k0


# unrelated changes ---------------------------------------------------------------------------------------------------
def test_good_music_change_keeps_every_visual_key(project, tmp_path):
    p, tl = project()
    nk, hk = native_keys(p, tl), hf_key(p, tl)
    gk = grade._key(tl.segments[0], [], [], p / "assets" / "still.jpg")

    def music(d):
        d["audio_chain"].update(bgm_gain_db=-30.0, music_brief={"mood": "calm", "energy": "low", "bpm_min": 60,
                                                                "bpm_max": 80, "duration_s": 6})
    p2, tl2 = project(lambda d: (d.setdefault("audio_chain", {}), music(d)))
    assert native_keys(p2, tl2) == nk and hf_key(p2, tl2) == hk
    assert grade._key(tl2.segments[0], [], [], p2 / "assets" / "still.jpg") == gk


def test_good_visual_code_sets_exclude_audio_and_gates():
    names = {Path(f).name for f in (*native.CODE, *hyperframes.CODE)}
    assert "audio.py" not in names and "mixer.py" not in names and not any("gates" in str(f) for f in names)
