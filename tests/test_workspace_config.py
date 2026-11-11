"""config.WORKSPACE: one variable moves the default location of the shared libraries; each library keeps its own
override. With nothing set the default is the portable user-data directory, not a machine-specific path."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from video_engine import discover


ROOT = Path(__file__).resolve().parents[1]
VARS = ("VIDEO_ENGINE_WORKSPACE", "VIDEO_ENGINE_GEO_LIBRARY", "VIDEO_ENGINE_OPEN_ASSETS", "VIDEO_ENGINE_STOCK_DIRS",
        "VIDEO_ENGINE_DOWNLOAD_DIR", "VIDEO_ENGINE_ENV_FILE")
SCRIPT = ("import os; from video_engine import config, library; "
          "print(config.WORKSPACE, config.GEO_LIBRARY, config.OPEN_ASSETS, "
          "os.pathsep.join(map(str, config.STOCK_DIRS)), config.DOWNLOAD_DIR, library.OPEN_ASSETS, "
          "os.pathsep.join(map(str, library.STOCK_DIRS)), sep=\'|\')")


def _isolated_env(**env) -> dict[str, str]:
    """Fresh environment that cannot see the operator's engine.env or XDG dirs."""
    base = {k: v for k, v in os.environ.items() if k not in VARS}
    td = Path(tempfile.mkdtemp(prefix="ve-ws-test-"))
    home = td / "home"
    home.mkdir()
    isolated = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(td / "config"),
        "XDG_DATA_HOME": str(td / "data"),
        "XDG_CACHE_HOME": str(td / "cache"),
        "LOCALAPPDATA": str(td / "localapp"),
        "APPDATA": str(td / "appdata"),
    }
    return {**base, **isolated, **env}


def paths(env: dict[str, str] | None = None, **overrides) -> list[str]:
    """Run a fresh interpreter (config reads the environment at import) and return the resolved defaults."""
    merged = env if env is not None else _isolated_env(**overrides)
    if env is not None and overrides:
        merged = {**env, **overrides}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=merged, capture_output=True,
                         text=True, check=True).stdout.strip()
    return out.split("|")


def test_good_without_any_variable_the_defaults_are_the_platform_paths():
    """Portable user-data dir (`<XDG_DATA_HOME or ~/.local/share>/video-engine` on Linux, and the
    equivalent on macOS / Windows), never a hard-coded machine path."""
    env = _isolated_env()
    w = str(discover.default_workspace(environ=env, home=Path(env["HOME"]), system=sys.platform))
    # The isolated XDG_DATA_HOME (Linux) or LOCALAPPDATA (Windows) or ~/Library (macOS) must be the parent.
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths(env)
    sep = os.pathsep
    assert (ws, geo, openassets, stock, dl) == (w, f"{w}/geo-library", f"{w}/open-assets", f"{w}/asset-cache",
                                                f"{w}/downloads")
    assert lib_open == f"{w}/open-assets"
    assert lib_stock == f"{w}/asset-cache/footage{sep}{w}/asset-cache"
    # And the default itself must not be the old Linux render-box path.
    assert w != "/workspace"


def test_good_one_variable_moves_every_default(tmp_path):
    w = str(tmp_path / "ws")
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths(VIDEO_ENGINE_WORKSPACE=w)
    sep = os.pathsep
    assert (ws, geo, openassets, stock, dl) == (w, f"{w}/geo-library", f"{w}/open-assets", f"{w}/asset-cache",
                                                f"{w}/downloads")
    assert lib_open == f"{w}/open-assets" and lib_stock == f"{w}/asset-cache/footage{sep}{w}/asset-cache"


def test_good_a_specific_variable_still_wins_over_the_workspace(tmp_path):
    w = str(tmp_path / "ws")
    ws, geo, openassets, stock, dl, lib_open, lib_stock = paths(
        VIDEO_ENGINE_WORKSPACE=w, VIDEO_ENGINE_GEO_LIBRARY="/data/geo", VIDEO_ENGINE_OPEN_ASSETS="/data/open",
        VIDEO_ENGINE_STOCK_DIRS=os.pathsep.join(["/data/a", "/data/b"]), VIDEO_ENGINE_DOWNLOAD_DIR="/data/dl")
    assert (geo, openassets, stock, dl) == ("/data/geo", "/data/open",
                                            os.pathsep.join(["/data/a", "/data/b"]), "/data/dl")
    assert lib_open == "/data/open" and lib_stock == os.pathsep.join(["/data/a", "/data/b"])
    assert ws == w                                                       # the workspace itself is still reported


def test_good_engine_env_fills_unset_variables_only(tmp_path):
    envfile = tmp_path / "engine.env"
    envfile.write_text("VIDEO_ENGINE_WORKSPACE=/data/from-env-file\nVIDEO_ENGINE_GEO_LIBRARY=/data/geo-env\n")
    ws, geo, *_ = paths(VIDEO_ENGINE_ENV_FILE=str(envfile))
    assert ws == "/data/from-env-file" and geo == "/data/geo-env"
    ws2, geo2, *_ = paths(VIDEO_ENGINE_ENV_FILE=str(envfile), VIDEO_ENGINE_WORKSPACE="/explicit")
    assert ws2 == "/explicit" and geo2 == "/data/geo-env"               # explicit env wins; other keys still load


def test_bad_no_source_file_hardcodes_the_workspace_path_any_more():
    offenders = []
    for f in (ROOT / "video_engine").rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if '"/workspace' in code or "'/workspace" in code:
                offenders.append(f"{f.relative_to(ROOT)}:{n}")
    assert offenders == [], "use config.WORKSPACE or a local engine.env instead of a literal /workspace path"
