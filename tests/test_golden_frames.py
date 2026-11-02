"""Golden frames: pixel regression tests for the paths that render without Chrome (native Ken Burns over a still,
native footage with a pan, the crossfade, state, zoom whip, zoom through and page turn transitions, a colour grade). Each case renders a few frames at a
small size and compares them with tests/golden/<name>.png.

Tolerance (`_close`): mean absolute difference <= 1.5 levels and at most 0.5 % of the samples off by more than 24,
so encoder/scaler rounding across ffmpeg builds passes and a moved, re-coloured or re-timed picture fails.
Refresh after an intended visual change: VIDEO_ENGINE_UPDATE_GOLDENS=1 pytest tests/test_golden_frames.py, then
look at the PNGs before committing them."""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import engine_clip
from video_engine import config, grade, native, profiles, transitions

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("VIDEO_ENGINE_UPDATE_GOLDENS") == "1"
W, H = 216, 384                     # 9:16 at a fifth of 1080 x 1920


def _small(d):
    d["settings"].update(width=W, height=H)


def _close(got: np.ndarray, want: np.ndarray) -> tuple[bool, str]:
    if got.shape != want.shape:
        return False, f"shape {got.shape} != {want.shape}"
    diff = np.abs(got.astype(np.int16) - want.astype(np.int16))
    mean, far = float(diff.mean()), float((diff > 24).mean())
    return mean <= 1.5 and far <= 0.005, f"mean |diff| {mean:.3f} (<= 1.5), share > 24: {far:.4%} (<= 0.5 %)"


def _check(name: str, frames: list[np.ndarray]):
    """Frames side by side against tests/golden/<name>.png (written when VIDEO_ENGINE_UPDATE_GOLDENS=1)."""
    got = np.concatenate([np.asarray(f, np.uint8).reshape(H, W, 3) for f in frames], axis=1)
    path = GOLDEN / f"{name}.png"
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        Image.fromarray(got).save(path, optimize=True)
        pytest.skip(f"golden written: {path}")
    assert path.is_file(), f"missing golden {path}; create it with VIDEO_ENGINE_UPDATE_GOLDENS=1"
    ok, why = _close(got, np.asarray(Image.open(path).convert("RGB")))
    if not ok:
        Image.fromarray(got).save(path.with_suffix(".actual.png"))
    assert ok, f"{name}: {why}; actual frames written next to the golden as {path.stem}.actual.png"


def _frames(video: Path, idx: list[int]) -> list[np.ndarray]:
    sel = "+".join(f"eq(n\\,{i})" for i in idx)
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(video), "-vf",
                          f"select='{sel}',scale=in_color_matrix=bt709:in_range=tv,format=rgb24", "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3))


def _native(project, tmp_path, mut, seg):
    p, tl = project(lambda d: (_small(d), mut(d)))
    job = native.plan(tl.segments[seg], tl, p)
    wd = tmp_path / "wd"
    wd.mkdir()
    out, _, _ = native.render(job, tl, wd, threads=2, use_cache=False)
    n = tl.segments[seg].n_frames
    return _frames(out, [0, n // 2, n - 1])


def test_golden_native_ken_burns_over_a_still(project, tmp_path):
    _check("native_ken_burns_still", _native(project, tmp_path, lambda d: None, 0))


def test_golden_native_footage_pan(project, tmp_path):
    def pan(d):
        d["segments"][1]["camera_move"] = {"preset": "pan_left", "intensity": 1.0}
    _check("native_footage_pan", _native(project, tmp_path, pan, 1))


def _scene(kind: int) -> np.ndarray:
    """Synthetic frame: a diagonal gradient with a block and a stripe placed by `kind` (exact, no codec)."""
    y, x = np.mgrid[0:H, 0:W]
    img = np.stack([(x * 255 // W), (y * 255 // H), np.full_like(x, 60 + 80 * kind)], axis=-1).astype(np.uint8)
    img[40 + 60 * kind:120 + 60 * kind, 30:110] = (230, 40 + 100 * kind, 30)
    img[:, W - 20 - 40 * kind:W - 10 - 40 * kind] = 245
    return img


def _moving(kind: int, n: int) -> list[np.ndarray]:
    """n frames of _scene(kind) sliding 3 px per frame, so a held frame differs from the live ones."""
    return [np.roll(_scene(kind), 3 * i, axis=1) for i in range(n)]


@pytest.mark.parametrize("kind, seconds", [("crossfade", profiles.TransitionsProfile().fade_s),
                                           ("state", profiles.TransitionsProfile().state_s)])
def test_golden_dissolve_frames(kind, seconds):
    """crossfade (0.4 s) and state (0.2 s) at 30 fps, split around the boundary as transitions.windows does."""
    n = round(seconds * 30)
    n_out, n_in = n // 2, n - n // 2
    tail, head = _moving(0, n_out), _moving(1, n_in)
    fr = transitions.fade_frames(tail, tail[-1], head[0], head)
    assert len(fr) == n
    _check(f"transition_{kind}", [fr[0], fr[n_out - 1], fr[n_out], fr[-1]])


def test_golden_zoom_whip_frames():
    tail, head = [_scene(0)] * 6, [_scene(1)] * 6
    fr = transitions.whip_frames(tail, head, profiles.TransitionsProfile(), 30)
    _check("transition_zoom_whip", [fr[2], fr[5], fr[6], fr[9]])


def test_golden_zoom_through_frames():
    T = profiles.TransitionsProfile(through_flash=0.6, through_in_scale=1.5)
    tail, head = _moving(0, 12), _moving(1, 15)
    fr = transitions.through_frames(tail, head, T, 30)
    _check("transition_zoom_through", [fr[5], fr[11], fr[12], fr[18], fr[26]])


def test_golden_page_turn_frames():
    fr = transitions.page_frames(_scene(0), [_scene(1)] * 9)
    _check("transition_page_turn", [fr[1], fr[4], fr[7]])


def test_golden_colour_grade(project, tmp_path):
    def mut(d):
        _small(d)
        d["segments"][0]["grade"] = {"lift": 0.04, "gamma": 1.15, "gain": 0.95, "saturation": 1.35}
    p, tl = project(mut)
    src = engine_clip(tmp_path / "s.mp4", tl.segments[0].n_frames, size=(W, H), src="testsrc2")
    out = grade.apply(tl.segments[0], tl, p, src, tmp_path / "wd")[0]
    _check("grade_lift_gamma_gain_sat", _frames(out, [0, 30]))
