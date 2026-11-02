"""config.WORKSPACE: one variable moves the default location of the shared libraries; each library keeps its own
override, and with nothing set the defaults are the paths of the render machine."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VARS = ("VIDEO_ENGINE_WORKSPACE", "VIDEO_ENGINE_GEO_LIBRARY", "VIDEO_ENGINE_OPEN_ASSETS", "VIDEO_ENGINE_STOCK_DIRS",
        "VIDEO_ENGINE_DOWNLOAD_DIR")
SCRIPT = ("from video_engine import config, library; "
          "print(config.WORKSPACE, config.GEO_LIBRARY, config.OPEN_ASSETS, "
          "':'.join(map(str, config.STOCK_DIRS)), config.DOWNLOAD_DIR, library.OPEN_ASSETS, "
          "':'.join(map(str, library.STOCK_DIRS)), sep='|')")


def paths(**env) -> list[str]:
    """Run a fresh interpreter (config reads the environment at import) and return the resolved defaults."""
    base = {k: v for k, v in os.environ.items() if k not in VARS}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env={**base, **env}, capture_output=True,
                         text=True, check=True).stdout.strip()
    return out.split("|")


def test_good_without_any_variable_the_defaults_are_the_render_machine_paths():
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths()
    assert (ws, geo, openassets, stock, dl) == ("/workspace", "/workspace/geo-library", "/workspace/open-assets",
                                                "/workspace/asset-cache", "/workspace/downloads")
    assert lib_open == "/workspace/open-assets"
    assert lib_stock == "/workspace/asset-cache/footage:/workspace/asset-cache"


def test_good_one_variable_moves_every_default(tmp_path):
    w = str(tmp_path / "ws")
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths(VIDEO_ENGINE_WORKSPACE=w)
    assert (ws, geo, openassets, stock, dl) == (w, f"{w}/geo-library", f"{w}/open-assets", f"{w}/asset-cache",
                                                f"{w}/downloads")
    assert lib_open == f"{w}/open-assets" and lib_stock == f"{w}/asset-cache/footage:{w}/asset-cache"


def test_good_a_specific_variable_still_wins_over_the_workspace(tmp_path):
    w = str(tmp_path / "ws")
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths(
        VIDEO_ENGINE_WORKSPACE=w, VIDEO_ENGINE_GEO_LIBRARY="/data/geo", VIDEO_ENGINE_OPEN_ASSETS="/data/open",
        VIDEO_ENGINE_STOCK_DIRS="/data/a:/data/b", VIDEO_ENGINE_DOWNLOAD_DIR="/data/dl")
    assert (geo, openassets, stock, dl) == ("/data/geo", "/data/open", "/data/a:/data/b", "/data/dl")
    assert lib_open == "/data/open" and lib_stock == "/data/a:/data/b"
    assert ws == w                                                       # the workspace itself is still reported


def test_bad_no_source_file_hardcodes_the_workspace_path_any_more():
    offenders = []
    for f in (ROOT / "video_engine").rglob("*.py"):
        if f.name == "config.py":                       # the one place that names the default
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if '"/workspace' in code or "'/workspace" in code:
                offenders.append(f"{f.relative_to(ROOT)}:{n}")
    assert offenders == [], "use config.WORKSPACE instead of a literal /workspace path"
