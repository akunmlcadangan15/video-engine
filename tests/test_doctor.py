"""Pinned toolchain in vendor/: doctor checks, SHA-256 verified node install, and no dependency on the retired kit."""
import json
import re
from pathlib import Path

import pytest

from video_engine import config, doctor
from video_engine.util import EngineError

ROOT = Path(__file__).resolve().parent.parent
PINS = json.loads((ROOT / "vendor" / "VERSIONS.json").read_text())


def _fake_vendor(tmp_path: Path, *, node=True, hf_version=None, hf_dist=True, pptr=True, chrome=True,
                 three=True) -> Path:
    v = tmp_path / "vendor"
    (v / "npm").mkdir(parents=True)
    (v / "VERSIONS.json").write_text(json.dumps(PINS))
    x = doctor.paths(v, PINS)
    if node:
        x["node"].parent.mkdir(parents=True)
        x["node"].write_text("")
        (v / "node" / ".version").write_text(PINS["node"]["version"])
    hf = v / "npm" / "node_modules" / "hyperframes"
    hf.mkdir(parents=True)
    (hf / "package.json").write_text(json.dumps({"version": hf_version or PINS["npm"]["hyperframes"]}))
    if hf_dist:
        (hf / "dist").mkdir()
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
    res = doctor.check(_fake_vendor(tmp_path), run_binaries=False)
    assert all(c.ok for c in res), [c.line() for c in res]


@pytest.mark.parametrize("kw,bad", [
    ({"node": False}, "node"),
    ({"hf_version": "0.8.100"}, "hyperframes"),
    ({"hf_dist": False}, "hyperframes"),
    ({"pptr": False}, "puppeteer-core"),
    ({"chrome": False}, "chrome"),
    ({"three": False}, "three"),
])
def test_bad_missing_or_mismatched_component_fails(tmp_path, kw, bad):
    res = {c.name: c for c in doctor.check(_fake_vendor(tmp_path, **kw), run_binaries=False)}
    assert not res[bad].ok
    assert all(c.ok for n, c in res.items() if n != bad)


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
    assert re.fullmatch(r"[0-9a-f]{64}", PINS["node"]["sha256"])


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
