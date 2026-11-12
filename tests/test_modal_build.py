"""Closure, chunked fetch, and the engine-build-modal CLI. No Modal calls."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from video_engine.modal_cli import (
    Args,
    DiskLow,
    LockBusy,
    RenderLock,
    UsageError,
    apply_runtime_env,
    assert_free,
    compare_components,
    format_dry_run,
    image_build_args,
    major_token,
    parse_args,
)
from video_engine.modal_closure import file_sha256, link_allowed, materialize, resolve_closure
from video_engine.modal_transfer import (
    TransferError,
    annotate_reports,
    assemble_file,
    download_manifest,
    publish_outputs,
    upload_plan,
    verified_prefix,
    write_chunks,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "engine-build-modal"


def _env(**extra: str) -> dict[str, str]:
    base = {
        "VIDEO_ENGINE_WORKSPACE": extra.get("VIDEO_ENGINE_WORKSPACE", "/unset-ws"),
        "VIDEO_ENGINE_GEO_LIBRARY": extra.get("VIDEO_ENGINE_GEO_LIBRARY", "/unset-geo"),
        "VIDEO_ENGINE_OPEN_ASSETS": extra.get("VIDEO_ENGINE_OPEN_ASSETS", "/unset-open"),
        "VIDEO_ENGINE_STOCK_DIRS": extra.get("VIDEO_ENGINE_STOCK_DIRS", "/unset-stock"),
        "VIDEO_ENGINE_DOWNLOAD_DIR": extra.get("VIDEO_ENGINE_DOWNLOAD_DIR", "/unset-dl"),
        "VIDEO_ENGINE_CACHE": extra.get("VIDEO_ENGINE_CACHE", "/unset-cache"),
        "VIDEO_ENGINE_PROFILES": extra.get("VIDEO_ENGINE_PROFILES", "/unset-profiles"),
        "VIDEO_ENGINE_VENDOR": extra.get("VIDEO_ENGINE_VENDOR", "/unset-vendor"),
    }
    base.update(extra)
    return base


def _project(tmp: Path, doc: dict, profiles: Path | None = None) -> Path:
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "master_timeline.json").write_text(json.dumps(doc), encoding="utf-8")
    if profiles is not None:
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "default.toml").write_text("description = \"test\"\n", encoding="utf-8")
    return proj


def _closure(proj: Path, env: dict, engine: Path):
    return resolve_closure(proj, environ=env, engine_root=engine, profiles_dir=Path(env["VIDEO_ENGINE_PROFILES"]))


def test_symlink_is_dereferenced_and_unreferenced_sibling_is_omitted(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"clip-bytes")
    (library / "other.mp4").write_bytes(b"not-referenced")
    proj = _project(tmp_path, {"profile": "default", "segments": [{"source_video": "assets/clip.mp4"}]}, profiles)
    (proj / "assets").mkdir()
    (proj / "assets" / "clip.mp4").symlink_to(library / "clip.mp4")
    before = (proj / "master_timeline.json").read_bytes()
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles), VIDEO_ENGINE_WORKSPACE=str(tmp_path / "ws"))
    closure = _closure(proj, env, engine)
    assert closure.ok, closure.missing
    assert before == (proj / "master_timeline.json").read_bytes()
    shipped = [item for item in closure.files if item.rel == "assets/clip.mp4"]
    assert len(shipped) == 1
    item = shipped[0]
    assert item.symlink and item.ship and item.root == "project"
    assert item.sha256 == hashlib.sha256(b"clip-bytes").hexdigest()
    assert item.local == str((library / "clip.mp4").resolve())
    assert all("other.mp4" not in f.rel for f in closure.files)


def test_asset_roots_open_stock_and_extra(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    open_root = tmp_path / "open"
    (open_root / "maps").mkdir(parents=True)
    (open_root / "maps" / "a.geojson").write_text("{}", encoding="utf-8")
    (open_root / "catalog.csv").write_text("path\nmaps/a.geojson\n", encoding="utf-8")
    stock_a = tmp_path / "stock-a"
    stock_b = tmp_path / "stock-b"
    (stock_b / "footage").mkdir(parents=True)
    (stock_b / "footage" / "b.mp4").write_bytes(b"bbbb")
    (stock_b / "index.csv").write_text("file\nfootage/b.mp4\n", encoding="utf-8")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "notes.txt").write_text("n", encoding="utf-8")
    proj = _project(tmp_path, {
        "profile": "default",
        "open_ref": "open:maps/a.geojson",
        "stock_ref": "download:footage/b.mp4",
        "extra_ref": str(extra / "notes.txt"),
    }, profiles)
    env = _env(
        VIDEO_ENGINE_PROFILES=str(profiles),
        VIDEO_ENGINE_OPEN_ASSETS=str(open_root),
        VIDEO_ENGINE_STOCK_DIRS=os.pathsep.join((str(stock_a), str(stock_b))),
        VIDEO_ENGINE_ASSET_ROOTS=str(extra),
        VIDEO_ENGINE_WORKSPACE=str(tmp_path / "ws"),
    )
    closure = _closure(proj, env, engine)
    assert closure.ok, closure.missing
    by_rel = {item.rel: item for item in closure.files}
    assert by_rel["maps/a.geojson"].root == "open-assets"
    assert by_rel["catalog.csv"].root == "open-assets" and by_rel["catalog.csv"].role == "sidecar"
    assert by_rel["footage/b.mp4"].root == "stock-1"
    assert by_rel["index.csv"].root == "stock-1"
    assert by_rel["notes.txt"].root == "asset"
    assert closure.env["VIDEO_ENGINE_OPEN_ASSETS"] == "/work/roots/open-assets"
    assert "/work/roots/stock-0" in closure.env["VIDEO_ENGINE_STOCK_DIRS"]
    assert "/work/roots/stock-1" in closure.env["VIDEO_ENGINE_STOCK_DIRS"]
    assert all(not item.link for item in closure.files if item.logical.startswith("open:"))
    absolute = next(item for item in closure.files if item.rel == "notes.txt")
    assert absolute.link == str(extra / "notes.txt")
    assert absolute.dest.startswith("/work/roots/asset/")


def test_missing_files_and_ignored_prose(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    proj = _project(tmp_path, {
        "profile": "default",
        "gone": "assets/missing.mp4",
        "scheme": "open:no/such.png",
        "prose": "see the docs/README.md file",
        "unrooted": "docs/README.md",
    }, profiles)
    (proj / "assets").mkdir()
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles), VIDEO_ENGINE_OPEN_ASSETS=str(tmp_path / "open"))
    closure = _closure(proj, env, engine)
    refs = {item["ref"] for item in closure.missing}
    assert "assets/missing.mp4" in refs
    assert "open:no/such.png" in refs
    assert "docs/README.md" not in refs
    assert "see the docs/README.md file" not in refs
    assert not closure.ok


def test_sound_lock_cache_copy_and_mismatch(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    cache = tmp_path / "cache"
    audio = cache / "sound" / "files" / "bed.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"music")
    sha = hashlib.sha256(b"music").hexdigest()
    proj = _project(tmp_path, {"profile": "default"}, profiles)
    locked = proj / "out" / "locked-sounds"
    locked.mkdir(parents=True)
    (locked / f"{sha}.mp3").write_bytes(b"music")
    manifest = {
        "version": 1,
        "sounds": {"music": {"path": "cache:sound/files/bed.mp3", "sha256": sha, "brief_key": "k"}, "sfx": {}},
    }
    (proj / "out" / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles), VIDEO_ENGINE_CACHE=str(cache))
    closure = _closure(proj, env, engine)
    assert closure.ok, closure.missing
    roles = {(item.root, item.rel): item.role for item in closure.files}
    assert "sound" in roles[("cache", "sound/files/bed.mp3")]
    assert "sound" in roles[("project", f"out/locked-sounds/{sha}.mp3")]
    audio.write_bytes(b"changed")
    (locked / f"{sha}.mp3").unlink()
    again = _closure(proj, env, engine)
    assert any(item["reason"] == "sha256 differs from the sound lock" for item in again.missing)
    audio.unlink()
    gone = _closure(proj, env, engine)
    assert any("not in the cache" in item["reason"] for item in gone.missing)


def test_locked_copy_is_enough_when_the_cache_file_is_gone(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    payload = b"sfx"
    sha = hashlib.sha256(payload).hexdigest()
    proj = _project(tmp_path, {"profile": "default"}, profiles)
    locked = proj / "out" / "locked-sounds"
    locked.mkdir(parents=True)
    (locked / f"{sha}.wav").write_bytes(payload)
    manifest = {"sounds": {"music": None, "sfx": {"hit": {"path": "cache:sound/files/hit.wav", "sha256": sha}}}}
    (proj / "out" / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles), VIDEO_ENGINE_CACHE=str(tmp_path / "empty-cache"))
    closure = _closure(proj, env, engine)
    assert closure.ok, closure.missing
    assert any(item.rel == f"out/locked-sounds/{sha}.wav" for item in closure.files)


def test_profile_include_rules_and_lut(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    (profiles / "parts").mkdir(parents=True)
    (profiles / "luts").mkdir()
    (profiles / "default.toml").write_text("description = \"base\"\n", encoding="utf-8")
    (profiles / "parts" / "extra.toml").write_text("[draft]\nrules_file = \"parts/rules.md\"\n", encoding="utf-8")
    (profiles / "parts" / "rules.md").write_text("- one\n", encoding="utf-8")
    (profiles / "luts" / "a.cube").write_text("LUT\n", encoding="utf-8")
    (profiles / "demo.toml").write_text(
        "include = [\"parts/extra.toml\"]\n[grade]\nlut = \"luts/a.cube\"\n", encoding="utf-8")
    proj = _project(tmp_path, {"profile": "demo", "audio_chain": {"voiceover_path": "vo.wav"},
                               "captions": [{"word": "hi"}]})
    (proj / "vo.wav").write_bytes(b"RIFF")
    (proj / "vo.captions.json").write_text("[]", encoding="utf-8")
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles))
    closure = _closure(proj, env, engine)
    assert closure.ok, closure.missing
    rels = {item.rel for item in closure.files if item.root == "profiles"}
    assert {"default.toml", "demo.toml", "parts/extra.toml", "parts/rules.md", "luts/a.cube"} <= rels
    assert all(item.ship for item in closure.files if item.root == "profiles")
    voice = next(item for item in closure.files if item.rel == "vo.wav")
    assert "captions" in voice.role
    assert any(item.rel == "vo.captions.json" and "captions" in item.role for item in closure.files)


def test_geo_library_files_are_included_when_present(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    geo = tmp_path / "geo"
    base = geo / "natural-earth" / "natural-earth-vector" / "geojson"
    base.mkdir(parents=True)
    (base / "ne_50m_land.geojson").write_text("{}", encoding="utf-8")
    (base / "ne_110m_admin_0_countries.geojson").write_text("{}", encoding="utf-8")
    proj = _project(tmp_path, {"profile": "default"}, profiles)
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles), VIDEO_ENGINE_GEO_LIBRARY=str(geo))
    closure = _closure(proj, env, engine)
    rels = {item.rel for item in closure.files if item.role == "geo"}
    assert "natural-earth/natural-earth-vector/geojson/ne_50m_land.geojson" in rels
    assert "natural-earth/natural-earth-vector/geojson/ne_110m_admin_0_countries.geojson" in rels
    assert closure.env["VIDEO_ENGINE_GEO_LIBRARY"] == "/work/roots/geo-library"


def test_materialize_copies_blobs_checks_sha_and_links(tmp_path: Path):
    payload = b"hello-asset"
    sha = hashlib.sha256(payload).hexdigest()
    cas = tmp_path / "cas"
    (cas / sha[:2]).mkdir(parents=True)
    (cas / sha[:2] / sha).write_bytes(payload)
    dest_root = tmp_path / "work"
    dest = dest_root / "project" / "assets" / "a.bin"
    link = tmp_path / "abs" / "a.bin"
    layout = {
        "mkdir": [str(dest_root / "project"), str(dest_root / "shm")],
        "files": [{
            "ship": True, "sha256": sha, "dest": str(dest), "rel": "assets/a.bin",
        }],
        "links": [{"at": str(link), "dest": str(dest)}],
    }
    materialize(layout, cas)
    assert dest.read_bytes() == payload
    assert link.is_symlink() and os.readlink(link) == str(dest)
    (cas / sha[:2] / sha).write_bytes(b"tamper")
    with pytest.raises(RuntimeError, match="sha256"):
        materialize(layout, cas)


def test_link_allowed_skips_system_paths():
    assert link_allowed("/tmp/project/assets/a.mp4")
    assert not link_allowed("/etc/passwd")
    assert not link_allowed("/usr/bin/ffmpeg")
    assert not link_allowed("assets/a.mp4")


def test_upload_plan_skips_existing_blobs_and_duplicate_shas():
    sha = "ab" + "cd" * 31
    files = [
        {"ship": True, "sha256": sha, "local": "/a"},
        {"ship": True, "sha256": sha, "local": "/a-again"},
        {"ship": False, "sha256": "ff" * 32, "local": "/image"},
        {"ship": True, "sha256": "11" * 32, "local": "/new"},
    ]
    pending, present = upload_plan(files, lambda remote: remote.endswith(sha))
    assert present == [sha]
    assert [item["local"] for item in pending] == ["/new"]


def test_chunk_roundtrip_resume_hole_and_disk(tmp_path: Path):
    src = tmp_path / "final.mp4"
    src.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    fetch = tmp_path / "fetch"
    record = write_chunks(src, fetch / "chunks" / "0000", 8)
    record["rel"] = "out/final.mp4"
    record["index"] = 0
    assert record["size"] == 26
    assert sum(chunk["size"] for chunk in record["chunks"]) == 26
    assert file_sha256(src) == record["sha256"]
    blobs = {}
    for chunk in record["chunks"]:
        blobs[f"/runs/run1/fetch/chunks/0000/{chunk['name']}"] = (fetch / "chunks" / "0000" / chunk["name"]).read_bytes()
    manifest = {"version": 1, "files": [record]}
    project = tmp_path / "proj"
    (project / "out").mkdir(parents=True)
    calls = []

    def reader(path: str) -> bytes:
        calls.append(path)
        return blobs[path]

    # Prefix: the first chunk is already valid, so it is not downloaded again.
    parts = project / "out" / ".modal-fetch" / "run1" / "parts" / "0000"
    parts.mkdir(parents=True)
    (parts / "000000").write_bytes(blobs["/runs/run1/fetch/chunks/0000/000000"])
    stats = download_manifest(manifest, reader, project, run_id="run1", min_free=0)
    assert (project / "out" / "final.mp4").read_bytes() == src.read_bytes()
    assert stats["files"][0]["resumed"] == 1
    assert all(not path.endswith("/000000") for path in calls)

    # A hole: chunk 0 and chunk 2 exist, chunk 1 does not. Chunk 2 is discarded and fetched again.
    calls.clear()
    (project / "out" / "final.mp4").unlink()
    for child in parts.iterdir():
        child.unlink()
    (parts / "000000").write_bytes(blobs["/runs/run1/fetch/chunks/0000/000000"])
    (parts / "000002").write_bytes(b"corrupt!")
    assert verified_prefix(parts, record["chunks"]) == 1
    download_manifest(manifest, reader, project, run_id="run1", min_free=0)
    assert (project / "out" / "final.mp4").read_bytes() == src.read_bytes()
    assert any(path.endswith("/000002") for path in calls)

    # Whole-file mismatch leaves the previous destination in place.
    previous = project / "out" / "final.mp4"
    previous.write_bytes(b"previous")
    bad = dict(record)
    bad["sha256"] = "0" * 64
    with pytest.raises(TransferError):
        assemble_file(parts, bad["chunks"], previous, size=bad["size"], sha256=bad["sha256"])
    assert previous.read_bytes() == b"previous"

    # Not enough room for a chunk.
    def tiny(_path: Path) -> int:
        return 10

    with pytest.raises(Exception, match="free disk") as raised:
        download_manifest(manifest, reader, project, run_id="run1", min_free=1024, free_bytes=tiny)
    assert getattr(raised.value, "exit_code", 74) == 74


def test_unsafe_manifest_path_is_rejected(tmp_path: Path):
    manifest = {"version": 1, "files": [{"rel": "../secrets", "size": 0, "sha256": "e" * 64, "chunks": [], "index": 0}]}
    with pytest.raises(TransferError):
        download_manifest(manifest, lambda _path: b"", tmp_path, run_id="run1", min_free=0)


def test_publish_and_annotate(tmp_path: Path):
    proj = tmp_path / "proj"
    (proj / "out").mkdir(parents=True)
    (proj / "out" / "final.mp4").write_bytes(b"video")
    (proj / "out" / ".modal-fetch").mkdir()
    (proj / "out" / ".modal-fetch" / "note").write_text("skip", encoding="utf-8")
    (proj / "credits.txt").write_text("credits\n", encoding="utf-8")
    report = {"ok": True, "build_id": "abc"}
    (proj / "out" / "build_report.json").write_text(json.dumps(report), encoding="utf-8")
    manifest = publish_outputs(proj, tmp_path / "fetch", 4)
    rels = [item["rel"] for item in manifest["files"]]
    assert "out/final.mp4" in rels and "credits.txt" in rels and "out/build_report.json" in rels
    assert all(".modal-fetch" not in rel for rel in rels)
    written = annotate_reports(proj, {"remote": True, "image_digest": "abc"})
    assert "out/build_report.json" in written
    doc = json.loads((proj / "out" / "build_report.json").read_text(encoding="utf-8"))
    assert doc["remote"]["image_digest"] == "abc" and doc["ok"] is True
    assert (proj / "out" / "final.mp4").read_bytes() == b"video"


def test_parse_args_defaults_and_flags():
    args = parse_args([str(ROOT)])
    assert args.cpu == 8 and args.memory_mb == 16384 and args.timeout_s == 10800
    assert args.gpu == "none" and not args.keep_volume and not args.dry_run and args.fetch is None
    flagged = parse_args([
        str(ROOT), "--cpu", "4", "--memory", "8192", "--timeout", "120", "--gpu", "L4",
        "--keep-volume", "--dry-run",
    ])
    assert (flagged.cpu, flagged.memory_mb, flagged.timeout_s, flagged.gpu) == (4, 8192, 120, "L4")
    assert flagged.keep_volume and flagged.dry_run
    fetched = parse_args([str(ROOT), "--fetch", "abc123"])
    assert fetched.fetch == "abc123"
    selftest = parse_args(["--selftest"])
    assert selftest.selftest and selftest.project is None


@pytest.mark.parametrize("argv", [
    ["--gpu", "A100"],
    ["--cpu", "0"],
    ["--memory", "10"],
    ["--dry-run", "--fetch", "abc"],
    ["--selftest", "--dry-run"],
    [],
])
def test_parse_args_rejects(argv):
    with pytest.raises(UsageError):
        parse_args(argv)


def test_runtime_env_and_disk_and_lock(tmp_path: Path):
    args = parse_args([str(ROOT), "--dry-run"])
    apply_runtime_env(args, {"VIDEO_ENGINE_MODAL_CHUNK_MB": "8", "VIDEO_ENGINE_MIN_FREE_GB": "0",
                             "VIDEO_ENGINE_MODAL_VOLUME": "custom"})
    assert args.chunk_bytes == 8 * 1024 * 1024 and args.min_free_bytes == 0 and args.volume == "custom"
    with pytest.raises(UsageError):
        apply_runtime_env(Args(project=None), {"VIDEO_ENGINE_MIN_FREE_GB": "eight"})
    with pytest.raises(DiskLow):
        assert_free(tmp_path, 10 ** 18)
    lock = tmp_path / "render.lock"
    with RenderLock(lock):
        with pytest.raises(LockBusy):
            with RenderLock(lock):
                pass
    with RenderLock(lock):
        pass


def test_component_compare_and_image_args():
    assert major_token("ffmpeg", "n8.1.3-35-gabc") == "8"
    assert major_token("node", "v22.23.4") == "22"
    assert compare_components(
        {"ffmpeg": "8.0.2", "node": "v22.1.0", "hyperframes": "0.9.13", "puppeteer-core": "25.16.0",
         "three": "0.186.1", "chrome": "152.0.1.1"},
        {"ffmpeg": "8.1.3", "node": "v22.23.4", "hyperframes": "0.9.13", "puppeteer-core": "25.16.0",
         "three": "0.186.1", "chrome": "152.0.7977.30"},
    ) == []
    bad = compare_components(
        {"ffmpeg": "7.1", "node": "v20.0.0", "hyperframes": "0.9.14", "puppeteer-core": "25.16.0",
         "three": "0.186.1", "chrome": "152.0.0.0"},
        {"ffmpeg": "8.1", "node": "v22.0.0", "hyperframes": "0.9.13", "puppeteer-core": "25.16.0",
         "three": "0.186.1", "chrome": "152.0.0.0"},
    )
    assert any(line.startswith("ffmpeg:") for line in bad)
    assert any(line.startswith("hyperframes:") for line in bad)
    assert any(line.startswith("node:") for line in bad)
    pins = {
        "node": {"version": "v22.23.4"},
        "npm": {"hyperframes": "0.9.13", "puppeteer-core": "25.16.0", "three": "0.186.1"},
        "chrome": {"version": "152.0.7977.30"},
    }
    got = image_build_args({"components": {"node": "missing", "hyperframes": "0.9.20"}}, pins)
    assert got["NODE_VERSION"] == "v22.23.4"
    assert got["HF_VERSION"] == "0.9.20"
    assert got["CHROME_VERSION"] == "152.0.7977.30"


def test_dry_run_text_lists_uploads_and_resources(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    profiles = tmp_path / "profiles"
    proj = _project(tmp_path, {"profile": "default", "clip": "assets/clip.mp4"}, profiles)
    (proj / "assets").mkdir()
    (proj / "assets" / "clip.mp4").write_bytes(b"x")
    env = _env(VIDEO_ENGINE_PROFILES=str(profiles))
    closure = _closure(proj, env, engine)
    args = Args(project=proj, cpu=4, memory_mb=2048, timeout_s=90, gpu="none", dry_run=True, volume="video-engine-builds")
    text = format_dry_run(closure, args, toolchain={"components": {"ffmpeg": "8.1.3"}})
    assert "dry run" in text
    assert "cpu=(4, 4)" in text and "memory=(2048, 2048)" in text and "gpu=none" in text
    assert "retries=0" in text and "min_containers=0" in text and "single_use_containers=true" in text
    assert "upload\ttimeline\tproject\tassets/clip.mp4" in text
    assert "modal: not called" in text
    assert "ffmpeg=8.1.3" in text


def test_modal_render_has_no_gpu_default():
    text = (ROOT / "modal_render.py").read_text(encoding="utf-8")
    assert 'gpu="L4"' not in text and "gpu='L4'" not in text and "gpu=\"L4\"" not in text


def test_script_is_executable():
    assert SCRIPT.is_file() and SCRIPT.stat().st_mode & stat.S_IXUSR


def _stub_env(tmp: Path, proj: Path, exit_code: str = "0") -> dict[str, str]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "engine"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$STUB_LOG"\nexit "${STUB_EXIT:-0}"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    profiles = tmp / "profiles"
    profiles.mkdir()
    (profiles / "default.toml").write_text("description = \"t\"\n", encoding="utf-8")
    envfile = tmp / "engine.env"
    lines = [
        f"VIDEO_ENGINE_WORKSPACE={tmp / 'ws'}",
        f"VIDEO_ENGINE_GEO_LIBRARY={tmp / 'geo'}",
        f"VIDEO_ENGINE_OPEN_ASSETS={tmp / 'open'}",
        f"VIDEO_ENGINE_STOCK_DIRS={tmp / 'stock'}",
        f"VIDEO_ENGINE_DOWNLOAD_DIR={tmp / 'dl'}",
        f"VIDEO_ENGINE_CACHE={tmp / 'cache'}",
        f"VIDEO_ENGINE_PROFILES={profiles}",
        f"VIDEO_ENGINE_VENDOR={tmp / 'vendor'}",
        "VIDEO_ENGINE_MIN_FREE_GB=0",
    ]
    envfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIDEO_ENGINE_")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", STUB_LOG=str(tmp / "stub.log"),
               STUB_EXIT=exit_code, VIDEO_ENGINE_ENV_FILE=str(envfile),
               VIDEO_ENGINE_RENDER_LOCK=str(tmp / "render.lock"),
               PYTHONPATH=str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""))
    env["STUB_EXIT"] = exit_code
    return env


def test_script_dry_run_checks_then_prints_closure(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "assets").mkdir()
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"abc")
    (proj / "assets" / "clip.mp4").symlink_to(library / "clip.mp4")
    (proj / "master_timeline.json").write_text(json.dumps({"profile": "default", "clip": "assets/clip.mp4"}),
                                               encoding="utf-8")
    env = _stub_env(tmp_path, proj)
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(SCRIPT), str(proj), "--dry-run", "--cpu", "3"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    log = Path(env["STUB_LOG"]).read_text(encoding="utf-8").split()
    assert log[:2] == ["validate", str(proj.resolve())]
    assert "preflight" in log
    assert "dry run" in result.stdout
    assert "assets/clip.mp4" in result.stdout and "symlink=yes" in result.stdout
    assert "gpu=none" in result.stdout and "modal: not called" in result.stdout
    assert "cpu=(3, 3)" in result.stdout


def test_script_dry_run_stops_when_validate_fails(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "master_timeline.json").write_text("{}", encoding="utf-8")
    env = _stub_env(tmp_path, proj, exit_code="4")
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(SCRIPT), str(proj), "--dry-run"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 4
    log = Path(env["STUB_LOG"]).read_text(encoding="utf-8")
    assert "validate" in log and "preflight" not in log


def test_script_reports_missing_closure_and_missing_env_file(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "assets").mkdir()
    (proj / "master_timeline.json").write_text(json.dumps({"gone": "assets/missing.mp4"}), encoding="utf-8")
    env = _stub_env(tmp_path, proj)
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(SCRIPT), str(proj), "--dry-run"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert "assets/missing.mp4" in result.stdout
    env["VIDEO_ENGINE_ENV_FILE"] = str(tmp_path / "no-such.env")
    bad = subprocess.run([sys.executable, str(SCRIPT), str(proj), "--dry-run"],
                         env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2
    assert "does not exist" in bad.stderr or "does not exist" in bad.stdout
