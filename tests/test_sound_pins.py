"""Sound-cache pins: a file named by a project sound lock is not LRU-evicted, and a project-local copy
satisfies the lock when the cache file is gone. Releasing the pin (missing manifest, or unpin) makes the
cache file evictable again."""
import json
from pathlib import Path

from video_engine import cache as engine_cache
from video_engine import config, manifest
from video_engine.cli import main
from video_engine.sound import cache, pins


def _tone(path: Path, freq: int = 440) -> Path:
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d=0.2:r=48000", str(path)],
                   check=True)
    return path


def _prov(i="es-pop"):
    return {"source": "epidemic", "id": i, "title": "T", "artist": "A",
            "credit": cache.credit_line("epidemic", i, "T", "A")}


def _lock(project: Path, stored, sha: str) -> dict:
    rel = "cache:" + stored.resolve().relative_to(config.CACHE_DIR.resolve()).as_posix()
    doc = {"version": manifest.VERSION, "sounds": {
        "music": None,
        "sfx": {"pop": {"path": rel, "sha256": sha, "provenance": _prov()}},
    }}
    man = project / "out" / "build_manifest.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps(doc))
    return doc


def test_good_lock_pins_the_file_and_lru_evicts_only_the_unpinned(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    kept = cache.put("sfx", "pop", _tone(tmp_path / "pop.wav", 440), _prov(), 200)
    loose = cache.put("sfx", "tick", _tone(tmp_path / "tick.wav", 880), _prov("es-tick"), 100)
    sha = manifest.sha256(kept.path)
    _lock(p, kept.path, sha)
    assert pins.pin_project(p)["pinned"] == [kept.path.resolve().relative_to(config.CACHE_DIR.resolve()).as_posix()]
    rep = engine_cache.prune(max_bytes=0)
    assert kept.path.is_file() and not loose.path.is_file()
    assert str(kept.path.resolve().relative_to(config.CACHE_DIR.resolve())) in rep.skipped_pinned
    copy = pins.local_path(p, sha, kept.path.suffix)
    assert copy.is_file() and manifest.sha256(copy, fresh=True) == sha
    kept.path.unlink()
    got = manifest.locked_sounds(json.loads((p / "out" / "build_manifest.json").read_text()), tl, ["pop"], p)
    assert got["sfx"]["pop"][0] == copy


def test_good_deleting_the_manifest_releases_the_pin(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, _ = project()
    kept = cache.put("sfx", "pop", _tone(tmp_path / "pop.wav"), _prov(), 200)
    _lock(p, kept.path, manifest.sha256(kept.path))
    pins.pin_project(p)
    (p / "out" / "build_manifest.json").unlink()
    engine_cache.prune(max_bytes=0)
    assert not kept.path.is_file()
    assert pins.pin_file().exists() is False


def test_good_unpin_releases_the_cache_file_and_leaves_the_local_copy(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    kept = cache.put("sfx", "pop", _tone(tmp_path / "pop.wav"), _prov(), 200)
    sha = manifest.sha256(kept.path)
    _lock(p, kept.path, sha)
    assert main(["cache", "--pin", "--project", str(p)]) == 0
    assert main(["cache", "--unpin", "--project", str(p)]) == 0
    engine_cache.prune(max_bytes=0)
    assert not kept.path.is_file()
    copy = pins.local_path(p, sha, ".wav")
    assert copy.is_file()
    got = manifest.locked_sounds(json.loads((p / "out" / "build_manifest.json").read_text()), tl, ["pop"], p)
    assert got["sfx"]["pop"][0] == copy


def test_bad_cache_pin_without_a_project_is_usage(capsys):
    assert main(["cache", "--pin"]) == 2
    assert "--project" in capsys.readouterr().err
