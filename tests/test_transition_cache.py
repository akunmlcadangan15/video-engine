"""A composite cache hit must not render transition clips: the composite lookup uses transitions.plan_key before the
factory runs."""
from pathlib import Path

import pytest

from conftest import engine_clip
from video_engine import audio, profiles, transitions


def test_good_composite_cache_hit_skips_transition_rendering(project, tmp_path, monkeypatch):
    def small(d):
        d["profile"] = "geoverum"
        d["settings"].update(width=216, height=384)
        d["segments"][1]["transition_in"] = "crossfade"     # geoverum cuts by default; ask for a drawn transition
    _, tl = project(small)
    files = [engine_clip(tmp_path / f"s{i}.mp4", 60, (216, 384), src=s)
             for i, s in enumerate(["testsrc2", "smptebars", "rgbtestsrc"])]
    assert transitions.windows(tl)

    calls = []
    real = transitions.render

    def run(render):
        wd = tmp_path / f"wd{len(list(tmp_path.glob('wd*')))}"
        wd.mkdir()
        return audio.composite(tl, files, None, wd / "composite.mp4", threads=2,
                               transition_key=transitions.plan_key(tl, files), transition_factory=render,
                               use_cache=True)

    def first():
        calls.append(1)
        return real(tl, files, tmp_path / "clips")

    (tmp_path / "clips").mkdir()
    out, hit, key = run(first)
    assert not hit and out.is_file() and len(calls) == 1

    def must_not_run():
        pytest.fail("transition clips were rendered on a composite cache hit")
    out2, hit2, key2 = run(must_not_run)
    assert hit2 and key2 == key and out2.read_bytes() == out.read_bytes()


def test_bad_transition_profile_change_invalidates_the_plan_key(project, tmp_path, monkeypatch):
    _, tl = project(lambda d: d.update(profile="geoverum", settings={**d["settings"], "width": 216, "height": 384}))
    files = [engine_clip(tmp_path / f"p{i}.mp4", 60, (216, 384), src=s)
             for i, s in enumerate(["testsrc2", "smptebars", "rgbtestsrc"])]
    base = profiles.load("geoverum").data
    def with_scale(scale):
        tr = base.transitions.model_copy(update={"kind": "zoom_whip", "whip_scale": scale})
        data = base.model_copy(update={"transitions": tr})
        monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile("x", data, f"x{scale}"))
        return transitions.plan_key(tl, files)
    assert with_scale(1.2) != with_scale(1.4)
