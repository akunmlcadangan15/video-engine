"""Pins for sound-cache files a project lock still names.

LRU eviction (`video_engine.cache.prune`) sizes the whole cache, including `<cache>/sound/`. A project's
`out/build_manifest.json` sound lock stores `cache:` paths of those files. Eviction used to delete them while the
lock still named them, so the next build stopped and asked for `--relock`.

A pin is a record in `<cache>/sound/pins.json`: one owner per project manifest (`out/build_manifest.json` or
`out/draft/build_manifest.json`). The owner lists the cache-relative audio files that manifest's `sounds` section
still references, with the sha256 the lock stored. Eviction skips a pinned audio file, its sidecar, and any sound
index that names it. `sound/pins.json` itself is skipped while it exists.

The same step copies each pinned file to `<project>/out/locked-sounds/<sha256><ext>`. `manifest.locked_sounds`
reads that copy when the cache file is gone and the bytes still match the lock. The copy is not in the shared
cache, so eviction never removes it.

Release:
- The next eviction re-reads each owner's manifest. An owner is dropped when the manifest is missing, unreadable,
  or no longer lists that path and sha256. A file with no owners left is evictable.
- `engine cache unpin --project P` drops every owner of P immediately. The local copies stay until deleted.
- `engine cache pin --project P` (also the start of `engine build`, and `engine cache --prune --project P`) records
  owners from the manifests on disk and refreshes the local copies.
- Deleting `<project>/out/locked-sounds/` drops the fallback. The lock then needs the cache file or `--relock`.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .. import cache as engine_cache
from .. import config, manifest

PIN_NAME = "pins.json"
VERSION = 1
LOCAL_DIR = "locked-sounds"   # <project>/out/locked-sounds/<sha256><ext>
_MANIFESTS = ("out/build_manifest.json", "out/draft/build_manifest.json")


def pin_file(root: Path | None = None) -> Path:
    return Path(root or config.CACHE_DIR) / "sound" / PIN_NAME


def local_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "out" / LOCAL_DIR


def local_path(project_dir: Path, sha: str, suffix: str) -> Path:
    return local_dir(project_dir) / f"{sha}{suffix}"


def _load(root: Path) -> dict:
    p = pin_file(root)
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "projects": {}}
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("projects"), dict):
        return {"version": VERSION, "projects": {}}
    return doc


def _save(root: Path, doc: dict) -> None:
    p = pin_file(root)
    projects = doc.get("projects") or {}
    if not projects:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": VERSION, "projects": projects}, indent=2, ensure_ascii=False))
    os.replace(tmp, p)


def _entries(doc: dict):
    sounds = (doc or {}).get("sounds") or {}
    if not isinstance(sounds, dict):
        return
    music = sounds.get("music")
    if isinstance(music, dict):
        yield "music", music
    sfx = sounds.get("sfx") or {}
    if isinstance(sfx, dict):
        for kind, entry in sfx.items():
            if isinstance(entry, dict):
                yield f"sfx:{kind}", entry


def cache_files(doc: dict, root: Path) -> dict[str, tuple[str, str]]:
    """`sound/...` paths the manifest lock still names that live under `<root>/sound/files`.

    Values are `(sha256, slot)`. A path outside the sound cache (a project file, a local copy) is not pinned:
    eviction cannot see it."""
    root = Path(root).resolve()
    out: dict[str, tuple[str, str]] = {}
    for slot, entry in _entries(doc):
        raw, sha = entry.get("path"), entry.get("sha256")
        if not isinstance(raw, str) or not isinstance(sha, str) or not sha:
            continue
        path = manifest.resolve(raw, None).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel.startswith("sound/files/") and Path(rel).suffix != ".json":
            out[rel] = (sha, slot)
    return out


def copy_local(project_dir: Path, src: Path, sha: str) -> Path | None:
    """Copy `src` to the project's locked-sounds dir when the bytes match `sha`. Returns the copy, or None."""
    src = Path(src)
    if not src.is_file():
        return None
    if manifest.sha256(src, fresh=True) != sha:
        return None
    dest = local_path(project_dir, sha, src.suffix.lower())
    if dest.is_file() and manifest.sha256(dest, fresh=True) == sha:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    return dest


def fallback(project_dir: Path | None, sha: str, suffix: str) -> Path | None:
    """Project-local copy of a locked sound, when it exists. The caller still checks the hash."""
    if project_dir is None or not sha or not suffix:
        return None
    p = local_path(project_dir, sha, suffix)
    return p if p.is_file() else None


def sync_document(project_dir: Path, doc: dict | None, manifest_rel: str, *,
                  cache_root: Path | None = None) -> dict:
    """Replace this manifest's pin with the cache files `doc` still names, and refresh local copies.

    `doc` None, or a document that names no cache file, drops the owner. Returns `{"pinned": [rels]}`."""
    project_dir = Path(project_dir).resolve()
    root = Path(cache_root or config.CACHE_DIR)
    files = cache_files(doc, root) if doc else {}
    for rel, (sha, _slot) in files.items():
        copy_local(project_dir, root / rel, sha)
    owner = f"{project_dir}|{manifest_rel}"
    with engine_cache.locked(root, exclusive=True):
        reg = _load(root)
        projects = reg["projects"]
        if files:
            projects[owner] = {
                "project": str(project_dir), "manifest": manifest_rel,
                "files": {rel: {"sha256": sha, "slot": slot} for rel, (sha, slot) in files.items()},
            }
        else:
            projects.pop(owner, None)
        _save(root, reg)
    return {"pinned": sorted(files), "owner": owner}


def pin_project(project_dir: Path, *, cache_root: Path | None = None) -> dict:
    """Record pins from the manifests on disk (`out/build_manifest.json` and the draft copy)."""
    project_dir = Path(project_dir).resolve()
    pinned: list[str] = []
    found = False
    for rel in _MANIFESTS:
        f = project_dir / rel
        if not f.is_file():
            continue
        found = True
        try:
            doc = json.loads(f.read_text())
        except json.JSONDecodeError:
            doc = None
        if not isinstance(doc, dict) or doc.get("version") != manifest.VERSION:
            doc = None
        pinned.extend(sync_document(project_dir, doc, rel, cache_root=cache_root)["pinned"])
    return {"project": str(project_dir), "pinned": sorted(set(pinned)), "manifest": found}


def unpin(project_dir: Path, *, cache_root: Path | None = None) -> dict:
    """Drop every pin owned by `project_dir`. Local copies under `out/locked-sounds/` are left in place."""
    project_dir = Path(project_dir).resolve()
    root = Path(cache_root or config.CACHE_DIR)
    with engine_cache.locked(root, exclusive=True):
        reg = _load(root)
        projects = reg["projects"]
        gone = [k for k, rec in projects.items() if rec.get("project") == str(project_dir)]
        released: list[str] = []
        for k in gone:
            released.extend((projects.pop(k).get("files") or {}))
        _save(root, reg)
    return {"project": str(project_dir), "released": sorted(set(released))}


def _indexes_naming(root: Path, names: set[str]) -> set[str]:
    index = root / "sound" / "index"
    if not index.is_dir() or not names:
        return set()
    out = set()
    for idx in index.glob("*.json"):
        try:
            data = json.loads(idx.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and names.intersection(v for v in data.values() if isinstance(v, str)):
            out.add(idx.relative_to(root).as_posix())
    return out


def protected_rels(root: Path | None = None) -> set[str]:
    """Cache-relative paths eviction must keep. Stale owners are dropped first.

    Safe to call while the cache root's exclusive lock is already held (`cache.locked` is re-entrant)."""
    root = Path(root or config.CACHE_DIR)
    with engine_cache.locked(root, exclusive=True):
        reg = _load(root)
        projects = reg["projects"]
        changed = False
        keep: dict[str, str] = {}
        for owner, rec in list(projects.items()):
            proj, rel = rec.get("project"), rec.get("manifest")
            doc = None
            if isinstance(proj, str) and isinstance(rel, str):
                f = Path(proj) / rel
                if f.is_file():
                    try:
                        doc = json.loads(f.read_text())
                    except (OSError, json.JSONDecodeError):
                        doc = None
            live = cache_files(doc, root) if isinstance(doc, dict) and doc.get("version") == manifest.VERSION else {}
            recorded = rec.get("files") if isinstance(rec.get("files"), dict) else {}
            still = {r: meta for r, meta in recorded.items()
                     if r in live and isinstance(meta, dict) and meta.get("sha256") == live[r][0]}
            if still != recorded:
                changed = True
                if still:
                    rec["files"] = still
                else:
                    projects.pop(owner, None)
                    continue
            for r, meta in still.items():
                keep[r] = meta.get("sha256", "")
        if changed:
            _save(root, reg)
        extra = set(keep)
        pin = pin_file(root)
        if pin.is_file():
            extra.add(pin.relative_to(root).as_posix())
        names = {Path(r).name for r in keep}
        stems = {Path(r).stem for r in keep}
        files = root / "sound" / "files"
        if files.is_dir():
            for stem in stems:
                side = files / f"{stem}.json"
                if side.is_file():
                    extra.add(side.relative_to(root).as_posix())
        extra |= _indexes_naming(root, names)
        return extra
