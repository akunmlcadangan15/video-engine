"""platform_info: platform key, chrome directory layout, default scratch root; config uses them."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from video_engine import config, platform_info
from video_engine.util import EngineError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("system,machine,key", [
    ("linux", "x86_64", "linux-x64"),
    ("linux", "AMD64", "linux-x64"),
    ("darwin", "arm64", "darwin-arm64"),
    ("darwin", "aarch64", "darwin-arm64"),
])
def test_good_platform_key(system, machine, key):
    assert platform_info.platform_key(system, machine) == key


@pytest.mark.parametrize("system,machine", [
    ("linux", "aarch64"), ("darwin", "x86_64"), ("win32", "AMD64"), ("freebsd14", "amd64"), ("linux", ""),
])
def test_bad_unsupported_platform_raises_listing_supported(system, machine):
    with pytest.raises(EngineError) as e:
        platform_info.platform_key(system, machine)
    assert "linux-x64" in str(e.value) and "darwin-arm64" in str(e.value) and system in str(e.value)


def test_good_default_key_follows_this_machine(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform_info._platform, "machine", lambda: "arm64")
    assert platform_info.platform_key() == "darwin-arm64"
    assert platform_info.supported_key() == "darwin-arm64"
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform_info.supported_key() is None


def test_good_chrome_relpath_layouts():
    p = "chrome-headless-shell"
    assert platform_info.chrome_relpath(p, "152.0.1", "linux-x64") == Path("linux-152.0.1/chrome-headless-shell-linux64/chrome-headless-shell")
    assert platform_info.chrome_relpath(p, "152.0.1", "darwin-arm64") == Path("mac_arm-152.0.1/chrome-headless-shell-mac-arm64/chrome-headless-shell")


def test_good_config_chrome_matches_helper():
    key = platform_info.supported_key()
    c = config._CH
    assert config.CHROME == config.VENDOR / "chrome" / c["product"] / platform_info.chrome_relpath(c["product"], c["version"], key)


def test_good_default_shm_root_per_platform(monkeypatch):
    assert platform_info.default_shm_root("linux") == Path("/dev/shm/video_engine")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: "/var/folders/xx/T")
    assert platform_info.default_shm_root("darwin") == Path("/var/folders/xx/T/video_engine")


def _shm(env_extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if k != "VIDEO_ENGINE_SHM"} | env_extra
    return subprocess.run([sys.executable, "-c", "from video_engine import config; print(config.SHM_ROOT)"], cwd=ROOT,
                          env=env, capture_output=True, text=True, check=True).stdout.strip()


def test_good_shm_default_and_env_override():
    assert _shm({}) == str(platform_info.default_shm_root())
    assert _shm({"VIDEO_ENGINE_SHM": "/tmp/custom_shm"}) == "/tmp/custom_shm"


def test_good_config_imports_on_unsupported_platform():
    code = ("import sys, platform; sys.platform = 'freebsd14'; platform.machine = lambda: 'amd64'\n"
            "from video_engine import config\nprint(config.CHROME)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-500:]
    assert "unsupported-platform" in out.stdout
