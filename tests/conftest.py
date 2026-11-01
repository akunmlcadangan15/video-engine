"""Shared fixtures: a small synthetic project (generated media, no third-party assets) and timeline builders."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# All test scratch lives under one per-process root that the session removes at exit. The parent scratch dir is
# removed only when this session created it and it is empty afterwards (a real build's scratch is never touched).
SHM_PARENT = Path(os.environ.get("VIDEO_ENGINE_SHM_TEST", "/tmp/video_engine_test_shm"))
TEST_ROOT = SHM_PARENT / f"pytest-{os.getpid()}"
_PARENT_PREEXISTED = SHM_PARENT.exists()
TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["VIDEO_ENGINE_SHM"] = str(TEST_ROOT / "shm")
os.environ["VIDEO_ENGINE_CACHE"] = str(TEST_ROOT / "cache")
# AI provider selection is hermetic: no provider env, no AI config file unless a test writes one.
os.environ.pop("VIDEO_ENGINE_AI_PROVIDER", None)
os.environ["VIDEO_ENGINE_AI_CONFIG"] = str(TEST_ROOT / "no_ai_providers.toml")

ASSETS = TEST_ROOT / "assets"


def cleanup_scratch(root: Path, parent: Path, parent_preexisted: bool) -> None:
    """Remove the test root; remove `parent` only if the session created it and nothing else is in it."""
    shutil.rmtree(root, ignore_errors=True)
    if not parent_preexisted:
        try:
            parent.rmdir()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _taste_dir(monkeypatch, request):
    """Each test gets its own (empty) channel taste store; the tracked seeds in profiles/taste are never written."""
    import hashlib
    d = TEST_ROOT / "taste" / hashlib.blake2b(request.node.nodeid.encode(), digest_size=8).hexdigest()
    monkeypatch.setenv("VIDEO_ENGINE_TASTE_DIR", str(d))
    return d


def pytest_configure(config):
    if not config.option.basetemp:
        config.option.basetemp = str(TEST_ROOT / "tmp")


def pytest_unconfigure(config):
    cleanup_scratch(TEST_ROOT, SHM_PARENT, _PARENT_PREEXISTED)


VENDOR = Path(__file__).resolve().parents[1] / "vendor"


def missing_vendored_tool(e: BaseException) -> str | None:
    """The vendored toolchain file (vendor/node, vendor/chrome, vendor/npm) a FileNotFoundError reports missing."""
    name = getattr(e, "filename", None)
    if not isinstance(e, FileNotFoundError) or not name:
        return None
    p = Path(str(name))
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if p.exists() or not p.is_relative_to(VENDOR):
        return None
    return str(name)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """A test that needs the vendored HyperFrames toolchain skips with a hint while it is not installed
    (`engine doctor --install`) instead of failing; VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1 (the nightly CI job, which
    installs it) makes such a test fail."""
    try:
        return (yield)
    except FileNotFoundError as e:
        missing = missing_vendored_tool(e)
        if missing and os.environ.get("VIDEO_ENGINE_REQUIRE_TOOLCHAIN") != "1":
            pytest.skip(f"vendored toolchain not installed ({missing}); run `engine doctor --install`")
        raise


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def engine_clip(out: Path, n: int, size=(1080, 1920), fps: int = 30, src: str = "testsrc2", extra=()) -> Path:
    """n-frame clip in the engine segment format (raw RGB frames -> config.X264_SEGMENT, as native._encoder); `extra`
    output options are appended after config.X264_SEGMENT."""
    from video_engine import config
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"{src}{':' if '=' in src else '='}size={w}x{h}:rate={fps}", "-frames:v",
                          str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
                    "-i", "-", *config.X264_SEGMENT, *map(str, extra), "-frames:v", str(n), "-an", "-movflags", "+faststart",
                    str(out)], input=raw, check=True)
    return out


@pytest.fixture(scope="session")
def assets() -> Path:
    ASSETS.mkdir(parents=True, exist_ok=True)
    img = ASSETS / "still.jpg"
    if not img.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=1600x1200:rate=1", "-frames:v", "1", img)
    vid = ASSETS / "clip_1080x1920_30.mp4"
    if not vid.exists():   # engine stream format: h264 yuv420p 30 fps, keyframe every 30 frames
        ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", vid)
    land = ASSETS / "clip_1280x720_25.mp4"
    if not land.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", land)
    mp = ASSETS / "map.jpg"
    if not mp.exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=2700x2880:rate=1", "-frames:v", "1", mp)   # >= 1.2 texels per screen px (M02)
    tone = ASSETS / "tone.wav"
    if not tone.exists():
        ff("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-af", "volume=-26dB", tone)
    return ASSETS


def base_doc(assets: Path) -> dict:
    url = "https://example.org/asset/"
    return {
        "project_id": "pytest_proj",
        "settings": {"width": 1080, "height": 1920, "fps": 30, "project_number": "07", "topic": "Uji Coba",
                     "date": "2026-10-28"},
        "credits_manifest": [
            {"asset_id": "still", "path": "assets/still.jpg", "source_url": url + "still", "license": "CC-BY-4.0",
             "watermark_free": True},
            {"asset_id": "clip", "path": "assets/clip_1080x1920_30.mp4", "source_url": url + "clip",
             "license": "Pexels License", "watermark_free": True},
            {"asset_id": "map", "path": "assets/map.jpg", "source_url": url + "map", "license": "Public Domain",
             "watermark_free": True},
        ],
        "segments": [
            {"seg_index": 0, "engine_type": "NATIVE_RUST_FFMPEG", "start_frame": 0, "end_frame": 60,
             "source_image": "assets/still.jpg", "asset_id": "still",
             "camera_move": {"preset": "ken_burns_zoom_in", "intensity": 1.0}},
            {"seg_index": 1, "engine_type": "NATIVE_RUST_FFMPEG", "start_frame": 60, "end_frame": 120,
             "source_video": "assets/clip_1080x1920_30.mp4", "asset_id": "clip"},
            {"seg_index": 2, "engine_type": "HYPERFRAMES_GSAP", "start_frame": 120, "end_frame": 180,
             "gsap_scene_config": {"map_asset": "assets/map.jpg", "map_asset_id": "map",
                                   "pins": [{"label": "Titik A", "x": 0.4, "y": 0.3}],
                                   "camera_preset": "zoom_in"}},
        ],
    }


@pytest.fixture
def project(tmp_path, assets):
    """Returns make(doc_mutator=None, credits_txt=True) -> (project_dir, Timeline)."""
    from video_engine import timeline

    def make(mut=None, credits_txt: bool = True):
        d = tmp_path / "proj"
        (d / "assets").mkdir(parents=True, exist_ok=True)
        for f in assets.iterdir():
            dst = d / "assets" / f.name
            if not dst.exists():
                os.symlink(f, dst)
        doc = copy.deepcopy(base_doc(assets))
        if mut:
            mut(doc)
        (d / "master_timeline.json").write_text(json.dumps(doc))
        if credits_txt:
            (d / "credits.txt").write_text("\n".join(c["source_url"] for c in doc["credits_manifest"]) + "\n")
        elif (d / "credits.txt").exists():
            (d / "credits.txt").unlink()
        return d, timeline.load(d)
    return make


def words(*items):
    """words(("Halo", 0, 10), ...) -> list[CaptionWord]."""
    from video_engine.timeline import CaptionWord
    return [CaptionWord(word=w, start_frame=a, end_frame=b) for w, a, b in items]


@pytest.fixture
def W():
    return words
