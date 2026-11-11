"""Engine-wide paths and limits. Everything machine-specific is here and overridable by environment variables."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import discover, platform_info

# Local engine.env (VIDEO_ENGINE_ENV_FILE, ./engine.env, or <user-config>/video-engine/engine.env)
# fills in unset variables only: an explicit environment always wins.
for _k, _v in discover.load_engine_env().items():
    os.environ.setdefault(_k, _v)

ENGINE_ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
VENDOR = Path(os.environ.get("VIDEO_ENGINE_VENDOR", ENGINE_ROOT / "vendor"))
FONTS_DIR = VENDOR / "fonts"
TEXTURES_DIR = VENDOR / "textures"
GSAP_JS = VENDOR / "js" / "gsap.min.js"
TEMPLATE_DIR = PKG / "template"
PROFILES_DIR = Path(os.environ.get("VIDEO_ENGINE_PROFILES", ENGINE_ROOT / "profiles"))
# Folder that holds the machine's shared libraries (geo library, open assets, stock footage, downloads). Each default
# below sits in it and each has its own override. The default is the portable user-data directory
# (`<XDG_DATA_HOME or ~/.local/share>/video-engine` on Linux, `~/Library/Application Support/video-engine` on macOS,
# `%LOCALAPPDATA%\video-engine` on Windows). Machine-specific folders belong in a local engine.env, not here.
WORKSPACE = Path(os.environ.get("VIDEO_ENGINE_WORKSPACE", str(discover.default_workspace())))
GEO_LIBRARY = Path(os.environ.get("VIDEO_ENGINE_GEO_LIBRARY", WORKSPACE / "geo-library"))
OPEN_ASSETS = Path(os.environ.get("VIDEO_ENGINE_OPEN_ASSETS", WORKSPACE / "open-assets"))
STOCK_DIRS = tuple(Path(p) for p in os.environ.get("VIDEO_ENGINE_STOCK_DIRS", str(WORKSPACE / "asset-cache")).split(os.pathsep) if p)
DOWNLOAD_DIR = Path(os.environ.get("VIDEO_ENGINE_DOWNLOAD_DIR", WORKSPACE / "downloads"))

# Persistent content-addressed cache (word timestamps, rendered segments) on disk; survives /dev/shm cleanup.
CACHE_DIR = Path(os.environ.get("VIDEO_ENGINE_CACHE", ENGINE_ROOT / ".cache"))
CACHE_MAX_BYTES = int(os.environ.get("VIDEO_ENGINE_CACHE_MAX_MB", "4096")) * 1024 * 1024
# /dev/shm/video_engine on Linux, <tempdir>/video_engine on macOS (no /dev/shm there).
SHM_ROOT = Path(os.environ.get("VIDEO_ENGINE_SHM", platform_info.default_shm_root()))

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
def _resolved(res, fallback: Path | str):
    return res.path if res.found and res.path is not None else fallback

# Tool paths: env override > vendor > global npm / PATH > platform-standard locations (discover.py).
# When nothing is found the historical vendor (or "ffmpeg") path is kept so error messages stay stable.
_NODE_RES = discover.resolve_node(vendor=VENDOR, environ=os.environ)
NODE = Path(_resolved(_NODE_RES, VENDOR / "node" / "bin" / "node"))
_HF_RES = discover.resolve_hyperframes(vendor=VENDOR, environ=os.environ)
HF_CLI = Path(_HF_RES.path / "bin" / "hyperframes.mjs") if (_HF_RES.found and _HF_RES.path and (_HF_RES.path / "bin" / "hyperframes.mjs").is_file()) else (
    Path(_HF_RES.path) if (_HF_RES.found and _HF_RES.path and _HF_RES.path.suffix) else VENDOR / "npm" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs")
_PPTR_RES = discover.resolve_puppeteer(vendor=VENDOR, environ=os.environ)
PUPPETEER = Path(_resolved(_PPTR_RES, VENDOR / "npm" / "node_modules" / "puppeteer-core"))
# Unsupported platform: importing config must still work, so CHROME points at a path that does not exist and
# `engine doctor` reports the platform (and so the missing chrome) as FAIL.
_PLATFORM = platform_info.supported_key()
_CHROME_FALLBACK = VENDOR / "chrome" / _CH["product"] / (platform_info.chrome_relpath(_CH["product"], _CH["version"], _PLATFORM)
                                                         if _PLATFORM else Path("unsupported-platform") / _CH["product"])
# On an unsupported platform keep the historical fallback path so `engine doctor` can report the platform
# (discovery must not pick a real Chrome built for a different OS).
if _PLATFORM:
    CHROME = Path(_resolved(discover.resolve_chrome(vendor=VENDOR, environ=os.environ), _CHROME_FALLBACK))
else:
    CHROME = _CHROME_FALLBACK

_FF_RES = discover.resolve_ffmpeg(environ=os.environ, probe=False)
_FP_RES = discover.resolve_ffmpeg(environ=os.environ, probe=True)
FFMPEG = str(_resolved(_FF_RES, os.environ.get("VIDEO_ENGINE_FFMPEG", "ffmpeg")))
FFPROBE = str(_resolved(_FP_RES, os.environ.get("VIDEO_ENGINE_FFPROBE", "ffprobe")))
# ffmpeg the HyperFrames CLI encodes with: the resolved binaries (absolute when found).
_SYSTEM_FFMPEG, _SYSTEM_FFPROBE = (str(Path(FFMPEG).resolve()) if Path(FFMPEG).is_file() else FFMPEG,
                                   str(Path(FFPROBE).resolve()) if Path(FFPROBE).is_file() else FFPROBE)

# Encoder defaults when a profile's [render] sets nothing for a stage. Intermediates are encoded again by the final
# composite, so they use a fast preset at a low CRF: crf 16 veryfast matches crf 18 medium after the final pass
# (PSNR within 0.05 dB, SSIM within 0.002) at the same file size in about 40 % of the encode time.
SEGMENT_CRF, SEGMENT_PRESET = 16, "veryfast"
FINAL_CRF, FINAL_PRESET = 18, "medium"


def pix_fmt(bit_depth: int = 8) -> str:
    """Engine encode pixel format: 4:2:0 at 8 or 10 bits."""
    return "yuv420p10le" if bit_depth == 10 else "yuv420p"


def x264(crf: int = FINAL_CRF, preset: str = FINAL_PRESET, tune: str | None = None,
         maxrate_kbps: int | None = None, bit_depth: int = 8) -> list[str]:
    """libx264 encoder arguments (yuv420p, or yuv420p10le + High 10 at bit_depth 10; bt709 tags); maxrate_kbps caps
    the CRF rate (VBV, bufsize 2x)."""
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), *(["-tune", tune] if tune else []),
            *(["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{2 * maxrate_kbps}k"] if maxrate_kbps else []),
            "-pix_fmt", pix_fmt(bit_depth), *(["-profile:v", "high10"] if bit_depth == 10 else []),
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


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
        "HYPERFRAMES_FFMPEG_PATH": _SYSTEM_FFMPEG, "HYPERFRAMES_FFPROBE_PATH": _SYSTEM_FFPROBE,
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
