#!/usr/bin/env python3
"""Install the Linux image toolchain: FFmpeg 8.1 static, Node, npm packages, Chrome.

Versions come from the environment (Docker build args). When a version is missing or
does not parse, vendor/VERSIONS.json is used. FFmpeg is always the pinned static GPL
build in vendor/ffmpeg-static.json (libx264, major 8). The script records what it
installed in /opt/video-engine/toolchain.json.

Run from the image build, with the engine tree at /src. It uses only the standard library.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path("/src")
VENDOR = ROOT / "vendor"
PINS = json.loads((VENDOR / "VERSIONS.json").read_text(encoding="utf-8"))
FFMPEG_PIN = json.loads((VENDOR / "ffmpeg-static.json").read_text(encoding="utf-8"))
RECORD = Path("/opt/video-engine/toolchain.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract_top(archive: Path, dest: Path) -> None:
    stage = dest.with_name(dest.name + ".stage")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(stage, filter="tar")
    tops = [path for path in stage.iterdir() if not path.name.startswith("._")]
    if len(tops) != 1 or not tops[0].is_dir():
        raise SystemExit(f"{archive.name} did not contain a single top-level directory")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(tops[0]), dest)
    shutil.rmtree(stage, ignore_errors=True)


def install_ffmpeg() -> dict:
    archive = Path("/tmp/ffmpeg-static.tar.xz")
    _download(FFMPEG_PIN["url"], archive)
    got = _sha256(archive)
    if got != FFMPEG_PIN["sha256"]:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"ffmpeg sha256 mismatch: want {FFMPEG_PIN['sha256']}, got {got}")
    stage = Path("/tmp/ffmpeg-static")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(stage, filter="tar")
    bins = list(stage.rglob("bin"))
    if not bins:
        raise SystemExit("ffmpeg archive has no bin directory")
    dest = Path("/opt/ffmpeg")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    shutil.move(str(bins[0]), dest / "bin")
    archive.unlink(missing_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    return {"version": FFMPEG_PIN.get("version"), "build": FFMPEG_PIN.get("build"),
            "sha256": FFMPEG_PIN["sha256"], "major": FFMPEG_PIN.get("major")}


def _node_pin(version: str) -> tuple[str, str]:
    pinned = PINS["node"]
    if version == pinned["version"]:
        plat = pinned["platforms"]["linux-x64"]
        return plat["url"], plat["sha256"]
    name = f"node-{version}-linux-x64.tar.xz"
    url = f"https://nodejs.org/dist/{version}/{name}"
    with urllib.request.urlopen(f"https://nodejs.org/dist/{version}/SHASUMS256.txt", timeout=120) as response:
        sums = response.read().decode()
    sha = next((line.split()[0] for line in sums.splitlines() if line.endswith(name)), "")
    if not sha:
        raise SystemExit(f"no SHA-256 for {name}")
    return url, sha


def install_node(version: str) -> str:
    url, sha = _node_pin(version)
    archive = Path("/tmp") / Path(url).name
    _download(url, archive)
    got = _sha256(archive)
    if got != sha:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"node sha256 mismatch: want {sha}, got {got}")
    _extract_top(archive, VENDOR / "node")
    archive.unlink(missing_ok=True)
    (VENDOR / "node" / ".version").write_text(version + "\n", encoding="utf-8")
    return version


def install_npm(hf: str, puppeteer: str, three: str) -> dict:
    npm_dir = VENDOR / "npm"
    npm_dir.mkdir(parents=True, exist_ok=True)
    want = {"hyperframes": hf, "puppeteer-core": puppeteer, "three": three}
    pinned = PINS["npm"]
    lock = npm_dir / "package-lock.json"
    npm = VENDOR / "node" / "bin" / "npm"
    env = dict(os.environ)
    env["PATH"] = f"{VENDOR / 'node' / 'bin'}:{env.get('PATH', '')}"
    env["npm_config_update_notifier"] = "false"
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    if want == pinned and lock.is_file():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=npm_dir, env=env, check=True)
        how = "npm-ci"
    else:
        lock.unlink(missing_ok=True)
        (npm_dir / "package.json").write_text(json.dumps({
            "name": "video-engine-vendor", "private": True, "dependencies": want,
        }, indent=2) + "\n", encoding="utf-8")
        subprocess.run([npm, "install", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=npm_dir, env=env, check=True)
        how = "npm-install"
    return {"versions": want, "installer": how}


def install_chrome(version: str) -> str:
    node = VENDOR / "node" / "bin" / "node"
    pkg = VENDOR / "npm" / "node_modules" / "@puppeteer" / "browsers"
    try:
        binary = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["bin"]
        cli = pkg / (binary if isinstance(binary, str) else next(iter(binary.values())))
    except (OSError, ValueError, KeyError, StopIteration):
        cli = pkg / "lib" / "cjs" / "main-cli.js"
    if not cli.is_file():
        raise SystemExit(f"@puppeteer/browsers CLI missing at {cli}")
    dest = VENDOR / "chrome"
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{VENDOR / 'node' / 'bin'}:{env.get('PATH', '')}"
    subprocess.run([node, cli, "install", f"chrome-headless-shell@{version}", "--path", dest], env=env, check=True)
    return version


def main() -> None:
    node = os.environ.get("NODE_VERSION") or PINS["node"]["version"]
    hf = os.environ.get("HF_VERSION") or PINS["npm"]["hyperframes"]
    puppeteer = os.environ.get("PUPPETEER_VERSION") or PINS["npm"]["puppeteer-core"]
    three = os.environ.get("THREE_VERSION") or PINS["npm"]["three"]
    chrome = os.environ.get("CHROME_VERSION") or PINS["chrome"]["version"]
    record = {
        "ffmpeg": install_ffmpeg(),
        "node": install_node(node),
        "npm": install_npm(hf, puppeteer, three),
        "chrome": install_chrome(chrome),
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
