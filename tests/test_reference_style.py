"""Reference-style options (docs/REFERENCE-STYLE.md): profile choices for the
nine-video frame-by-frame bedah (videos 02-10, 2026-11-05).

These tests pin the integrated package: every new concept group exists as a
profile option, overlap stays allowed (never forbidden), and the 12 mandatory
gates are untouched.
"""

from pathlib import Path

import pytest

from video_engine.gate_registry import KERNEL_GATE_IDS, LINT_GATE_IDS, MANDATORY_GATE_IDS
from video_engine import profiles as _profiles

ROOT = Path(__file__).resolve().parent.parent
# Kernel integrity set. Licence/pixel gates R07, R14, A02 and R56px are lint unless a channel requires them.
KERNEL = {"T02", "M01", "W01", "L22", "R38", "A01", "G01", "M03"}
DEMOTED = {"R07", "R14", "A02", "R56px"}


@pytest.fixture(scope="module")
def profile():
    return _profiles.load("geoverum")


def test_mandatory_gate_set_unchanged(profile):
    assert set(MANDATORY_GATE_IDS) == set(KERNEL_GATE_IDS) == KERNEL
    assert DEMOTED <= set(LINT_GATE_IDS) and not (DEMOTED & set(KERNEL))
    assert not profile.gates.require


def test_overlap_is_allowed_never_forbidden(profile):
    overlap = profile.reference_style["overlap"]
    assert overlap["allowed"] is True
    assert overlap["rule"] == "readability"
    assert overlap["forbid"] == []


@pytest.mark.parametrize(
    "section,keys",
    [
        ("fill", ["hatch", "double_layer", "policy_fill", "spotlight_dim",
                  "coast_glow", "heatmap"]),
        ("label", ["types", "banner_bg", "typing_chars_per_s",
                   "handwritten_arrow", "stamp", "title_cinematic", "title_morph"]),
        ("data_viz", ["count_up", "thermometer", "stat_box", "flame_numbers"]),
        ("overlay", ["speech_bubble", "characters", "ships", "city_icons",
                     "weather_icons", "snow_particles", "medal"]),
        ("scene_variants", ["document", "ink_illustration", "parchment_map",
                            "live_footage", "framed_photo", "cross_section",
                            "schematic_hud", "telescope"]),
    ],
)
def test_reference_option_groups_present(profile, section, keys):
    for key in keys:
        assert key in profile.reference_style[section], f"{section}.{key} missing"


def test_reference_transitions_and_camera_options(profile):
    ref = profile.reference_style
    for kind in ["zoom_blur", "warp_blur", "zoom_whip", "flash", "wipe_circle"]:
        assert kind in ref["transitions"]["extra_kinds"]
    assert ref["transitions"]["continuous_shot"] is True
    assert ref["transitions"]["tilt_latitude"] is True


def test_style_gates_still_non_blocking(profile):
    assert list(profile.gates.require or []) == []
