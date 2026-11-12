"""Portable tool and asset discovery: search order, overrides, missing case, per-platform roots (mocked env)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_engine import discover


def _touch(p: Path, executable: bool = False) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    if executable:
        p.chmod(0o755)
    return p


def _pkg(root: Path, name: str, version: str = "1.0.0") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps({"name": name, "version": version}))
    return d


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


# ---------------------------------------------------------------------------
# Platform directories
# ---------------------------------------------------------------------------

def test_good_user_dirs_linux_xdg_and_defaults(tmp_path):
    h = tmp_path / "home"
    xdg = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "HOME": str(h)}
    assert discover.user_data_dir(xdg, "linux") == tmp_path / "data"
    assert discover.user_cache_dir(xdg, "linux") == tmp_path / "cache"
    assert discover.user_config_dir(xdg, "linux") == tmp_path / "config"
    bare = {"HOME": str(h)}
    assert discover.user_data_dir(bare, "linux") == h / ".local" / "share"
    assert discover.user_cache_dir(bare, "linux") == h / ".cache"
    assert discover.user_config_dir(bare, "linux") == h / ".config"
    assert discover.user_bin_dir(bare, "linux") == h / ".local" / "bin"


def test_good_user_dirs_darwin(tmp_path):
    h = tmp_path / "home"
    env = {"HOME": str(h)}
    assert discover.user_data_dir(env, "darwin") == h / "Library" / "Application Support"
    assert discover.user_cache_dir(env, "darwin") == h / "Library" / "Caches"
    assert discover.user_config_dir(env, "darwin") == h / "Library" / "Application Support"
    assert discover.user_bin_dir(env, "darwin") == h / ".local" / "bin"


def test_good_user_dirs_win32(tmp_path):
    h = tmp_path / "home"
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    env = {"USERPROFILE": str(h), "LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
    assert discover.user_data_dir(env, "win32") == local
    assert discover.user_cache_dir(env, "win32") == local
    assert discover.user_config_dir(env, "win32") == roaming
    assert discover.user_bin_dir(env, "win32") == local / "Programs"
    bare = {"USERPROFILE": str(h), "HOME": str(h)}
    assert discover.user_data_dir(bare, "win32") == h / "AppData" / "Local"
    assert discover.user_config_dir(bare, "win32") == h / "AppData" / "Roaming"


def test_good_default_workspace_is_under_user_data_not_a_machine_folder(tmp_path):
    h = tmp_path / "home"
    env = {"HOME": str(h)}
    assert discover.default_workspace(env, "linux", h) == h / ".local" / "share" / "video-engine"
    assert discover.default_workspace(env, "darwin", h) == h / "Library" / "Application Support" / "video-engine"
    env_w = {"HOME": str(h), "LOCALAPPDATA": str(tmp_path / "Local")}
    assert discover.default_workspace(env_w, "win32", h) == tmp_path / "Local" / "video-engine"
    # no hardcoded render-server path
    for fam in ("linux", "darwin", "win32"):
        text = str(discover.default_workspace(env if fam != "win32" else env_w, fam, h))
        assert "/workspace" not in text.replace(str(tmp_path), "")


def test_good_workspace_env_override(tmp_path):
    env = {"HOME": str(tmp_path), "VIDEO_ENGINE_WORKSPACE": str(tmp_path / "ws")}
    assert discover.default_workspace(env, "linux") == tmp_path / "ws"


# ---------------------------------------------------------------------------
# engine.env
# ---------------------------------------------------------------------------

def test_good_parse_env_file_strips_export_and_quotes():
    text = '# c\nexport FOO=bar\nBAZ="qux quux"\nEMPTY=\nnope\n'
    assert discover.parse_env_file(text) == {"FOO": "bar", "BAZ": "qux quux", "EMPTY": ""}


def test_good_load_engine_env_prefers_explicit_then_cwd_then_user_config(tmp_path):
    home = tmp_path / "home"
    cfg = discover.user_config_dir({"HOME": str(home)}, "linux", home) / "video-engine" / "engine.env"
    cwd_f = tmp_path / "cwd" / "engine.env"
    expl = tmp_path / "explicit.env"
    _touch(cfg).write_text("FROM=config\n")
    _touch(cwd_f).write_text("FROM=cwd\n")
    _touch(expl).write_text("FROM=explicit\n")
    env = {"HOME": str(home), "VIDEO_ENGINE_ENV_FILE": str(expl)}
    assert discover.load_engine_env(env, "linux", home, cwd=tmp_path / "cwd")["FROM"] == "explicit"
    env.pop("VIDEO_ENGINE_ENV_FILE")
    assert discover.load_engine_env(env, "linux", home, cwd=tmp_path / "cwd")["FROM"] == "cwd"
    assert discover.load_engine_env(env, "linux", home, cwd=tmp_path / "empty")["FROM"] == "config"
    other = tmp_path / "other-home"
    other.mkdir()
    assert discover.load_engine_env({"HOME": str(other)}, "linux", other, cwd=tmp_path / "empty") == {}


# ---------------------------------------------------------------------------
# npm roots
# ---------------------------------------------------------------------------

def test_good_npm_global_dirs_include_node_path_and_platform_locations(tmp_path):
    h = tmp_path / "home"
    linux = discover.default_npm_global_dirs({"HOME": str(h), "NODE_PATH": str(tmp_path / "np")}, "linux", h)
    assert linux[0] == tmp_path / "np"
    assert h / ".local" / "lib" / "node_modules" in linux
    assert Path("/usr/local/lib/node_modules") in linux
    mac = discover.default_npm_global_dirs({"HOME": str(h)}, "darwin", h)
    assert Path("/opt/homebrew/lib/node_modules") in mac
    win = discover.default_npm_global_dirs(
        {"USERPROFILE": str(h), "APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local"),
         "ProgramFiles": str(tmp_path / "pf")},
        "win32", h)
    assert tmp_path / "Roaming" / "npm" / "node_modules" in win
    assert tmp_path / "pf" / "nodejs" / "node_modules" in win


def test_good_npm_root_g_callback_is_tried_first(tmp_path):
    extra = tmp_path / "from-npm-root-g"
    extra.mkdir()
    roots = discover.npm_global_roots({"HOME": str(tmp_path), "NODE_PATH": ""}, "linux", tmp_path,
                                      npm_root_g=lambda: extra)
    assert roots[0] == extra


# ---------------------------------------------------------------------------
# Search order: env > npm global / PATH / wellknown > vendor  (prefer_vendor=True restores vendor-first)
# ---------------------------------------------------------------------------

def test_good_node_search_order_env_beats_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    _touch(vendor / "node" / "bin" / "node", executable=True)
    custom = _touch(tmp_path / "bin" / "node", executable=True)
    env = {"HOME": str(tmp_path), "PATH": "", "VIDEO_ENGINE_NODE": str(custom)}
    r = discover.resolve_node(vendor=vendor, environ=env, system="linux", home=tmp_path)
    assert r.found and r.path == custom and r.source == "env:VIDEO_ENGINE_NODE"


def test_good_node_path_beats_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    _touch(vendor / "node" / "bin" / "node", executable=True)
    pathd = tmp_path / "onpath"
    exe = _touch(pathd / "node", executable=True)
    env = {"HOME": str(tmp_path), "PATH": str(pathd)}
    r = discover.resolve_node(vendor=vendor, environ=env, system="linux", home=tmp_path, confine=tmp_path)
    assert r.found and r.path == exe and r.source == "PATH"


def test_good_node_vendor_beats_path_when_prefer_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    vend = _touch(vendor / "node" / "bin" / "node", executable=True)
    pathd = tmp_path / "onpath"
    _touch(pathd / "node", executable=True)
    env = {"HOME": str(tmp_path), "PATH": str(pathd)}
    r = discover.resolve_node(vendor=vendor, environ=env, system="linux", home=tmp_path, prefer_vendor=True)
    assert r.found and r.path == vend and r.source == "vendor"


def test_good_node_path_beats_wellknown(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    pathd = tmp_path / "onpath"
    exe = _touch(pathd / "node", executable=True)
    env = {"HOME": str(tmp_path), "PATH": str(pathd)}
    r = discover.resolve_node(vendor=vendor, environ=env, system="linux", home=tmp_path)
    assert r.found and r.path == exe and r.source == "PATH"


def test_good_node_missing_lists_searched_locations(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    r = discover.resolve_node(vendor=vendor, environ={"HOME": str(tmp_path), "PATH": ""}, system="linux",
                              home=tmp_path, confine=tmp_path)
    assert not r.found and r.source == "missing"
    assert any(s.startswith("env:VIDEO_ENGINE_NODE") for s in r.searched)
    assert any(s.startswith("vendor:") for s in r.searched)
    assert any(s.startswith("PATH:") for s in r.searched)
    assert any(s.startswith("wellknown:") for s in r.searched)


def test_good_hyperframes_found_in_global_npm(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    g = tmp_path / "g_node_modules"
    _pkg(g, "hyperframes", "0.9.13")
    env = {"HOME": str(tmp_path), "PATH": "", "NODE_PATH": str(g)}
    r = discover.resolve_hyperframes(vendor=vendor, environ=env, system="linux", home=tmp_path)
    assert r.found and r.source == "npm_global" and r.path == g / "hyperframes"


def test_good_hyperframes_path_cli_resolves_to_package_dir(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    pkg = _pkg(tmp_path / "lib" / "node_modules", "hyperframes")
    cli = pkg / "bin" / "hyperframes"
    _touch(cli, executable=True)
    # which() + resolve(): bin/hyperframes -> parent.parent == package
    env = {"HOME": str(tmp_path), "PATH": str(cli.parent)}
    r = discover.resolve_hyperframes(vendor=vendor, environ=env, system="linux", home=tmp_path)
    assert r.found and r.source == "PATH" and r.path == pkg


def test_good_hyperframes_global_npm_beats_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    _pkg(vendor / "npm" / "node_modules", "hyperframes", "0.9.13")
    g = tmp_path / "g_node_modules"
    got = _pkg(g, "hyperframes", "9.9.9")
    env = {"HOME": str(tmp_path), "PATH": "", "NODE_PATH": str(g)}
    r = discover.resolve_hyperframes(vendor=vendor, environ=env, system="linux", home=tmp_path)
    assert r.found and r.source == "npm_global" and r.path == got


def test_good_hyperframes_vendor_beats_global_npm_when_prefer_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    vend_pkg = _pkg(vendor / "npm" / "node_modules", "hyperframes", "0.9.13")
    g = tmp_path / "g_node_modules"
    _pkg(g, "hyperframes", "9.9.9")
    env = {"HOME": str(tmp_path), "PATH": "", "NODE_PATH": str(g)}
    r = discover.resolve_hyperframes(vendor=vendor, environ=env, system="linux", home=tmp_path, prefer_vendor=True)
    assert r.found and r.source == "vendor" and r.path == vend_pkg


def test_bad_missing_package_is_not_found(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    r = discover.resolve_three(vendor=vendor, environ={"HOME": str(tmp_path), "PATH": "", "NODE_PATH": ""},
                               system="linux", home=tmp_path, confine=tmp_path)
    assert not r.found and "three" in "".join(r.searched) or any("three" in s or "THREE" in s for s in r.searched)


# ---------------------------------------------------------------------------
# Chrome / ffmpeg / modal, including Windows mocked paths
# ---------------------------------------------------------------------------

def test_good_chrome_from_puppeteer_cache_on_darwin(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    home = tmp_path / "home"
    cache = home / "Library" / "Caches"
    bin_path = cache / "puppeteer" / "chrome-headless-shell" / "mac_arm-1" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
    _touch(bin_path, executable=True)
    env = {"HOME": str(home), "PATH": ""}
    r = discover.resolve_chrome(vendor=vendor, environ=env, system="darwin", home=home)
    assert r.found and r.source == "wellknown" and r.path == bin_path


def test_good_puppeteer_cache_lists_newest_build_and_headless_shell_first(tmp_path):
    cache = tmp_path / "cache"
    root = cache / "puppeteer"
    old = _touch(root / "chrome-headless-shell" / "linux-131.0.9" / "chrome-headless-shell-linux64" / "chrome-headless-shell", executable=True)
    new = _touch(root / "chrome-headless-shell" / "linux-131.0.10" / "chrome-headless-shell-linux64" / "chrome-headless-shell", executable=True)
    full = _touch(root / "chrome" / "linux-131.0.10" / "chrome-linux64" / "chrome", executable=True)
    _touch(root / "chrome" / "linux-131.0.10" / "chrome-linux64" / "README")          # not an executable name
    assert discover.puppeteer_cache_bins(cache) == [new, old, full]
    flat = tmp_path / "pptr_root"                          # a cache dir that is itself the Puppeteer root
    hs = _touch(flat / "chrome-headless-shell" / "linux-1" / "chrome-headless-shell-linux64" / "chrome-headless-shell", executable=True)
    assert discover.puppeteer_cache_bins(flat) == [hs]


def test_bad_puppeteer_cache_lookup_never_walks_an_unrelated_tree(tmp_path, monkeypatch):
    """Without <cache>/puppeteer the lookup reads only the Puppeteer product dirs of the cache dir: a browser-named
    executable deep in another tool's cache is neither found nor even looked at."""
    cache = tmp_path / "cache"
    deep = cache / "some-tool" / "a" / "b" / "c" / "chrome-headless-shell-linux64"
    _touch(deep / "chrome-headless-shell", executable=True)
    _touch(cache / "uv" / "x" / "y" / "chrome", executable=True)
    seen: list[Path] = []
    for name in ("is_file", "is_dir", "iterdir"):
        real = getattr(Path, name)
        monkeypatch.setattr(Path, name, lambda self, *a, _real=real, **k: (seen.append(self), _real(self, *a, **k))[1])
    assert discover.puppeteer_cache_bins(cache) == []
    unrelated = [cache / "some-tool", cache / "uv"]
    assert not [p for p in seen if any(p.is_relative_to(u) for u in unrelated)]


def test_good_chrome_wellknown_windows_location(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    local = tmp_path / "Local"
    exe = _touch(local / "Google" / "Chrome" / "Application" / "chrome.exe")
    env = {"USERPROFILE": str(tmp_path), "LOCALAPPDATA": str(local), "PATH": "", "PATHEXT": ".EXE"}
    r = discover.resolve_chrome(vendor=vendor, environ=env, system="win32", home=tmp_path)
    assert r.found and r.path == exe and r.source == "wellknown"


def test_good_ffmpeg_env_override_and_windows_program_files(tmp_path):
    custom = _touch(tmp_path / "my" / "ffmpeg", executable=True)
    r = discover.resolve_ffmpeg(environ={"HOME": str(tmp_path), "PATH": "", "VIDEO_ENGINE_FFMPEG": str(custom)},
                                system="linux", home=tmp_path)
    assert r.found and r.source == "env:VIDEO_ENGINE_FFMPEG" and r.path == custom
    pf = tmp_path / "Program Files"
    exe = _touch(pf / "ffmpeg" / "bin" / "ffmpeg.exe")
    r2 = discover.resolve_ffmpeg(environ={"USERPROFILE": str(tmp_path), "ProgramFiles": str(pf), "PATH": ""},
                                 system="win32", home=tmp_path)
    assert r2.found and r2.path == exe and r2.source == "wellknown"


def test_good_modal_in_user_bin_dir(tmp_path):
    home = tmp_path / "home"
    exe = _touch(home / ".local" / "bin" / "modal", executable=True)
    r = discover.resolve_modal(environ={"HOME": str(home), "PATH": ""}, system="linux", home=home)
    assert r.found and r.path == exe and r.source == "wellknown"


def test_good_python_virtual_env_with_pillow_callback(tmp_path):
    py = _touch(tmp_path / "venv" / "bin" / "python", executable=True)
    env = {"HOME": str(tmp_path), "PATH": "", "VIRTUAL_ENV": str(tmp_path / "venv")}
    r = discover.resolve_python(environ=env, system="linux", home=tmp_path, require_pillow=True,
                                pillow_ok=lambda p: p == py)
    assert r.found and r.path == py and r.source == "env:VIRTUAL_ENV"


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def test_good_asset_roots_use_portable_workspace_defaults(tmp_path):
    home = tmp_path / "home"
    env = {"HOME": str(home)}
    roots = {r.name: r for r in discover.asset_search_roots(environ=env, system="linux", home=home)}
    ws = home / ".local" / "share" / "video-engine"
    assert roots["geo-library"].path == ws / "geo-library"
    assert roots["open-assets"].path == ws / "open-assets"
    assert roots["stock"].path == ws / "asset-cache"
    assert not roots["geo-library"].found
    # no machine-specific default
    assert all("/workspace/" not in str(r.path) for r in roots.values() if r.path)


def test_good_asset_roots_env_and_list_overrides(tmp_path):
    home = tmp_path / "home"
    geo, stock_a, stock_b = tmp_path / "geo", tmp_path / "sa", tmp_path / "sb"
    geo.mkdir(); stock_a.mkdir()
    env = {
        "HOME": str(home),
        "VIDEO_ENGINE_GEO_LIBRARY": str(geo),
        "VIDEO_ENGINE_STOCK_DIRS": f"{stock_a}:{stock_b}",
        "VIDEO_ENGINE_ASSET_ROOTS": str(tmp_path / "extra"),
    }
    roots = discover.asset_search_roots(environ=env, system="linux", home=home, project=tmp_path / "proj")
    by = {r.name: r for r in roots}
    assert by["project"].path == tmp_path / "proj"
    assert by["geo-library"].found and by["geo-library"].path == geo
    assert by["geo-library"].source == "env:VIDEO_ENGINE_GEO_LIBRARY"
    assert by["stock_0"].found and by["stock_0"].path == stock_a
    assert by["stock_1"].path == stock_b and not by["stock_1"].found
    assert by["asset_root_0"].path == tmp_path / "extra"


def test_good_asset_stock_dirs_windows_pathsep(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    env = {"USERPROFILE": str(tmp_path), "LOCALAPPDATA": str(tmp_path / "Local"),
           "VIDEO_ENGINE_STOCK_DIRS": f"{a};{b}"}
    roots = [r for r in discover.asset_search_roots(environ=env, system="win32", home=tmp_path) if r.name.startswith("stock")]
    assert [r.path for r in roots] == [a, b]


# ---------------------------------------------------------------------------
# which() on a mocked Windows PATH
# ---------------------------------------------------------------------------

def test_good_which_respects_pathext_on_mocked_win32(tmp_path):
    d = tmp_path / "bin"
    exe = _touch(d / "ffmpeg.exe")
    env = {"PATH": str(d), "PATHEXT": ".COM;.EXE;.BAT", "USERPROFILE": str(tmp_path)}
    assert discover.which("ffmpeg", env, "win32") == exe
    assert discover.which("nope", env, "win32") is None


def test_good_resolve_all_reports_tools_and_assets(tmp_path):
    vendor = tmp_path / "vendor"
    _touch(vendor / "node" / "bin" / "node", executable=True)
    _pkg(vendor / "npm" / "node_modules", "hyperframes")
    env = {"HOME": str(tmp_path), "PATH": "", "NODE_PATH": ""}
    d = discover.resolve_all(vendor=vendor, environ=env, system="linux", home=tmp_path, confine=tmp_path)
    by = d.by_name()
    assert by["node"].found and by["node"].source == "vendor"
    assert by["hyperframes"].found and by["hyperframes"].source == "vendor"
    assert not by["three"].found
    assert "geo-library" in by and by["geo-library"].kind == "asset"
