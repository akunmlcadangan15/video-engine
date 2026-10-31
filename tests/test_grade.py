"""Colour grade (video_engine/grade.py): grade values baked into a 3D LUT, .cube LUTs, per segment / beat / profile
frame ranges, the graded segment file (pixels, frame ranges, cache, joinable) and the G01 LUT gate."""
import subprocess

import numpy as np
import pytest
from pydantic import ValidationError

from conftest import engine_clip
from video_engine import grade, profiles, streams, timeline
from video_engine.gates import layer2
from video_engine.util import EngineError

G = grade.Grade
X = np.array([[0.0, 0.0, 0.0], [0.25, 0.25, 0.25], [1.0, 1.0, 1.0], [0.8, 0.4, 0.1]])


def _cube(path, size=2, fn=lambda r, g, b: (1 - r, 1 - g, 1 - b), head=""):
    v = np.linspace(0, 1, size)
    rows = [fn(r, g, b) for b in v for g in v for r in v]
    path.write_text(head + f"LUT_3D_SIZE {size}\n" + "".join(f"{a} {c} {d}\n" for a, c, d in rows))
    return path


def _frames(path, w=160, h=288):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          "scale=in_color_matrix=bt709:in_range=tv,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(float)


# values ---------------------------------------------------------------------------------------------------------------
def test_good_default_grade_is_identity_and_bakes_to_the_identity_cube(tmp_path):
    assert G().identity and G(gain=0.5, strength=0).identity and not G(saturation=0.9).identity
    assert np.allclose(grade.apply_values(G(), X, tmp_path), X)
    rows = np.loadtxt(grade.cube_text(G(), tmp_path).splitlines()[1:])
    assert rows.shape == (grade.N ** 3, 3) and np.allclose(rows[1], [1 / (grade.N - 1), 0, 0])   # red fastest


def test_good_lift_gamma_gain_saturation_tint_and_strength(tmp_path):
    av = lambda g, x: grade.apply_values(g, np.array(x, float), tmp_path)
    assert np.allclose(av(G(gain=0.5), [0.8, 0.4, 0.2]), [0.4, 0.2, 0.1])
    assert np.allclose(av(G(lift=0.2), [[0, 0, 0], [1, 1, 1]]), [[0.2] * 3, [1] * 3])
    assert np.allclose(av(G(gamma=2.0), [0.25, 0.25, 0.25]), [0.5] * 3)
    assert np.allclose(av(G(gain=[1.0, 1.0, 0.5]), [0.6, 0.6, 0.6]), [0.6, 0.6, 0.3])
    grey = av(G(saturation=0), [0.8, 0.4, 0.1])
    assert np.allclose(grey, grey[0]) and grey[0] == pytest.approx(np.array([0.8, 0.4, 0.1]) @ grade.LUMA)
    red = av(G(tint=grade.Tint(color="#c03020", amount=1)), [0.2, 0.2, 0.2])
    assert red @ grade.LUMA == pytest.approx(0.2, abs=1e-6) and red[0] > red[1] > red[2]
    assert np.allclose(av(G(gain=0.5, strength=0.5), [0.8, 0.8, 0.8]), [0.6] * 3)


def test_good_cube_lut_is_read_with_its_domain_and_sampled_trilinearly(tmp_path):
    inv = _cube(tmp_path / "inv.cube", head="TITLE \"x\"\n# comment\n")
    assert np.allclose(grade.apply_values(G(lut="inv.cube"), X, tmp_path), 1 - X)
    _cube(tmp_path / "d.cube", fn=lambda r, g, b: (2 * r, 2 * g, 2 * b), head="DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n")
    assert np.allclose(grade.apply_values(G(lut="d.cube"), X, tmp_path), X)
    assert grade.read_cube(inv).shape == (2, 2, 2, 3)


@pytest.mark.parametrize("kw", [{"lift": 0.6}, {"gamma": 0.1}, {"gain": [1, 1, 5]}, {"saturation": 4}, {"strength": 2},
                                {"lut": "look.png"}, {"tint": {"color": "red", "amount": 1}},
                                {"tint": {"color": "#000000", "amount": 1}}, {"contrast": 1.2}])
def test_bad_grade_values_rejected(kw):
    with pytest.raises(ValidationError):
        G(**kw)


@pytest.mark.parametrize("text, msg", [("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n", "1D"), ("0 0 0\n", "LUT_3D_SIZE"),
                                       ("LUT_3D_SIZE 2\n0 0 0\n", "needs 8"),
                                       ("LUT_3D_SIZE two\n", "invalid .cube"),
                                       ("LUT_3D_SIZE 2\nDOMAIN_MIN a b c\n", "invalid .cube"),
                                       ("LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 1 1 1\n" + "0 0 0\n" * 8, "DOMAIN")])
def test_bad_cube_files_raise(tmp_path, text, msg):
    (tmp_path / "x.cube").write_text(text)
    with pytest.raises(EngineError, match=msg):
        grade.read_cube(tmp_path / "x.cube")


# frame ranges -----------------------------------------------------------------------------------------------------------
RED = {"tint": {"color": "#c03020", "amount": 0.8}, "saturation": 1.2}


def _beats(d):
    from test_layout import beats_doc
    beats_doc(d)
    d["segments"][2]["gsap_scene_config"]["beats"][1]["grade"] = RED


def test_good_no_grade_means_no_ranges_and_the_file_is_untouched(project, tmp_path):
    p, tl = project()
    src = engine_clip(tmp_path / "s.mp4", 60, size=(160, 288))
    assert all(grade.ranges(s, tl, p) == [] for s in tl.segments)
    assert grade.apply(tl.segments[0], tl, p, src, tmp_path / "wd") == (src, False)
    assert [f.level for f in layer2.g01_grade_luts(tl, p)] == ["pass"]


def test_good_beat_grade_over_segment_grade_over_profile_grade(project, monkeypatch):
    def mut(d):
        _beats(d)
        d["segments"][0]["grade"] = {"gain": 0.9}
        d["segments"][1]["grade"] = {}                      # explicit identity: no profile grade either
    p, tl = project(mut)
    r = grade.ranges(tl.segments[2], tl, p)
    assert [(a, e) for a, e, *_ in r] == [(110, 200)] and r[0][2] == G(**RED) and r[0][3] == p
    assert [(a, e, g.gain) for a, e, g, _ in grade.ranges(tl.segments[0], tl, p)] == [(0, 60, 0.9)]
    real = profiles.of
    pg = G(saturation=0.8)
    monkeypatch.setattr(profiles, "of", lambda t: profiles.Profile("x", real(t).data.model_copy(update={"grade": pg}), "x"))
    seg2 = [(a, e, g) for a, e, g, _ in grade.ranges(tl.segments[2], tl, p)]
    assert seg2 == [(0, 110, pg), (110, 200, G(**RED)), (200, 300, pg)]
    assert grade.ranges(tl.segments[1], tl, p) == [] and grade.ranges(tl.segments[0], tl, p)[0][2].gain == 0.9


def test_good_equal_adjacent_beat_grades_merge(project):
    def mut(d):
        _beats(d)
        for b in d["segments"][2]["gsap_scene_config"]["beats"][1:]:
            b["grade"] = RED
    p, tl = project(mut)
    assert [(a, e) for a, e, *_ in grade.ranges(tl.segments[2], tl, p)] == [(110, 300)]


def test_good_profile_grade_keeps_profiles_without_it_unhashed():
    assert profiles.load().grade is None and profiles.load("geoverum").grade is None
    with pytest.raises(EngineError, match="grade"):
        profiles._parse("x", (profiles.path_of("default").read_text(), "[grade]\nlift = 0.9\n"))


# graded file -----------------------------------------------------------------------------------------------------------
def test_good_graded_segment_pixels_frame_range_cache_and_join(project, tmp_path):
    def mut(d):
        d["segments"][0]["grade"] = {"gain": [1.0, 0.5, 0.5]}
    p, tl = project(mut)
    src = engine_clip(tmp_path / "s.mp4", 60, size=(160, 288), src="color=c=0xb4b4b4")
    out, hit = grade.apply(tl.segments[0], tl, p, src, tmp_path / "wd")
    assert not hit and out != src
    a, b = _frames(src), _frames(out)
    assert len(b) == 60 and np.allclose(b[..., 0], a[..., 0], atol=3)
    assert np.allclose(b[..., 1:], a[..., 1:] * 0.5, atol=3)
    # a lossless grade is High 4:4:4 Predictive, so the composite joins it with the concat filter (not stream copy)
    joined, diffs = streams.joinable([src, out])
    assert not joined and any("profile" in d for d in diffs)
    assert grade.apply(tl.segments[0], tl, p, src, tmp_path / "wd") == (out, True)


def test_good_beat_grade_touches_only_its_frames(project, tmp_path):
    p, tl = project(_beats)
    seg = tl.segments[2]
    src = engine_clip(tmp_path / "s.mp4", seg.n_frames, size=(160, 288), src="color=c=0x808080")
    a, b = _frames(src), _frames(grade.apply(seg, tl, p, src, tmp_path / "wd")[0])
    diff = np.abs(b - a).max(axis=(1, 2, 3))
    assert diff[:110].max() <= 2 and diff[200:].max() <= 2 and diff[110:200].min() > 20
    assert b[150, 0, 0, 0] > b[150, 0, 0, 2]                                  # the red tint


def test_good_grade_intermediate_is_lossless(project, tmp_path):
    """The graded intermediate is lossless: on a near-identity grade the decoded pixels stay within colour-conversion
    rounding of the source (a CRF 8 second generation drifts much further)."""
    def mut(d):
        d["segments"][0]["grade"] = {"gain": 1.0000001}
    p, tl = project(mut)
    src = engine_clip(tmp_path / "s.mp4", 30, size=(320, 576), src="testsrc2")
    a = _frames(src, w=320, h=576)
    out = grade.apply(tl.segments[0], tl, p, src, tmp_path / "wd")[0]
    b = _frames(out, w=320, h=576).astype(float)
    assert np.abs(b - a).mean() < 0.15


def test_good_near_identity_grade_keeps_the_picture(project, tmp_path):
    def mut(d):
        d["segments"][1]["grade"] = {"saturation": 1.0001}
    p, tl = project(mut)
    src = engine_clip(tmp_path / "s.mp4", 60, size=(160, 288))
    a, b = _frames(src), _frames(grade.apply(tl.segments[1], tl, p, src, tmp_path / "wd")[0])
    assert np.abs(b - a).mean() < 1.5                                        # bt709 matrix both ways


def test_bad_missing_or_broken_lut_fails_g01(project):
    def mut(d):
        d["segments"][0]["grade"] = {"lut": "assets/none.cube"}
        d["segments"][1]["grade"] = {"lut": "assets/bad.cube"}
    p, tl = project(mut)
    (p / "assets" / "bad.cube").write_text("LUT_3D_SIZE 2\n0 0 0\n")
    fs = layer2.g01_grade_luts(tl, p)
    assert [(f.level, f.seg) for f in fs] == [("fail", 0), ("fail", 1)]
    assert "none.cube" in fs[0].message and "needs 8" in fs[1].message


def test_bad_grade_fields_rejected_on_segments(project):
    with pytest.raises(ValidationError):
        project(lambda d: d["segments"][0].update(grade={"exposure": 1}))


def test_good_render_segments_grades_only_graded_segments(project, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from video_engine import build, hyperframes, native

    def mut(d):
        d["segments"][1]["grade"] = {"gain": 0.8}
    p, tl = project(mut)
    clips = {s.seg_index: engine_clip(tmp_path / f"c{s.seg_index}.mp4", s.n_frames, size=(160, 288)) for s in tl.segments}
    monkeypatch.setattr(native, "plan", lambda s, t, d: SimpleNamespace(seg=s))
    monkeypatch.setattr(native, "render", lambda job, *a, **k: (clips[job.seg.seg_index], False, "encode"))
    monkeypatch.setattr(hyperframes, "render", lambda s, *a, **k: (clips[s.seg_index], False))
    res = build.render_segments(tl, p, tmp_path / "wd")
    assert [r.mode for r in res] == ["encode", "encode+grade", "hyperframes"]
    assert [r.path == str(clips[r.seg]) for r in res] == [True, False, True]
    assert build.render_segments(tl, p, tmp_path / "wd")[1].mode == "encode+grade(hit)"


def test_good_prune_keeps_the_graded_file_of_a_cached_render(project, tmp_path):
    from video_engine import cache_prune, native
    p, tl = project(lambda d: d["segments"][0].update(grade={"gain": 0.8}))
    root = tmp_path / "c"
    keep, _ = cache_prune.referenced([p], root)
    rendered = root / "segments" / (native.plan(tl.segments[0], tl, p).key + ".mp4")
    rendered.parent.mkdir(parents=True)
    engine_clip(rendered, 60, size=(160, 288))
    keep2, problems = cache_prune.referenced([p], root)
    assert not problems and keep2 - keep == {grade.key(tl.segments[0], tl, p, rendered) + ".mp4"}
