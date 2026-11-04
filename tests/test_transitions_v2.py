"""Stage 2 transitions: hard cut on a visual-type change, a short state dissolve on one base map, explicit overrides."""
import pytest
from pydantic import ValidationError
from test_style_gates import layout

from video_engine import profiles, transitions
from video_engine.util import EngineError

MAP = "assets/map.jpg"


def _map_pair(d, same=True):
    layout(("map", 10, None), ("map", 10, None), profile="geoverum")(d)
    if not same:
        d["segments"][1]["gsap_scene_config"]["map_asset"] = "assets/map2.jpg"


def test_good_geoverum_cuts_type_changes_and_states_the_same_map(project):
    _, tl = project(layout(("map", 10, None), ("photo", 10, None), ("map", 10, None), ("map", 10, None)))
    T = profiles.of(tl).transitions
    assert T.kind == "cut" and T.same_type == {"map": "state"} and T.state_s == 0.2
    assert [transitions.kind_at(tl, i) for i in (1, 2, 3)] == ["cut", "cut", "state"]
    ws = transitions.windows(tl)
    assert [(w.kind, w.boundary, w.f0, w.f1) for w in ws] == [("state", 900, 897, 903)]


def test_good_map_to_map_with_different_assets_is_a_hard_cut(project):
    _, tl = project(_map_pair)
    assert transitions.kind_at(tl, 1) == "state" and transitions.windows(tl)[0].kind == "state"
    _, tl2 = project(lambda d: _map_pair(d, same=False))
    assert transitions.kind_at(tl2, 1) == "cut" and transitions.windows(tl2) == []


def test_good_explicit_transition_in_overrides_the_profile(project):
    def cross(d):
        layout(("photo", 10, None), ("photo", 10, None), profile="geoverum")(d)
        d["segments"][1]["transition_in"] = "crossfade"
    _, tl = project(cross)
    assert transitions.kind_at(tl, 1) == "crossfade"
    assert [(w.kind, w.f0, w.f1) for w in transitions.windows(tl)] == [("crossfade", 294, 306)]

    def cut(d):
        _map_pair(d)
        d["segments"][1]["transition_in"] = "cut"
    _, tl2 = project(cut)
    assert transitions.kind_at(tl2, 1) == "cut" and transitions.windows(tl2) == []


def test_bad_explicit_state_needs_the_same_base_map(project):
    def state(d):
        _map_pair(d, same=False)
        d["segments"][1]["transition_in"] = "state"
    _, tl = project(state)
    with pytest.raises(EngineError, match="state"):
        transitions.kind_at(tl, 1)


def test_bad_page_turn_and_state_lengths_are_rejected(project):
    with pytest.raises(ValidationError):
        profiles.TransitionsProfile(page_turn=False, kind="page_turn")
    for v in (0.05, 0.5):
        with pytest.raises(ValidationError):
            profiles.TransitionsProfile(state_s=v)
    def page(d):
        layout(("map", 10, None), ("photo", 10, None), profile="geoverum")(d)
        d["segments"][1]["transition_in"] = "page_turn"
    with pytest.raises(EngineError, match="page_turn"):
        project(page)
