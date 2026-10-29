"""HyperFrames capture/encode profile: draft capture + low CRF intermediate, part of the segment cache key."""
from video_engine import config, hyperframes


def _fake_render(monkeypatch, seen):
    def fake_run(cmd, **kw):
        seen.append([str(c) for c in cmd])
        raise RuntimeError("stop after command capture")
    monkeypatch.setattr(hyperframes, "run", fake_run)


def test_good_render_uses_draft_capture_and_crf14(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    seen = []
    _fake_render(monkeypatch, seen)
    try:
        hyperframes.render(tl.segments[2], tl, p, tmp_path / "wd", workers=2, use_cache=False)
    except RuntimeError:
        pass
    cmd = seen[0]
    assert cmd[cmd.index("-q") + 1] == "draft" and cmd[cmd.index("--crf") + 1] == "14"
    assert cmd[cmd.index("--workers") + 1] == "2"


def test_bad_profile_change_without_key_change_is_impossible(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    seg = tl.segments[2]
    _, scene = hyperframes.build_dir(seg, tl, p, tmp_path / "hf")
    k1 = hyperframes.cache_key(seg, tl, p, scene)
    monkeypatch.setattr(hyperframes, "HF_CRF", 18)
    k2 = hyperframes.cache_key(seg, tl, p, scene)
    monkeypatch.setattr(hyperframes, "HF_QUALITY", "standard")
    k3 = hyperframes.cache_key(seg, tl, p, scene)
    assert len({k1, k2, k3}) == 3
