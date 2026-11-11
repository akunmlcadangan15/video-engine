"""Pinned toolchain in vendor/: doctor checks, SHA-256 verified node install, and no dependency on the retired kit."""
import json
import re
from pathlib import Path

import pytest

from video_engine import config, doctor, platform_info
from video_engine.util import EngineError

ROOT = Path(__file__).resolve().parent.parent
PINS = json.loads((ROOT / "vendor" / "VERSIONS.json").read_text())


def _fake_vendor(tmp_path: Path, *, node=True, hf_version=None, hf_dist=True, hf_cli=True, pptr=True, chrome=True,
                 three=True) -> Path:
    v = tmp_path / "vendor"
    (v / "npm").mkdir(parents=True)
    (v / "VERSIONS.json").write_text(json.dumps(PINS))
    x = doctor.paths(v, PINS)
    if node:
        x["node"].parent.mkdir(parents=True)
        x["node"].write_text(f"#!/bin/sh\necho {PINS['node']['version']}\n")
        x["node"].chmod(0o755)
        npm = x["node"].parent / "npm"
        npm.write_text("#!/bin/sh\n")
        npm.chmod(0o755)
        (v / "node" / ".version").write_text(PINS["node"]["version"])
    hf = v / "npm" / "node_modules" / "hyperframes"
    hf.mkdir(parents=True)
    (hf / "package.json").write_text(json.dumps({"version": hf_version or PINS["npm"]["hyperframes"]}))
    if hf_dist:
        (hf / "dist").mkdir()
    if hf_cli:
        x["hf_cli"].parent.mkdir(parents=True)
        x["hf_cli"].write_text("")
    if pptr:
        x["puppeteer"].mkdir(parents=True)
        (x["puppeteer"] / "package.json").write_text(json.dumps({"version": PINS["npm"]["puppeteer-core"]}))
    if three:
        t = v / "npm" / "node_modules" / "three"
        t.mkdir(parents=True)
        (t / "package.json").write_text(json.dumps({"version": PINS["npm"]["three"]}))
    if chrome:
        x["chrome"].parent.mkdir(parents=True)
        x["chrome"].write_text("")
        x["chrome"].chmod(0o755)
    return v


def test_good_complete_vendor_passes(tmp_path):
    res = doctor.check(_fake_vendor(tmp_path), run_binaries=False, machine=False)
    assert all(c.ok for c in res), [c.line() for c in res]


@pytest.mark.parametrize("kw,bad", [
    ({"node": False}, "node"),
    ({"hf_dist": False}, "hyperframes"),
    ({"hf_cli": False}, ("hyperframes-cli", "hyperframes-active")),   # the active (pinned) CLI is missing too
    ({"pptr": False}, "puppeteer-core"),
    ({"chrome": False}, "chrome"),
    ({"three": False}, "three"),
])
def test_bad_missing_or_mismatched_component_fails(tmp_path, kw, bad):
    bad = (bad,) if isinstance(bad, str) else bad
    res = {c.name: c for c in doctor.check(_fake_vendor(tmp_path, **kw), run_binaries=False, machine=False)}
    assert all(not res[b].ok for b in bad)
    assert all(c.ok for n, c in res.items() if n not in bad)


def test_bad_node_without_npm_fails(tmp_path):
    """A node tree whose bin/npm is missing (broken symlink or stripped lib/) fails the node check."""
    v = _fake_vendor(tmp_path)
    (v / "node" / "bin" / "npm").unlink()
    res = {c.name: c for c in doctor.check(v, run_binaries=False, machine=False)}
    assert not res["node"].ok and "npm missing" in res["node"].got
    assert all(c.ok for n, c in res.items() if n != "node")


def test_good_hyperframes_other_than_the_pin_warns_and_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE_HYPERFRAMES_VERSION", raising=False)
    res = {c.name: c for c in doctor.check(_fake_vendor(tmp_path, hf_version="0.8.100"), run_binaries=False, machine=False)}
    assert all(c.ok for c in res.values()), [c.line() for c in res.values()]
    assert res["hyperframes"].warn and res["hyperframes"].line().startswith("[WARN]")
    assert res["hyperframes-active"].warn and "0.8.100" in res["hyperframes-active"].got


def test_good_ffmpeg_with_required_filters_and_encoders_passes(monkeypatch):
    names = {"-filters": {"subtitles", "loudnorm", "sidechaincompress", "scale"}, "-encoders": {"libx264", "aac"}}
    monkeypatch.setattr(doctor, "_ffmpeg_names", names.__getitem__)
    assert doctor.check_ffmpeg().ok


@pytest.mark.parametrize("filters,encoders,gone", [
    ({"subtitles", "loudnorm"}, {"libx264", "aac"}, "sidechaincompress"),
    ({"subtitles", "loudnorm", "sidechaincompress"}, {"aac"}, "libx264"),
    (set(), set(), "subtitles"),                                  # ffmpeg missing or unrunnable: nothing listed
])
def test_bad_ffmpeg_missing_filter_or_encoder_fails(monkeypatch, filters, encoders, gone):
    monkeypatch.setattr(doctor, "_ffmpeg_names", {"-filters": filters, "-encoders": encoders}.__getitem__)
    c = doctor.check_ffmpeg()
    assert not c.ok and gone in c.got


FFMPEG_VERSION_LINES = [
    ("ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers", 7),
    ("ffmpeg version 7.1-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024", 7),
    ("ffmpeg version n7.0.2-5-gabcdef Copyright (c) 2000-2024 the FFmpeg developers", 7),
    ("ffmpeg version 7.0.git Copyright", 7),
    ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers", 6),
    ("ffmpeg version 8.0 Copyright", 8),
    ("ffmpeg version N-117021-g1234abc Copyright (c) 2000-2024", None),   # git master: no release number
    ("", None),
    ("command not found", None),
]


@pytest.mark.parametrize("text,major", FFMPEG_VERSION_LINES)
def test_good_parse_ffmpeg_major(text, major):
    assert doctor.parse_ffmpeg_major(text) == major


def test_good_ffmpeg_versions_at_least_7_pass():
    for version in ("7.1.1", "7.1.5", "8.0", "9.0.2", "10.0.0", "11.0.0"):
        assert doctor.check_ffmpeg_version(f"ffmpeg version {version} Copyright (c) 2000-2026").ok, version

def test_bad_ffmpeg_versions_below_min_or_unparseable_fail():
    for text in ("ffmpeg version 6.1.1-3ubuntu5 Copyright", "ffmpeg version 5.1 Copyright", "ffmpeg version N-1-gabc Copyright", ""):
        c = doctor.check_ffmpeg_version(text)
        assert not c.ok and c.name == "ffmpeg-version", c.line()
    assert doctor.check_ffmpeg_version("ffmpeg version 6.1.1-3ubuntu5 Copyright").got == "6.1.1-3ubuntu5"


def test_good_check_runs_ffmpeg_version_as_its_own_check(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_ffmpeg_names", lambda flag: {"subtitles", "loudnorm", "sidechaincompress", "libx264", "aac"})
    monkeypatch.setattr(doctor, "check_ffmpeg_version", lambda: doctor.Check("ffmpeg-version", False, "7.x", "6.1"))
    res = {c.name: c for c in doctor.check(_fake_vendor(tmp_path), machine=False)}
    assert res["ffmpeg"].ok and not res["ffmpeg-version"].ok
    assert [n for n, c in res.items() if not c.ok] == ["ffmpeg-version"]


def test_good_check_reports_platform_first():
    c = doctor.check_platform()
    assert c.name == "platform" and c.ok and c.got in platform_info.SUPPORTED


def test_bad_unsupported_platform_fails_check_and_blocks_install(tmp_path, monkeypatch):
    v = _fake_vendor(tmp_path)
    monkeypatch.setattr(platform_info.sys, "platform", "freebsd14")
    res = doctor.check(v, run_binaries=False, machine=False)
    assert [c.name for c in res] == ["platform"] and not res[0].ok and "unsupported platform" in res[0].got
    with pytest.raises(EngineError, match="unsupported platform"):
        doctor.install(v, tmp_path / "tmp")


@pytest.mark.parametrize("key,top", [("linux-x64", "linux-x64"), ("darwin-arm64", "darwin-arm64")])
def test_good_node_pin_selected_per_platform(key, top):
    pin = doctor.node_pin(PINS, key)
    assert pin["version"] == PINS["node"]["version"]
    assert pin["url"].endswith(f"node-{pin['version']}-{top}.tar.xz")
    assert pin["sha256"] == PINS["node"]["platforms"][key]["sha256"]


def test_bad_node_pin_missing_platform_is_a_clear_error():
    with pytest.raises(EngineError, match="no node pin for platform darwin-arm64"):
        doctor.node_pin({"node": {"version": "v1", "platforms": {}}}, "darwin-arm64")


def test_good_paths_chrome_layout_per_platform(tmp_path):
    c = PINS["chrome"]
    lin = doctor.paths(tmp_path, PINS, "linux-x64")["chrome"]
    mac = doctor.paths(tmp_path, PINS, "darwin-arm64")["chrome"]
    assert lin == tmp_path / "chrome" / c["product"] / f"linux-{c['version']}" / f"{c['product']}-linux64" / c["product"]
    assert mac == tmp_path / "chrome" / c["product"] / f"mac_arm-{c['version']}" / f"{c['product']}-mac-arm64" / c["product"]


def test_good_install_node_extracts_darwin_layout(tmp_path, monkeypatch):
    """The top-level dir of the tarball carries the platform name; extraction must not depend on it."""
    import io
    import hashlib
    import tarfile
    top = f"node-{PINS['node']['version']}-darwin-arm64"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        data = b"#!/bin/sh\n"
        ti = tarfile.TarInfo(f"{top}/bin/node")
        ti.size, ti.mode = len(data), 0o755
        tf.addfile(ti, io.BytesIO(data))
    blob = buf.getvalue()
    pins = json.loads(json.dumps(PINS))
    pins["node"]["platforms"]["darwin-arm64"]["sha256"] = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(platform_info, "platform_key", lambda *a: "darwin-arm64")
    monkeypatch.setattr(doctor.urllib.request, "urlretrieve", lambda url, dst: Path(dst).write_bytes(blob))
    v = tmp_path / "vendor"
    v.mkdir()
    doctor.install_node(v, pins, tmp_path / "tmp")
    assert (v / "node" / "bin" / "node").is_file()
    assert (v / "node" / ".version").read_text().strip() == PINS["node"]["version"]


def test_good_ffmpeg_names_parses_real_listing():
    if not (f := doctor._ffmpeg_names("-filters")):
        pytest.skip("no runnable ffmpeg")
    assert "scale" in f and "Filters:" not in f


def test_bad_node_tarball_sha_mismatch_rejected(tmp_path, monkeypatch):
    v = _fake_vendor(tmp_path, node=False)
    monkeypatch.setattr(doctor.urllib.request, "urlretrieve", lambda url, dst: Path(dst).write_bytes(b"tampered"))
    with pytest.raises(EngineError, match="sha256 mismatch"):
        doctor.install_node(v, PINS, tmp_path / "tmp")
    assert not (v / "node").exists()


def test_good_config_paths_come_from_engine_vendor():
    for p in (config.NODE, config.HF_CLI, config.PUPPETEER, config.CHROME):
        assert p.is_relative_to(config.VENDOR), p
    assert PINS["chrome"]["version"] in str(config.CHROME)


def test_good_pins_are_exact_versions():
    pkg = json.loads((ROOT / "vendor" / "npm" / "package.json").read_text())
    assert pkg["dependencies"] == PINS["npm"]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in PINS["npm"].values())
    lock = json.loads((ROOT / "vendor" / "npm" / "package-lock.json").read_text())
    for name, want in PINS["npm"].items():
        assert lock["packages"][f"node_modules/{name}"]["version"] == want
    for key, pin in PINS["node"]["platforms"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]), key
        assert PINS["node"]["version"] in pin["url"] and pin["url"].endswith(f"-{key}.tar.xz"), key


RETIRED = "hypoverum" + "-kit"  # split so this file does not match itself


def _engine_files():
    for d in ["video_engine", "rust/hypoverum_core_rs/src", "vendor/npm", "docs"]:
        for f in (ROOT / d).rglob("*"):
            if f.is_file() and "node_modules" not in f.parts and f.suffix not in {".pyc", ".so"}:
                yield f
    for f in ["pyproject.toml", "rust/hypoverum_core_rs/Cargo.toml", "vendor/VERSIONS.json", ".gitignore", "README.md"]:
        if (ROOT / f).is_file():
            yield ROOT / f


def test_good_no_engine_code_or_config_references_retired_kit():
    hits = [str(f.relative_to(ROOT)) for f in _engine_files()
            if re.search(r"hypoverum[-_]kit|/kit/vendor", f.read_text(errors="ignore"))]
    assert not hits, f"engine must not reference {RETIRED}: {hits}"


def test_bad_scanner_detects_a_reference(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(f'P = "/workspace/{RETIRED}/kit/vendor/node22/bin/node"\n')
    assert re.search(r"hypoverum[-_]kit|/kit/vendor", f.read_text())


def test_good_install_touches_only_failing_components(tmp_path, monkeypatch):
    v = _fake_vendor(tmp_path, node=False, hf_cli=False, chrome=False)
    calls = []
    monkeypatch.setattr(doctor, "install_node", lambda *a: calls.append("node"))
    monkeypatch.setattr(doctor, "install_npm", lambda *a: calls.append("npm"))
    monkeypatch.setattr(doctor, "install_chrome", lambda *a: calls.append("chrome"))
    doctor.install(v, tmp_path / "tmp")
    assert calls == ["node", "npm", "chrome"]


def test_good_install_does_nothing_when_everything_matches(tmp_path, monkeypatch):
    v = _fake_vendor(tmp_path)
    calls = []
    for name in ("install_node", "install_npm", "install_chrome"):
        monkeypatch.setattr(doctor, name, lambda *a, n=name: calls.append(n))
    doctor.install(v, tmp_path / "tmp")
    assert calls == []



def test_good_doctor_reports_machine_path_when_vendor_missing(tmp_path):
    """A node binary outside vendor is reported found (with source), not missing, when machine discovery is on."""
    v = _fake_vendor(tmp_path, node=False)
    bundled = tmp_path / "onpath" / "node"
    bundled.parent.mkdir()
    bundled.write_text("")
    bundled.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": str(bundled.parent), "NODE_PATH": ""}
    res = {c.name: c for c in doctor.check(v, run_binaries=False, machine=True,
                                           environ=env, home=tmp_path, system="linux", confine=tmp_path)}
    assert res["node"].ok and res["node"].warn
    assert res["node"].source == "PATH"
    assert res["node"].path == str(bundled)
    assert "path:" in res["node"].line()
