"""HF segments render in a bounded pool of budget.hf_parallel(native segment count): two at once when the shared
budget holds them next to the natives (native_cpus(natives) + hf_segments * hf_workers * CHROME_WORKER_CPUS <=
workers, natives charged as 0 or the whole native pool), else one; results keep segment order, and real renders
give the same bytes as one at a time. The Chrome worker count of a render changes its bytes, so it is part of the
render key (hyperframes.render_key)."""
import threading
import time
from pathlib import Path

import pytest

from video_engine import build, grade, hyperframes, resources, streams


def _clean(monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE_HF_WORKERS", raising=False)


def test_good_8_worker_budget_renders_two_hf_segments_when_no_native_competes(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=15.6)
    assert (b.hf_segments, b.hf_workers, b.native_workers) == (2, 2, 2)
    assert b.hf_parallel(0) == 2                        # an all-HyperFrames timeline: 2 x 2 Chrome workers


def test_good_8_worker_budget_with_natives_still_fits_two_hf_segments(monkeypatch):
    """With CHROME_WORKER_CPUS=1, the native pool (4 CPUs at default x264) plus 2 x 2 Chrome workers still fits 8."""
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=15.6)
    assert b.hf_parallel(1) == 2 and b.hf_parallel(5) == 2


def test_good_budget_invariant_holds_for_every_size_and_native_count(monkeypatch):
    _clean(monkeypatch)
    c = resources.CHROME_WORKER_CPUS
    for w in range(4, 65):
        b = resources.plan(w, cpus=64, gb=200)
        for n in range(0, 20):
            k = b.hf_parallel(n)
            assert k in (1, 2)
            if k == 2:
                assert b.native_cpus(n) + k * b.hf_workers * c <= b.workers, (w, n)
                assert min(n, b.native_workers) * b.x264_threads <= b.native_cpus(n)


@pytest.mark.parametrize("x264", [1, 2, 4, 8, 16])
def test_good_budget_invariants_hold_for_every_x264_thread_count(monkeypatch, x264):
    """VIDEO_ENGINE_X264_THREADS sets resources.X264_THREADS: native_workers = workers // (2 * x264) keeps the natives'
    threads within half the budget, so every invariant holds from workers = max(4, 2 * x264) up."""
    _clean(monkeypatch)
    monkeypatch.setattr(resources, "X264_THREADS", x264)
    c = resources.CHROME_WORKER_CPUS
    for w in range(max(4, 2 * x264), 65):
        b = resources.plan(w, cpus=64, gb=200)
        assert b.x264_threads == x264 and b.native_workers * x264 + b.hf_workers * c <= w, w
        for n in range(0, 20):
            if (k := b.hf_parallel(n)) == 2:
                assert b.native_cpus(n) + k * b.hf_workers * c <= w, (w, n)
            for h in range(1, 7):
                k, per = b.hf_allocation(h, n)
                if per > b.hf_workers:
                    assert b.native_cpus(n) + k * per * c <= w, (w, n, h)
                    assert min(n, b.native_workers) * x264 + k * per * c <= w, (w, n, h)


def test_good_default_x264_threads_keep_the_8_worker_budget(monkeypatch):
    _clean(monkeypatch)
    assert resources.X264_THREADS == 2
    b = resources.plan(8, cpus=8, gb=15.6)
    assert (b.native_workers, b.x264_threads, b.hf_workers, b.mux_threads) == (2, 2, 2, 6)


def test_bad_small_budget_hf_parallel_follows_chrome_charge(monkeypatch):
    _clean(monkeypatch)
    # hf_workers=1; hf_segments=2 only when 2 * 1 * CHROME_WORKER_CPUS <= w (so w>=2 with charge 1).
    assert [resources.plan(w, cpus=8, gb=16).hf_parallel(0) for w in (1, 2, 3)] == [1, 2, 2]


def _run(project, monkeypatch, tmp_path, segs):
    """render_segments over 4 HF segments with a fake render that sleeps; returns (results, peak concurrency)."""
    def mut(d):
        hf = d["segments"][2]
        d["segments"] = [dict(hf, seg_index=i, start_frame=30 * i, end_frame=30 * (i + 1)) for i in range(4)]
    p, tl = project(mut)
    lock, live, peak = threading.Lock(), [0], [0]

    def fake(s, tl_, pd, wd, workers, use_cache):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.2 if s.seg_index == 0 else 0.05)        # segment 0 finishes last
        with lock:
            live[0] -= 1
        return Path(f"/x/seg{s.seg_index}.mp4"), False
    monkeypatch.setattr(hyperframes, "render", fake)
    monkeypatch.setattr(streams, "verify", lambda *a, **k: None)
    monkeypatch.setattr(grade, "ranges", lambda *a, **k: [])
    b = resources.Budget(8, "env", 8, 16.0, 2, 2, 2, 6, segs)      # all-HF timeline: hf_parallel(0) = segs
    return build.render_segments(tl, p, tmp_path, budget=b), peak[0]


def test_good_two_hf_segments_overlap_and_keep_segment_order(project, monkeypatch, tmp_path):
    res, peak = _run(project, monkeypatch, tmp_path, 2)
    assert peak == 2
    assert [r.seg for r in res] == [0, 1, 2, 3] and [r.path for r in res] == [f"/x/seg{i}.mp4" for i in range(4)]


def test_bad_one_hf_segment_budget_never_overlaps(project, monkeypatch, tmp_path):
    res, peak = _run(project, monkeypatch, tmp_path, 1)
    assert peak == 1 and [r.seg for r in res] == [0, 1, 2, 3]


def test_good_real_concurrent_renders_match_serial_bytes_in_order(project, tmp_path):
    """Two different real HF segments: 2 at once writes the same bytes, per segment and in segment order, as 1 at a
    time (distinct segment work paths, no shared-file race)."""
    def mut(d):
        hf = d["segments"][2]
        d["segments"] = [dict(hf, seg_index=i, start_frame=20 * i, end_frame=20 * (i + 1),
                              gsap_scene_config=dict(hf["gsap_scene_config"], title=t)) for i, t in enumerate(("Kaspia", "Aral"))]
    p, tl = project(mut)
    out = {}
    budgets = {segs: resources.Budget(8, "env", 8, 16.0, 2, 2, 2, 6, segs, hf_workers_pinned=True) for segs in (1, 2)}
    # the Chrome worker count is part of the render (render_key): both runs must use the same one
    assert len({resources.hf_allocation_for(tl, b)[1] for b in budgets.values()}) == 1
    for segs, b in budgets.items():
        res = build.render_segments(tl, p, tmp_path / f"w{segs}", use_cache=False, budget=b)
        assert len({r.path for r in res}) == 2
        out[segs] = [Path(r.path).read_bytes() for r in res]
    assert out[1] == out[2]
    assert out[1][0] != out[1][1]                       # different segments: an order swap would be caught


def test_good_hf_allocation_examples(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=15.6)
    # 8 CPU / ~15.6 GB, charge 1 CPU + 3 GB: one HF segment gets min(8, 5) = 5 Chrome workers.
    assert b.hf_allocation(1, 0) == (1, 5)
    assert b.hf_allocation(2, 0) == (2, 2)
    # Any natives: native pool takes 4 CPUs, 4 left → 4 Chrome workers; two HF segments still fit in parallel.
    assert b.hf_allocation(1, 1) == b.hf_allocation(1, 5) == (1, 4)
    assert b.hf_allocation(2, 1) == b.hf_allocation(3, 3) == (2, 2)
    assert resources.plan(16, cpus=16, gb=32).hf_allocation(1, 1) == (1, 8)
    assert resources.plan(8, cpus=8, gb=24).hf_allocation(1, 0) == (1, 8)
    # 16 CPU / 32 GB, no natives: min(16 by CPU, 32//3 by RAM) = 10.
    assert resources.plan(16, cpus=16, gb=32).hf_allocation(1, 0) == (1, 10)
    assert resources.plan(8, cpus=8, gb=16).hf_allocation(1, 0) == (1, 5)


def test_bad_hf_allocation_ram_caps_workers_per_segment(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=6)                # 6 GB // 3 = 2 Chrome workers, CPU alone would allow 4
    assert b.hf_allocation(1, 0) == (1, 2)
    b = resources.plan(16, cpus=16, gb=12)             # the base hf_workers (4) is never lowered by the RAM cap
    assert b.hf_allocation(2, 0) == (2, 4)


def test_good_hf_allocation_invariants_hold_for_every_size(monkeypatch):
    _clean(monkeypatch)
    for w in range(4, 65):
        for gb in (4, 8, 16, 32, 64):
            b = resources.plan(w, cpus=64, gb=gb)
            for n in range(0, 20):
                for h in range(1, 7):
                    k, per = b.hf_allocation(h, n)
                    assert 1 <= k <= min(h, b.hf_parallel(n)) and per >= b.hf_workers, (w, gb, n, h)
                    if per > b.hf_workers:
                        assert b.native_cpus(n) + k * per * resources.CHROME_WORKER_CPUS <= w
                        assert min(n, b.native_workers) * b.x264_threads <= b.native_cpus(n)
                        assert k * per * resources.CHROME_WORKER_GB <= gb


@pytest.mark.parametrize("x264", [1, 2, 4])
def test_good_hf_allocation_depends_on_natives_only_through_none_versus_some(monkeypatch, x264):
    """The Chrome workers per segment are part of every HF render key: one more native clip must not change them."""
    _clean(monkeypatch)
    monkeypatch.setattr(resources, "X264_THREADS", x264)
    for w in range(max(4, 2 * x264), 65):
        for gb in (4, 8, 16, 32, 64):
            b = resources.plan(w, cpus=64, gb=gb)
            for h in range(0, 7):
                assert len({b.hf_allocation(h, n) for n in range(1, 25)}) == 1, (w, gb, h)


def test_good_hf_allocation_env_pinned_budget_is_unchanged(monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE_HF_WORKERS", "2")
    b = resources.plan(16, cpus=16, gb=64)
    assert b.hf_workers_pinned
    for h in (0, 1, 3):
        for n in (0, 4):
            assert b.hf_allocation(h, n) == (b.hf_parallel(n), 2)


def test_bad_hf_allocation_without_hf_segments_keeps_base_workers(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(16, cpus=16, gb=64)
    assert not b.hf_workers_pinned
    assert b.hf_allocation(0, 3) == (1, b.hf_workers)


def test_good_render_segments_passes_allocated_workers(project, monkeypatch, tmp_path):
    seen = []

    def fake(s, tl_, pd, wd, workers, use_cache):
        seen.append(workers)
        return Path(f"/x/seg{s.seg_index}.mp4"), False
    monkeypatch.setattr(hyperframes, "render", fake)
    monkeypatch.setattr(streams, "verify", lambda *a, **k: None)
    monkeypatch.setattr(grade, "ranges", lambda *a, **k: [])
    p, tl = project(lambda d: None)
    nat = sum(s.engine_type == build.NATIVE for s in tl.segments)
    b = resources.plan(8, cpus=8, gb=15.6)
    build.render_segments(tl, p, tmp_path, budget=b)
    hf = len(tl.segments) - nat
    assert seen and set(seen) == {b.hf_allocation(hf, nat)[1]}


def _one_hf_segment(project, frames=20):
    def mut(d):
        hf = d["segments"][2]
        d["segments"] = [dict(hf, seg_index=0, start_frame=0, end_frame=frames)]
    return project(mut)


def test_good_real_render_same_worker_count_is_byte_identical(project, tmp_path):
    """Chrome capture workers split the frames into contiguous ranges and each worker's page reaches its first frame
    through its own seek history, so the bytes are a function of the worker count: the same count twice gives the
    same bytes (1 worker: the streaming path, 4: disk capture of four ranges)."""
    p, tl = _one_hf_segment(project)
    for w in (1, 4):
        out = []
        for run in range(2):
            path, hit = hyperframes.render(tl.segments[0], tl, p, tmp_path / f"c{w}_{run}", workers=w, use_cache=False)
            assert not hit
            out.append(Path(path).read_bytes())
        assert out[0] == out[1], w


def test_good_worker_count_is_part_of_the_render_identity_only(project, tmp_path, monkeypatch):
    """The --workers value keys the rendered file (render_key) and not the scene (keys()[0]: build dir, DOM probes,
    inks): a cached render at one worker count is no hit at another."""
    from video_engine import cache, config

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = _one_hf_segment(project)
    s = tl.segments[0]
    assert len({hyperframes.render_key(s, tl, p, w) for w in (1, 2, 4)}) == 3
    sk = hyperframes.keys(s, tl, p)[0]
    assert sk not in {hyperframes.render_key(s, tl, p, w) for w in (1, 2, 4)}
    clip = tmp_path / "w1.mp4"
    clip.write_bytes(b"cached at 1 worker")
    cache.put_file("segments", hyperframes.render_key(s, tl, p, 1), clip)
    assert hyperframes.render(s, tl, p, tmp_path / "a", workers=1)[1] is True

    class Rendered(Exception):
        pass

    def cli(cmd, **kw):
        raise Rendered()
    monkeypatch.setattr(hyperframes, "run", cli)
    with pytest.raises(Rendered):
        hyperframes.render(s, tl, p, tmp_path / "b", workers=4)
    assert hyperframes.keys(s, tl, p)[0] == sk


def test_good_build_and_prune_use_one_worker_count(project, monkeypatch, tmp_path):
    """render_segments renders with the Chrome workers of resources.hf_allocation_for and cache_prune.referenced keeps
    the render under the key of those same workers (same budget for both)."""
    from video_engine import cache_prune

    _clean(monkeypatch)
    seen = []

    def fake(s, tl_, pd, wd, workers, use_cache):
        seen.append(workers)
        return Path(f"/x/seg{s.seg_index}.mp4"), False
    monkeypatch.setattr(hyperframes, "render", fake)
    monkeypatch.setattr(streams, "verify", lambda *a, **k: None)
    monkeypatch.setattr(grade, "ranges", lambda *a, **k: [])
    p, tl = project()
    b = resources.plan(16, cpus=16, gb=32)
    monkeypatch.setattr(resources, "budget", lambda tl=None: b)
    build.render_segments(tl, p, tmp_path, budget=b)
    workers = resources.hf_allocation_for(tl, b)[1]
    assert seen and set(seen) == {workers}
    keep, _ = cache_prune.referenced([p], tmp_path / "c")
    hf = next(s for s in tl.segments if s.engine_type != build.NATIVE)
    assert hyperframes.render_key(hf, tl, p, workers) + ".mp4" in keep
    assert hyperframes.render_key(hf, tl, p, workers + 1) + ".mp4" not in keep
