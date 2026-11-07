"""Layer 3 R38 snapshot gate and post-render pixel QA: GOOD and BAD cases on synthetic frames."""
import subprocess

import numpy as np

import hypoverum_core_rs as core
from video_engine import qa
from video_engine.gates import layer3

W, H = 270, 480


def stats(arr):
    return core.frame_stats([arr.astype(np.uint8).tobytes()], W, H)[0]


def noise(seed=0):
    return np.random.default_rng(seed).integers(0, 256, (H, W, 3))


def test_R38_good_detailed_frame():
    assert not layer3.r38_check(stats(noise()), 0, "t")


def test_R38_bad_black_frame():
    fs = layer3.r38_check(stats(np.zeros((H, W, 3))), 0, "t")
    assert "black frame" in fs[0].message


def test_R38_bad_empty_top_third():
    a = noise()
    a[: H // 3] = 128
    fs = layer3.r38_check(stats(a), 0, "t")
    assert len(fs) == 1 and "top third empty" in fs[0].message
    assert fs[0].gate == "R38space" and fs[0].level == "warn"


def test_R38_good_dark_but_detailed():
    a = noise() // 4 + 10       # dark night scene with texture
    assert not layer3.r38_check(stats(a), 0, "t")


def test_R38_snapshots_on_project(project):
    p, tl = project()
    fs, _ = layer3.r38_snapshots(tl, p, {})
    assert not [f for f in fs if f.level == "fail"]


def _video(path, src):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "60", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_QA_good_moving_video(project, tmp_path):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    tl.segments[0].end_frame = 60
    v = tmp_path / "moving.mp4"
    _video(v, "testsrc2=size=1080x1920:rate=30")
    fs, info = qa.pixel_gates(v, tl)
    assert not [f for f in fs if f.level == "fail"], fs


def test_QA_bad_frozen_video(project, tmp_path):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    tl.segments[0].end_frame = 60
    v = tmp_path / "frozen.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30", "-vf",
                    "trim=end_frame=1,tpad=stop_mode=clone:stop=-1", "-frames:v", "60", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(v)], check=True)
    fs, info = qa.pixel_gates(v, tl)
    assert any(f.gate == "R56px" for f in fs)


def test_QA_bad_black_video(project, tmp_path):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    tl.segments[0].end_frame = 60
    v = tmp_path / "black.mp4"
    _video(v, "color=c=black:size=1080x1920:rate=30")
    fs, _ = qa.pixel_gates(v, tl)
    assert any("black frame" in f.message for f in fs)


# R38 judges every third of every sample independently so two empty thirds that alternate being the blankest are both caught.
def _alternating(k):
    """Top and middle thirds both empty (luma std < 6); which of them is the blankest alternates with k."""
    a = noise(k)
    flat, faint = slice(0, H // 3), slice(H // 3, 2 * H // 3)
    if k % 2:
        flat, faint = faint, flat
    a[flat] = 128
    a[faint] = (124 + np.arange(W) * 8 // W)[None, :, None]      # faint smooth gradient: std ~2, no edges
    return a


def test_R38_bad_two_empty_thirds_alternating_blankest_both_flagged_every_sample():
    for k in range(4):
        fs = layer3.r38_check(stats(_alternating(k)), 0, f"frame {k}")
        assert sorted(f.message.split(": ")[1].split(" third")[0] for f in fs) == ["middle", "top"], fs


def test_R38_bad_alternating_empty_thirds_fail_post_render_qa(project, monkeypatch):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    frames = [_alternating(k).astype(np.uint8).tobytes() for k in range(6)]
    monkeypatch.setattr(qa, "iter_sample_frames", lambda v, w, h, step=5, threads=4: iter(frames))
    fs, _ = qa.pixel_gates(None, tl)
    r38 = [f.message for f in fs if f.gate == "R38space" and f.level == "warn"]
    assert any("top third empty" in m for m in r38) and any("middle third empty" in m for m in r38)


def test_R38_good_alternating_faint_texture_with_detail_passes():
    for k in range(4):
        a = noise(k)
        a[: H // 3] = a[: H // 3] // 2 + 64     # lower contrast, still detailed
        assert not layer3.r38_check(stats(a), 0, "t")


def test_good_pixel_gates_process_samples_in_bounded_batches(project, tmp_path, monkeypatch):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    tl.segments[0].end_frame = 60
    v = tmp_path / "batched.mp4"
    _video(v, "testsrc2=size=1080x1920:rate=30")
    batches = []
    real_stats, real_motion = qa.core.frame_stats, qa.core.frame_motion

    def stats(frames, w, h):
        batches.append(len(frames))
        return real_stats(frames, w, h)

    def motion(frames, w, h):
        batches.append(len(frames))
        return real_motion(frames, w, h)
    monkeypatch.setattr(qa, "QA_BATCH_FRAMES", 5)
    monkeypatch.setattr(qa.core, "frame_stats", stats)
    monkeypatch.setattr(qa.core, "frame_motion", motion)
    fs, info = qa.pixel_gates(v, tl)
    assert not [f for f in fs if f.level == "fail"] and info["samples"] == 12
    assert max(batches) <= 6 and len([b for b in batches if b <= 5]) > 1


def test_bad_QA_no_samples_fails_instead_of_passing(project, monkeypatch):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    monkeypatch.setattr(qa, "iter_sample_frames", lambda v, w, h, step=5, threads=4: iter(()))
    fs, info = qa.pixel_gates(None, tl)
    assert info["samples"] == 0
    assert any(f.gate == "R38" and f.level == "fail" and "no sampled frames" in f.message for f in fs)


def test_good_QA_single_sample_is_not_reported_as_no_motion(project, monkeypatch):
    _, tl = project(lambda d: d.update(segments=d["segments"][:1]))
    frames = [noise(0).astype(np.uint8).tobytes()]
    monkeypatch.setattr(qa, "iter_sample_frames", lambda v, w, h, step=5, threads=4: iter(frames))
    fs, info = qa.pixel_gates(None, tl)
    assert not [f for f in fs if f.level == "fail"], fs
    assert any(f.gate == "R56px" and f.level == "info" and "too few" in f.message for f in fs)
    assert info["motion"] == {}


def test_R38_good_dark_source_not_false_positive():
    """A dark snapshot from a genuinely dark source (e.g. a night map) emits warn rather than a black-frame fail."""
    fs = layer3.r38_check(stats(np.full((H, W, 3), 8)), 0, "t", source_mean=18.0)
    assert len(fs) == 1 and fs[0].gate == "R38" and fs[0].level == "warn"
    assert "dark source" in fs[0].message


def test_R38_bad_black_frame_still_fails_with_bright_source():
    """A black snapshot from a bright source still fails R38."""
    fs = layer3.r38_check(stats(np.zeros((H, W, 3))), 0, "t", source_mean=220.0)
    assert fs[0].gate == "R38" and fs[0].level == "fail"
    assert "black frame" in fs[0].message and "source mean luma 220" in fs[0].message


def test_R38space_good_dark_source_is_info_not_warn():
    a = noise()
    a[: H // 3] = 128
    fs = layer3.r38_check(stats(a), 0, "t", source_mean=20.0)
    assert fs and all(f.level == "info" for f in fs)


def test_R38_snapshots_dark_map_source_is_lenient(project, tmp_path, monkeypatch):
    """A dark source asset emits a warning rather than an R38 black-frame failure in both Layer 3 and post-render QA."""
    from PIL import Image
    p, tl = project()
    seg = tl.segments[0]
    dark = tmp_path / "darkmap.jpg"
    Image.new("RGB", (1080, 1920), (12, 14, 16)).save(dark)
    seg.source_image = "assets/darkmap.jpg"
    seg.source_video = None
    (p / "assets" / "darkmap.jpg").write_bytes(dark.read_bytes())
    means = layer3._source_mean_lumas(tl, p)
    assert means[seg.seg_index] < 25
    fs, _ = layer3.r38_snapshots(tl, p, {})
    assert not [f for f in fs if f.gate == "R38" and f.level == "fail"], fs
    # one sample per 5 frames over the whole timeline (QA fails a segment without a sampled frame): dark inside the
    # dark-source segment, mid-grey in the others, which keep their ordinary sources
    dark_frames = [np.full((H, W, 3), 8 if seg.start_frame <= f < seg.end_frame else 128, dtype=np.uint8).tobytes()
                   for f in range(0, max(s.end_frame for s in tl.segments), 5)]
    monkeypatch.setattr(qa, "iter_sample_frames", lambda v, w, h, step=5, threads=4: iter(dark_frames))
    qa_fs, _ = qa.pixel_gates(None, tl, project_dir=p)
    assert not [f for f in qa_fs if f.gate == "R38" and f.level == "fail"], qa_fs
    assert any(f.gate == "R38" and f.level == "warn" and "dark source" in f.message for f in qa_fs)
