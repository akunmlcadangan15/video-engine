"""DOM probe cache (hyperframes.probe_dom, cache kind "dom"): a probe of an unchanged built segment is answered from the
cache without a browser and restores its screenshots byte-identical; any change of what Chrome would see (scene,
probe times, viewport, bg mode, toolchain identity) or a broken entry is a miss. `engine cache --prune` keeps the
entries of referenced segment keys only. layer2.dom_timing sums the probe phases and counts cache hits."""
from pathlib import Path


from video_engine import cache, cache_prune, config, hyperframes, toolchain
from video_engine.gates import layer2
from test_legibility import _plain_labels


def _built(project, tmp_path, title="Surabaya", profile=None):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["title"] = title
        if profile:
            d["profile"] = profile          # geoverum: legibility profile, so bg probes take ink screenshots
    p, tl = project(mut)
    d, _ = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    return p, tl, d


def _no_browser(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a browser was spawned")
    monkeypatch.setattr(hyperframes, "run", boom)


# GOOD: unchanged segment -> hit, no browser, identical rects and screenshot bytes ----------------------------------------
def test_good_unchanged_segment_hits_without_browser(project, tmp_path, monkeypatch):
    _plain_labels(monkeypatch)
    _, _, d = _built(project, tmp_path, profile="geoverum")
    first = hyperframes.probe_dom(d, [0.5, 1.5], 1080, 1920, tmp_path / "s1", bg="bg", ref="k")
    assert first["cached"] is False and first["timing"]["load"] > 0
    _no_browser(monkeypatch)
    second = hyperframes.probe_dom(d, [0.5, 1.5], 1080, 1920, tmp_path / "s2", bg="bg", ref="k")
    assert second["cached"] is True
    for a, b in zip(first["times"], second["times"]):
        assert a["rects"] == b["rects"] and a["lines"] == b["lines"] and a["clip"] == b["clip"]
        for f in hyperframes.SHOT_FIELDS:
            assert bool(a.get(f)) == bool(b.get(f))
            if a.get(f):
                assert Path(b[f]).parent == tmp_path / "s2" and Path(b[f]).name == Path(a[f]).name
                assert Path(b[f]).read_bytes() == Path(a[f]).read_bytes()
    assert any(i.get("png") for i in second["times"]) and any(i.get("bg") for i in second["times"])


# BAD: every input Chrome would see differently is a miss ------------------------------------------------------------------
def test_bad_changed_label_text_misses(project, tmp_path):
    _, _, d = _built(project, tmp_path / "a", "Surabaya")
    k0 = hyperframes.probe_key(d, [1.0], 1080, 1920, True, "")
    _, _, d2 = _built(project, tmp_path / "b", "Semarang")
    assert hyperframes.probe_key(d2, [1.0], 1080, 1920, True, "") != k0


def test_bad_changed_times_size_mode_or_snaps_miss(project, tmp_path):
    _, _, d = _built(project, tmp_path)
    k = lambda *a: hyperframes.probe_key(d, *a)
    base = k([1.0], 1080, 1920, True, "")
    assert base == k([1.00001], 1080, 1920, True, "")          # same 4-dp probe time: same probe
    for other in (k([1.001], 1080, 1920, True, ""), k([1.0, 1.5], 1080, 1920, True, ""), k([1.0], 1920, 1080, True, ""),
                  k([1.0], 1080, 1920, False, ""), k([1.0], 1080, 1920, True, "bg")):
        assert other != base


def test_bad_changed_hf_identity_misses(project, tmp_path, monkeypatch):
    _, _, d = _built(project, tmp_path)
    k0 = hyperframes.probe_key(d, [1.0], 1080, 1920, False, "")
    monkeypatch.setattr(toolchain, "hf_identity", lambda: "other-chrome")
    assert hyperframes.probe_key(d, [1.0], 1080, 1920, False, "") != k0


def test_bad_evicted_screenshot_is_a_miss_and_reprobes(project, tmp_path):
    _, _, d = _built(project, tmp_path)
    hyperframes.probe_dom(d, [1.0], 1080, 1920, tmp_path / "s1", ref="evict")
    shots = [f for f in (config.CACHE_DIR / "dom").iterdir() if f.name.startswith("evict.") and f.suffix == ".png"]
    assert shots
    shots[0].unlink()                      # LRU eviction of one member of the entry
    again = hyperframes.probe_dom(d, [1.0], 1080, 1920, tmp_path / "s2", ref="evict")
    assert again["cached"] is False and Path(again["times"][0]["png"]).is_file()


# prune: entries of referenced segment keys stay, adhoc and stale entries go -------------------------------------------------
def test_good_prune_keeps_referenced_dom_entries_bad_drops_others(tmp_path):
    root = tmp_path / "c"
    (root / "dom").mkdir(parents=True)
    names = ["live.p1.json", "live.p1.snap_0.png", "dead.p2.json", "adhoc.p3.json"]
    for n in names:
        (root / "dom" / n).write_text("x")
    keep = {cache_prune.DOM_REF + "live"}
    assert [n for n in names if cache_prune.is_referenced(root / "dom" / n, keep)] == ["live.p1.json", "live.p1.snap_0.png"]
    assert not cache_prune.is_referenced(root / "segments" / "live.p1.json", keep)   # the prefix rule is dom/ only
    assert "dom" in cache.KINDS


def test_good_prune_run_deletes_stale_dom_entries_and_keeps_live(tmp_path, monkeypatch):
    root = tmp_path / "c"
    (root / "dom").mkdir(parents=True)
    for n in ("live.p1.json", "live.p1.snap_0.png", "dead.p2.json", "adhoc.p3.json"):
        (root / "dom" / n).write_text("x")
    monkeypatch.setattr(cache_prune, "referenced", lambda projects, r=None: ({cache_prune.DOM_REF + "live"}, []))
    r = cache_prune.prune([], root)
    assert sorted(p.name for p in (root / "dom").iterdir()) == ["live.p1.json", "live.p1.snap_0.png"]
    assert sorted(Path(d).name for d in r["deleted"]) == ["adhoc.p3.json", "dead.p2.json"]


def test_good_referenced_names_the_hf_segment_key_for_dom_entries(project):
    p, tl = project()
    hf = next(s for s in tl.segments if s.engine_type == "HYPERFRAMES_GSAP")
    names, problems = cache_prune.referenced([p])
    assert not problems and cache_prune.DOM_REF + hyperframes.keys(hf, tl, p)[0] in names


# dom_timing ---------------------------------------------------------------------------------------------------------------
def test_good_dom_timing_sums_phases_and_counts_hits():
    dom = {0: {"cached": False, "build_s": 1.5, "timing": {"load": 1000, "rects": 500, "snaps": 250, "bg": 2000, "masks": 1500}},
           1: {"cached": True, "build_s": 0.5, "timing": {"load": 9999, "rects": 9999, "snaps": 9999, "bg": 9999, "masks": 9999}}}
    t = layer2.dom_timing(dom, 0.75)
    assert t == {"build": 2.0, "browser_load": 1.0, "r63_rects": 0.5, "snapshots": 0.25, "bg_screenshots": 2.0,
                 "glyph_masks": 1.5, "r64_numpy": 0.75, "probes": 2, "cache_hits": 1}


def test_bad_dom_timing_of_no_segments_is_zero():
    assert layer2.dom_timing({}, 0.0)["probes"] == 0
