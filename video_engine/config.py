"""Engine-wide paths and limits. Everything machine-specific is here and overridable by environment variables."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
VENDOR = Path(os.environ.get("VIDEO_ENGINE_VENDOR", ENGINE_ROOT / "vendor"))
FONTS_DIR = VENDOR / "fonts"
TEXTURES_DIR = VENDOR / "textures"
GSAP_JS = VENDOR / "js" / "gsap.min.js"
TEMPLATE_DIR = PKG / "template"
PROFILES_DIR = Path(os.environ.get("VIDEO_ENGINE_PROFILES", ENGINE_ROOT / "profiles"))
GEO_LIBRARY = Path(os.environ.get("VIDEO_ENGINE_GEO_LIBRARY", "/workspace/geo-library"))
OPEN_ASSETS = Path(os.environ.get("VIDEO_ENGINE_OPEN_ASSETS", "/workspace/open-assets"))
STOCK_DIRS = tuple(Path(p) for p in os.environ.get("VIDEO_ENGINE_STOCK_DIRS", "/workspace/asset-cache").split(":") if p)
DOWNLOAD_DIR = Path(os.environ.get("VIDEO_ENGINE_DOWNLOAD_DIR", "/workspace/downloads"))

# Persistent content-addressed cache (word timestamps, rendered segments) on disk; survives /dev/shm cleanup.
CACHE_DIR = Path(os.environ.get("VIDEO_ENGINE_CACHE", ENGINE_ROOT / ".cache"))
CACHE_MAX_BYTES = int(os.environ.get("VIDEO_ENGINE_CACHE_MAX_MB", "4096")) * 1024 * 1024
SHM_ROOT = Path(os.environ.get("VIDEO_ENGINE_SHM", "/dev/shm/video_engine"))

# CPU budget: max threads used by encoders and rayon. Default 6 (of 8 vCPU).
THREADS = max(1, int(os.environ.get("VIDEO_ENGINE_THREADS", "6")))
# Rust (rayon) pool: THREADS-2 by default so concurrent ffmpeg encoders/decoders keep headroom.
RUST_THREADS = max(1, min(256, int(os.environ.get("VIDEO_ENGINE_RUST_THREADS", str(max(1, THREADS - 2))))))
os.environ.setdefault("RAYON_NUM_THREADS", str(RUST_THREADS))
import hypoverum_core_rs as _core  # after RAYON_NUM_THREADS so the pool size holds either way

RUST_THREADS_ACTIVE = _core.set_threads(RUST_THREADS)

# HyperFrames toolchain: pinned in vendor/VERSIONS.json, installed into vendor/ by `engine doctor --install`.
_PINS = json.loads((VENDOR / "VERSIONS.json").read_text())
_CH = _PINS["chrome"]
NODE = VENDOR / "node" / "bin" / "node"
HF_CLI = VENDOR / "npm" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
PUPPETEER = VENDOR / "npm" / "node_modules" / "puppeteer-core"
CHROME = VENDOR / "chrome" / _CH["product"] / f"linux-{_CH['version']}" / f"{_CH['product']}-linux64" / _CH["product"]

FFMPEG = os.environ.get("VIDEO_ENGINE_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("VIDEO_ENGINE_FFPROBE", "ffprobe")

# Encoder defaults when a profile's [render] sets nothing for a stage. Intermediates are encoded again by the final
# composite, so they use a fast preset at a low CRF: crf 16 veryfast matches crf 18 medium after the final pass
# (PSNR within 0.05 dB, SSIM within 0.002) at the same file size in about 40 % of the encode time.
SEGMENT_CRF, SEGMENT_PRESET = 16, "veryfast"
FINAL_CRF, FINAL_PRESET = 18, "medium"


def x264(crf: int = FINAL_CRF, preset: str = FINAL_PRESET, tune: str | None = None,
         maxrate_kbps: int | None = None) -> list[str]:
    """libx264 encoder arguments (yuv420p, bt709 tags); maxrate_kbps caps the CRF rate (VBV, bufsize 2x)."""
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), *(["-tune", tune] if tune else []),
            *(["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{2 * maxrate_kbps}k"] if maxrate_kbps else []),
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


# Colour conversion of every engine encode: RGB frames -> BT.709 limited-range yuv420p (matches the x264 bt709 tags).
BT709_VF = "scale=out_color_matrix=bt709:out_range=tv:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p"
# An RGBA overlay (native overlays, watermark) converted on its own to BT.709 yuva420p and blended with
# overlay=format=yuv420 onto a BT.709 yuv420p base: the base pixels are never converted again (an auto-negotiated
# overlay format re-converts the whole frame with a BT.601 matrix and shifts saturated colours by up to ~25 levels).
OVERLAY_YUV_VF = "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuva420p"

X264 = x264()                                        # final composite default (no [render] final_* values)
X264_SEGMENT = x264(SEGMENT_CRF, SEGMENT_PRESET)     # intermediate default (no [render] segment_* values)


def hf_env() -> dict:
    env = dict(os.environ)
    env.update({
        "HYPERFRAMES_BROWSER_PATH": str(CHROME), "PRODUCER_HEADLESS_SHELL_PATH": str(CHROME),
        "HYPERFRAMES_SKIP_SKILLS": "1", "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1",
        "HYPERFRAMES_FFMPEG_PATH": "/usr/bin/ffmpeg", "HYPERFRAMES_FFPROBE_PATH": "/usr/bin/ffprobe",
        "NODE_PATH": str(PUPPETEER.parent),
    })
    env["PATH"] = f"{NODE.parent}:{env.get('PATH', '')}"
    return env


# A project id names scratch dirs: letters, digits, "_", "." and "-", first character a letter, digit or "_"
# (no "." or ".." and no path separators).
PROJECT_ID_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$"


def check_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not re.fullmatch(PROJECT_ID_PATTERN, project_id):
        raise ValueError(f"project id {project_id!r} does not match {PROJECT_ID_PATTERN}")
    return project_id


def safe_project_id(name: str) -> str:
    """A valid project id derived from any name (a directory name, for example)."""
    pid = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return pid if re.fullmatch(PROJECT_ID_PATTERN, pid) else "_" + pid


def inside_shm(path: Path) -> Path:
    """`path` resolved (symlinks followed); it must be a strict descendant of the resolved SHM_ROOT."""
    root = SHM_ROOT.resolve()
    p = Path(path).resolve()
    if p == root or not p.is_relative_to(root):
        raise ValueError(f"{path} (resolved {p}) is not inside {root}")
    return p


def workdir(project_id: str, base: Path | None = None) -> Path:
    """Scratch dir of one invocation, created here: <base>/<project_id>-<pid>-<random> (base = SHM_ROOT, or a dir
    strictly inside it). Every call returns a dir no other invocation uses; the caller owns it and removes it."""
    check_project_id(project_id)
    SHM_ROOT.mkdir(parents=True, exist_ok=True)
    if base is None:
        base = SHM_ROOT.resolve()
    else:
        base = inside_shm(base)
        base.mkdir(parents=True, exist_ok=True)
        base = inside_shm(base)   # created, then re-checked: a symlink placed meanwhile cannot lead outside
    return inside_shm(Path(tempfile.mkdtemp(prefix=f"{project_id}-{os.getpid()}-", dir=base)))
