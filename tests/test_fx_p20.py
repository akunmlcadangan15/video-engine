"""P20: page curl and map morph are never a default transition. The engine default profile keeps its old permissions
(page_turn / map_morph allowed only when a timeline asks), but no default kind or type rule picks page_turn; Geoverum
forbids both outright."""
from video_engine import profiles


def _kinds(t) -> set[str]:
    return {t.kind, *t.by_type.values(), *t.same_type.values()}


def test_good_page_turn_never_a_default_kind():
    for name in ("default", "geoverum"):
        assert "page_turn" not in _kinds(profiles.load(name).transitions), name


def test_good_geoverum_forbids_page_turn_and_map_morph():
    t = profiles.load("geoverum").transitions
    assert t.page_turn is False
    assert t.map_morph is False
