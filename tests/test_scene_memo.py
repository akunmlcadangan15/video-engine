"""scene_data() is memoised per segment inside a process (one preflight/build reads it several times); the memo
does not hand callers a shared mutable scene and a config change invalidates it."""
from video_engine import hyperframes, layout


def test_good_scene_data_is_computed_once_and_returns_an_independent_copy(project, monkeypatch):
    p, tl = project()
    hyperframes.clear_scene_cache()
    calls = []
    real = layout.apply_segment
    monkeypatch.setattr(layout, "apply_segment", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    seg = tl.segments[2]
    first = hyperframes.scene_data(seg, tl, p)
    second = hyperframes.scene_data(seg, tl, p)
    assert first == second and len(calls) == 1
    first["pins"].append({"label": "mutated"})
    assert len(hyperframes.scene_data(seg, tl, p)["pins"]) == len(second["pins"])


def test_bad_scene_config_change_invalidates_the_memo(project, monkeypatch):
    p, tl = project()
    hyperframes.clear_scene_cache()
    calls = []
    real = layout.apply_segment
    monkeypatch.setattr(layout, "apply_segment", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    a = hyperframes.scene_data(tl.segments[2], tl, p)
    tl.segments[2].gsap_scene_config.camera_intensity = 2.0
    b = hyperframes.scene_data(tl.segments[2], tl, p)
    assert len(calls) == 2 and a["motion_px"] != b["motion_px"]


def test_good_bounded_memo_evicts_the_oldest_entry_and_refreshes_a_used_one():
    memo = {}
    for i in range(5):
        hyperframes._bounded_put(memo, str(i), i, 3)
    assert list(memo) == ["2", "3", "4"]
    hyperframes._bounded_put(memo, "4", "new", 3)
    assert list(memo) == ["2", "3", "4"] and memo["4"] == "new"


def test_good_ink_choices_are_memoised_with_a_bound(monkeypatch, tmp_path):
    """The _INKS memo (segment key -> adaptive ink choices) is limited: a long process does not keep every scene."""
    hyperframes.clear_scene_cache()
    monkeypatch.setattr(hyperframes, "INK_CACHE_MAX", 2)
    keys = iter(f"k{i}" for i in range(100))
    monkeypatch.setattr(hyperframes, "keys", lambda *a: (next(keys), ""))
    monkeypatch.setattr(hyperframes.legibility, "all_times", lambda *a: [])
    monkeypatch.setattr(hyperframes.legibility, "collect", lambda *a: {})
    monkeypatch.setattr(hyperframes.legibility, "elements", lambda *a: [])
    monkeypatch.setattr(hyperframes.legibility, "scene_inks", lambda *a: {})
    monkeypatch.setattr(hyperframes, "probe_dom", lambda *a, **k: {"times": []})
    from types import SimpleNamespace
    tl = SimpleNamespace(settings=SimpleNamespace(fps=30, width=216, height=384))
    scene = {"legibility": {"glow_colour": None}, "fps": 30, "n_frames": 30}
    for _ in range(5):
        assert hyperframes._inks(None, tl, tmp_path, tmp_path / "seg", scene) == {}
    assert len(hyperframes._INKS) == 2
