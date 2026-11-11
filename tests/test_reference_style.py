"""Reference-style options (docs/REFERENCE-STYLE.md): verifies that all seven
concept groups exist as profile options on Geoverum, visual overlap remains allowed
under the readability rule, and core integrity gate sets remain intact.
"""

from pathlib import Path

import pytest

from video_engine.gate_registry import KERNEL_GATE_IDS, NEVER_ADVISORY
from video_engine import profiles as _profiles

ROOT = Path(__file__).resolve().parent.parent
KERNEL = {"T02", "M01", "W01", "L22", "R38", "A01", "G01", "M03"}
HISTORICAL_12 = {
    "T02", "R07", "R14", "A01", "A02", "W01",
    "R38", "R56px", "M01", "M03", "G01", "L22",
}


@pytest.fixture(scope="module")
def profile():
    return _profiles.load("geoverum")


def test_mandatory_gate_set_unchanged(profile):
    assert set(KERNEL_GATE_IDS) == KERNEL
    assert set(NEVER_ADVISORY) == HISTORICAL_12


def test_overlap_is_allowed_never_forbidden(profile):
    overlap = profile.reference_style.overlap
    assert overlap.allowed is True and profile.reference_style.overlap_allowed
    assert overlap.rule == "readability"
    assert overlap.forbid == []


@pytest.mark.parametrize(
    "section,keys",
    [
        ("fill", ["hatch", "double_layer", "policy_fill", "spotlight_dim",
                  "coast_glow", "heatmap", "territory_sweep", "dmz_fire_border"]),
        ("label", ["types", "banner_bg", "typing_chars_per_s",
                   "handwritten_arrow", "stamp", "title_cinematic", "title_morph", "equation_slash"]),
        ("data_viz", ["count_up", "thermometer", "stat_box", "flame_numbers",
                      "timeline_ruler", "measure_bar"]),
        ("overlay", ["speech_bubble", "characters", "ships", "city_icons",
                     "weather_icons", "snow_particles", "medal", "flag_badges_moving"]),
        ("scene_variants", ["document", "ink_illustration", "parchment_map",
                            "live_footage", "framed_photo", "cross_section",
                            "schematic_hud", "telescope"]),
    ],
)
def test_reference_option_groups_present(profile, section, keys):
    for key in keys:
        assert key in getattr(profile.reference_style, section), f"{section}.{key} missing"


def test_reference_transitions_and_camera_options(profile):
    ref = profile.reference_style
    for kind in ["zoom_blur", "warp_blur", "zoom_whip", "flash", "wipe_circle"]:
        assert kind in ref.transitions["extra_kinds"]
    assert ref.transitions["continuous_shot"] is True
    assert ref.transitions["tilt_latitude"] is True


def test_style_gates_still_non_blocking(profile):
    assert list(profile.gates.require or []) == []
