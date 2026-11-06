"""HF segments render in a bounded pool of budget.hf_parallel(native segment count): two at once when the shared
budget holds them next to the natives (min(natives, native_workers) * X264_THREADS + 2 * hf_workers * 2 <= workers),
else one; results keep segment order, and real renders give the same bytes as one at a time."""
import threading
import time
from pathlib import Path

from video_engine import build, grade, hyperframes, resources, streams


def _clean(monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE_HF_WORKERS", raising=False)


def test_good_8_worker_budget_renders_two_hf_segments_when_no_native_competes(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=15.6)
    assert (b.hf_segments, b.hf_workers, b.native_workers) == (2, 2, 2)
    assert b.hf_parallel(0) == 2                        # an all-HyperFrames timeline: 2 x 2 Chrome workers


def test_bad_8_worker_budget_with_natives_keeps_one_hf_segment(monkeypatch):
    _clean(monkeypatch)
    b = resources.plan(8, cpus=8, gb=15.6)
    assert b.hf_parallel(1) == 1 and b.hf_parallel(5) == 1   # 2 or 4 x264 threads + 8 > 8 workers


def test_good_budget_invariant_holds_for_every_size_and_native_count(monkeypatch):
    _clean(monkeypatch)
    for w in range(4, 65):
        b = resources.plan(w, cpus=64, gb=200)
        for n in range(0, 20):
            k = b.hf_parallel(n)
            assert k in (1, 2)
            if k == 2:
                assert min(n, b.native_workers) * b.x264_threads + k * b.hf_workers * 2 <= b.workers, (w, n)


def test_bad_small_budget_keeps_one_hf_segment(monkeypatch):
    _clean(monkeypatch)
    assert [resources.plan(w, cpus=8, gb=16).hf_parallel(0) for w in (1, 2, 3)] == [1, 1, 1]   # 2 x 1 x 2 > w


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
    for segs in (1, 2):
        b = resources.Budget(8, "env", 8, 16.0, 2, 2, 2, 6, segs)
        res = build.render_segments(tl, p, tmp_path / f"w{segs}", use_cache=False, budget=b)
        assert len({r.path for r in res}) == 2
        out[segs] = [Path(r.path).read_bytes() for r in res]
    assert out[1] == out[2]
    assert out[1][0] != out[1][1]                       # different segments: an order swap would be caught
