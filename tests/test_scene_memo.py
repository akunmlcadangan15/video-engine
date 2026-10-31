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
