"""Public import compatibility, relocated resources, and native-free information paths."""
from __future__ import annotations

import contextlib
import importlib
import importlib.resources
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
ALIASES = (
    ("config", "core.config"),
    ("util", "core.util"),
    ("hyperframes", "render.hyperframes"),
    ("native", "render.native"),
    ("hf_probe", "render.hf_probe"),
    ("mixer", "sound.mixer"),
    ("modal_closure", "cloud.closure"),
)


def _python(tmp_path: Path, script: str, **overrides: str) -> subprocess.CompletedProcess:
    """Run outside the checkout with isolated engine configuration and an explicit import root."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIDEO_ENGINE_")}
    env.update(
        PYTHONPATH=str(ROOT),
        VIDEO_ENGINE_ENV_FILE=str(tmp_path / "absent.env"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
    )
    env.update(overrides)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize(("legacy", "canonical"), ALIASES)
@pytest.mark.parametrize("canonical_first", [False, True])
def test_legacy_and_canonical_imports_share_state(tmp_path, legacy, canonical, canonical_first):
    names = [f"video_engine.{legacy}", f"video_engine.{canonical}"]
    if canonical_first:
        names.reverse()
    _python(tmp_path, f"""
        import importlib
        import sys
        names = {names!r}
        first, second = map(importlib.import_module, names)
        assert first is second, names
        for name in names:
            assert sys.modules[name] is first
            parent, attr = name.rsplit(".", 1)
            assert getattr(importlib.import_module(parent), attr) is first
        marker = object()
        first._migration_patch = marker
        assert second._migration_patch is marker
        del second._migration_patch
        assert not hasattr(first, "_migration_patch")
    """)


@pytest.mark.parametrize("patched_name", ["video_engine.hyperframes", "video_engine.render.hyperframes"])
def test_hyperframes_run_patch_reaches_probe_adapter(monkeypatch, tmp_path, patched_name):
    hf = importlib.import_module(patched_name)
    probe = importlib.import_module("video_engine.render.hf_probe")
    calls = []
    expected = {"times": [{"t": 0.25, "props": []}]}

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps(expected)

    monkeypatch.setattr(hf, "run", fake_run)
    monkeypatch.setattr(probe, "serve", lambda directory: contextlib.nullcontext("http://test.invalid"))
    assert probe.probe_props(tmp_path, [0.25], 1080, 1920) == expected
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert Path(args[1]).name == "props_probe.mjs"
    assert args[2:] == ["http://test.invalid/index.html", "0.250000", 1080, 1920]
    assert kwargs["timeout"] == 120


@pytest.mark.parametrize("relative", [
    "template/index.html",
    "template/styles.css",
    "template/printstyle.js",
    "hf/dom_probe.mjs",
    "hf/props_probe.mjs",
    "hf/chrome_repeatable.py",
    "hf/ffmpeg_repeatable.py",
])
def test_render_package_resources_are_readable(relative):
    resource = importlib.resources.files("video_engine.render").joinpath(relative)
    assert resource.is_file(), relative
    assert resource.read_bytes(), relative


def test_config_roots_and_wrapper_paths_survive_relocation(tmp_path):
    result = _python(tmp_path, """
        import json
        from video_engine import config
        env = config.hf_env()
        print(json.dumps({
            "root": str(config.ENGINE_ROOT),
            "pkg": str(config.PKG),
            "vendor": str(config.VENDOR),
            "profiles": str(config.PROFILES_DIR),
            "template": str(config.TEMPLATE_DIR),
            "chrome": env["HYPERFRAMES_BROWSER_PATH"],
            "producer": env["PRODUCER_HEADLESS_SHELL_PATH"],
            "ffmpeg": env["HYPERFRAMES_FFMPEG_PATH"],
        }))
    """)
    paths = {key: Path(value) for key, value in json.loads(result.stdout).items()}
    assert paths["root"] == ROOT
    assert paths["pkg"] == ROOT / "video_engine"
    assert paths["vendor"] == ROOT / "vendor"
    assert paths["profiles"] == ROOT / "profiles"
    assert paths["template"] == ROOT / "video_engine/render/template"
    assert (paths["vendor"] / "VERSIONS.json").is_file()
    assert (paths["profiles"] / "default.toml").is_file()
    assert (paths["template"] / "index.html").is_file()
    assert paths["chrome"] == paths["producer"]
    for name in ("chrome", "ffmpeg"):
        assert paths[name].parent == ROOT / "video_engine/render/hf"
        assert paths[name].is_file()


_BLOCK_NATIVE = """
import importlib.abc
import sys

class BlockNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "hypoverum_core_rs" or fullname.startswith("hypoverum_core_rs."):
            raise AssertionError("information-only path attempted to import Rust: " + fullname)

assert "hypoverum_core_rs" not in sys.modules
sys.meta_path.insert(0, BlockNative())
"""


@pytest.mark.parametrize("operation", [
    "from video_engine.timeline import Timeline; assert Timeline.model_json_schema()['properties']",
    "from video_engine.profile_schema import ProfileData; assert ProfileData.model_json_schema()['properties']",
])
def test_contract_schema_works_without_native_extension(tmp_path, operation):
    _python(tmp_path, _BLOCK_NATIVE + "\n" + operation + '\nassert "hypoverum_core_rs" not in sys.modules\n')


def test_cli_help_works_without_native_extension(tmp_path):
    result = _python(tmp_path, _BLOCK_NATIVE + """
from video_engine.cli import main
try:
    status = main(["--help"])
except SystemExit as exc:
    status = exc.code
assert status in (None, 0), status
assert "hypoverum_core_rs" not in sys.modules
""")
    assert "usage: engine" in result.stdout
    assert "build" in result.stdout
    assert "preflight" in result.stdout
