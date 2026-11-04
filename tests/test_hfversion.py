"""Selectable HyperFrames version (hfversion.py): parsing, precedence env > vendor/hyperframes.select > pin,
resolution of pinned / X.Y.Z / latest / system, doctor's hyperframes-active check, the cache key and build report."""
import json
from pathlib import Path

import pytest

from video_engine import config, doctor, hfversion, manifest, toolchain
from video_engine.util import EngineError

ENV = hfversion.ENV


def _pkg(root: Path, version: str, cli=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"version": version}))
    if cli:
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "hyperframes.mjs").write_text("")


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    """Fake vendor/: pin 0.9.13 installed in npm/, 0.8.214 and 0.10.0-rc.1 in hf/."""
    monkeypatch.delenv(ENV, raising=False)
    v = tmp_path / "vendor"
    v.mkdir()
    (v / "VERSIONS.json").write_text(json.dumps({"npm": {"hyperframes": "0.9.13"}}))
    _pkg(hfversion.pinned_root(v), "0.9.13")
    _pkg(hfversion.vendor_root("0.8.214", v), "0.8.214")
    _pkg(hfversion.vendor_root("0.10.0-rc.1", v), "0.10.0-rc.1")
    return v


@pytest.mark.parametrize("text,want", [(None, "pinned"), ("", "pinned"), ("  ", "pinned"), ("pinned", "pinned"),
                                       ("LATEST", "latest"), ("System", "system"), ("0.9.13", "0.9.13"),
                                       ("v0.8.214", "0.8.214"), (" 1.2.3-beta.4 ", "1.2.3-beta.4")])
def test_good_parse_spec(text, want):
    assert hfversion.parse_spec(text) == want


@pytest.mark.parametrize("text", ["0.9", "^0.9.13", "~0.9.13", "0.9.x", "newest", "0.9.13 0.9.12", "../0.9.13"])
def test_bad_parse_spec_rejects_ranges_and_words(text):
    with pytest.raises(EngineError, match="exact version"):
        hfversion.parse_spec(text)


def test_good_version_key_orders_numerically_and_pre_releases_first():
    vs = ["0.10.0", "0.9.13", "0.8.214", "0.10.0-rc.1", "0.9.2"]
    assert sorted(vs, key=hfversion.version_key) == ["0.8.214", "0.9.2", "0.9.13", "0.10.0-rc.1", "0.10.0"]


def test_good_default_is_the_pin(vendor):
    a = hfversion.resolve(vendor=vendor, environ={})
    assert (a.spec, a.setting_from, a.version, a.source, a.installed) == ("pinned", "default", "0.9.13", "pinned", True)
    assert a.cli == vendor / "npm/node_modules/hyperframes/bin/hyperframes.mjs"


def test_good_env_wins_over_the_select_file(vendor):
    (vendor / hfversion.SELECT_FILE).write_text("0.8.214\n")
    assert hfversion.resolve(vendor=vendor, environ={}).version == "0.8.214"
    a = hfversion.resolve(vendor=vendor, environ={ENV: "pinned"})
    assert (a.setting_from, a.version) == ("env", "0.9.13")


def test_good_exact_version_resolves_to_vendor_hf_and_the_pin_to_vendor_npm(vendor):
    a = hfversion.resolve("0.8.214", vendor=vendor)
    assert (a.source, a.version, a.root) == ("vendor", "0.8.214", hfversion.vendor_root("0.8.214", vendor))
    assert hfversion.resolve("0.9.13", vendor=vendor).source == "pinned"


def test_good_latest_is_the_newest_installed(vendor):
    assert hfversion.resolve("latest", vendor=vendor).version == "0.10.0-rc.1"
    _pkg(hfversion.vendor_root("0.10.0", vendor), "0.10.0")
    assert hfversion.installed_versions(vendor) == ["0.8.214", "0.10.0-rc.1", "0.10.0"]
    assert hfversion.resolve("latest", vendor=vendor).version == "0.10.0"


def test_good_system_uses_the_cli_on_path(vendor, tmp_path, monkeypatch):
    root = tmp_path / "global" / "node_modules" / "hyperframes"
    _pkg(root, "0.9.99")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "hyperframes").symlink_to(root / "bin" / "hyperframes.mjs")
    (root / "bin" / "hyperframes.mjs").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    a = hfversion.resolve("system", vendor=vendor)
    assert (a.source, a.version, a.root.resolve(), a.installed) == ("system", "0.9.99", root.resolve(), True)


def test_bad_not_installed_version_fails_require_and_doctor(vendor, monkeypatch):
    monkeypatch.setattr(config, "VENDOR", vendor)
    monkeypatch.setenv(ENV, "0.7.1")
    a = hfversion.resolve(vendor=vendor)
    assert not a.installed and a.version == "missing"
    with pytest.raises(EngineError, match="engine toolchain use 0.7.1"):
        hfversion.require()
    c = doctor.check_active(vendor)
    assert not c.ok and c.name == "hyperframes-active"


def test_bad_invalid_setting_fails_doctor_check(vendor, monkeypatch):
    monkeypatch.setenv(ENV, "0.9")
    assert not doctor.check_active(vendor).ok


def test_good_doctor_warns_for_a_non_pinned_active_version(vendor, monkeypatch):
    assert doctor.check_active(vendor).ok and not doctor.check_active(vendor).warn
    monkeypatch.setenv(ENV, "0.8.214")
    c = doctor.check_active(vendor)
    assert c.ok and c.warn and c.line().startswith("[WARN] hyperframes-active") and "0.8.214" in c.got


def test_good_switching_version_changes_the_toolchain_identity(vendor, monkeypatch):
    monkeypatch.setattr(config, "VENDOR", vendor)
    monkeypatch.setattr(toolchain, "ffmpeg_version", lambda exe: "ffmpeg version 7.1")
    ids = {}
    for spec in ("pinned", "0.8.214", "latest"):
        monkeypatch.setenv(ENV, spec)
        ids[spec] = toolchain.hf_identity()
    assert len(set(ids.values())) == 3
    monkeypatch.setenv(ENV, "0.9.13")                              # the pin by number: same toolchain as "pinned"
    assert toolchain.hf_identity() == ids["pinned"]


def test_good_build_report_records_the_version_used(vendor, monkeypatch):
    (vendor / "VERSIONS.json").write_text(json.dumps({"node": {"version": "v22"}, "chrome": {"product": "c", "version": "1"},
                                                     "npm": {"hyperframes": "0.9.13", "puppeteer-core": "25"}}))
    monkeypatch.setattr(config, "VENDOR", vendor)
    monkeypatch.setenv(ENV, "0.8.214")
    t = manifest.toolchain()
    assert (t["hyperframes"], t["hyperframes_source"], t["hyperframes_setting"], t["hyperframes_pinned"]) == \
        ("0.8.214", "vendor", "0.8.214", "0.9.13")


def test_good_real_vendor_pin_is_0_9_13_and_render_uses_the_active_cli():
    assert hfversion.pinned_version() == "0.9.13"
    src = (Path(config.PKG) / "hyperframes.py").read_text()
    assert "hfversion.require().cli" in src and "config.HF_CLI" not in src
