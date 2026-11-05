"""units.py: reference canvas per orientation, the uniform scale k, the safe area and U01 (docs/SPEC.md, Units)."""
from __future__ import annotations

import pytest

from video_engine import units
from video_engine.units import Units

PORTRAIT_SIZES = [(1080, 1920), (720, 1280), (540, 960), (2160, 3840), (1080, 1350)]
LANDSCAPE_SIZES = [(1920, 1080), (2560, 1440), (3840, 2160)]


@pytest.mark.parametrize("W,H", PORTRAIT_SIZES)
def test_good_portrait_units_equal_the_legacy_expressions_bit_for_bit(W, H):
    u = Units.of(W, H)
    assert u.orientation == "portrait" and (u.ref_w, u.ref_h) == (1080, 1920)
    assert u.kx == W / 1080 and u.ky == H / 1920 and u.k == W / 1080
    for v in (0.0, 1.0, 26, 32.0, 60, 100, 160, 1382, 1480, 23.5, 0.5 * 150):
        assert u.x(v) == v * W / 1080
        assert u.y(v) == v * H / 1920
    assert u.safe() == (60 * W / 1080, 100 * H / 1920, H - 100 * H / 1920)


@pytest.mark.parametrize("W,H", LANDSCAPE_SIZES)
def test_good_landscape_scale_is_uniform_at_1080p_1440p_and_4k(W, H):
    u = Units.of(W, H)
    assert u.landscape and (u.ref_w, u.ref_h) == (1920, 1080)
    assert u.kx == pytest.approx(u.ky) and u.k == pytest.approx(H / 1080)
    sx, st, sb = u.safe()
    assert sx == pytest.approx(96 * u.k) and st == pytest.approx(54 * u.k) and sb == pytest.approx(H - 54 * u.k)
    assert u.uniform() and u.aspect_error() < 1e-9
    assert u.scene()["ref"] == [1920, 1080] and u.scene()["k"] == u.k


def test_good_square_counts_as_portrait():
    assert units.orientation_of(1080, 1080) == "portrait"
    assert not Units.of(1080, 1080).uniform()


@pytest.mark.parametrize("W,H,bad", [(1080, 1920, False), (1920, 1080, False), (3840, 2160, False),
                                     (1080, 1350, True), (1920, 800, True), (1080, 1080, True)])
def test_u01_flags_only_frames_off_the_reference_aspect(project, W, H, bad):
    from video_engine.gates.layer1 import u01_frame_aspect

    def mut(d):
        d["settings"]["width"], d["settings"]["height"] = W, H
        d["segments"] = d["segments"][:1]
    _, tl = project(mut)
    out = u01_frame_aspect(tl)
    assert (len(out) == 1 and out[0].gate == "U01" and out[0].level == "warn") if bad else out == []


def test_good_scale_height_is_the_portrait_equivalent():
    assert units.scale_height(1080, 1920) == 1920
    assert units.scale_height(2160, 3840) == 3840
    assert [units.scale_height(*s) for s in LANDSCAPE_SIZES] == [1920, 2560, 3840]


def test_good_formats_match_their_orientation():
    for name, (w, h) in units.FORMATS.items():
        assert units.orientation_of(w, h) == ("landscape" if name.startswith("landscape") else "portrait")
        assert Units.of(w, h).uniform()
