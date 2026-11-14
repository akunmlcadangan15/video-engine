"""Chunked, resumable copy of build outputs.

A finished remote build writes `out/` onto a volume as a manifest of chunks
`{name, size, sha256}` plus the whole-file size and sha256. The local fetch
verifies a contiguous prefix of chunks, downloads only what is missing or
wrong, checks each chunk and the assembled file, and replaces the destination
with `os.replace`. A failed assemble leaves the previous file in place.

The volume itself is behind a small file-system interface so tests can use a
dictionary. This module does not import Modal.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Callable


class TransferError(RuntimeError):
    """A chunk, path or assembled file failed verification."""


class DiskLow(RuntimeError):
    """The write would leave less free space than the reserve."""

    exit_code = 74


def check_run_id(run_id: str) -> str:
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id or ""):
        raise TransferError(f"invalid run id {run_id!r}")
    return run_id


def blob_remote(sha256: str) -> str:
    if len(sha256) < 2 or any(c not in "0123456789abcdef" for c in sha256):
        raise TransferError(f"invalid blob sha256 {sha256!r}")
    return f"/cas/blobs/{sha256[:2]}/{sha256}"


def run_remote(run_id: str) -> str:
    return f"/runs/{check_run_id(run_id)}"


def upload_plan(files: list[dict], exists) -> tuple[list[dict], list[str]]:
    """Ship each distinct sha256 once. `exists(remote) -> bool` skips blobs already on the volume.

    Returns (files to upload, shas already present).
    """
    pending: list[dict] = []
    present: list[str] = []
    seen: set[str] = set()
    for item in files:
        if not item.get("ship"):
            continue
        sha = str(item.get("sha256") or "")
        if sha in seen:
            continue
        seen.add(sha)
        remote = blob_remote(sha)
        if exists(remote):
            present.append(sha)
        else:
            pending.append({"sha256": sha, "local": item.get("local") or "", "remote": remote})
    return pending, present


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_rel(rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or not rel or rel.startswith(("/", "\\")):
        raise TransferError(f"unsafe output path {rel!r}")
    return path


def room_for(free: int, need: int, min_free: int) -> bool:
    """True when `need` bytes can be written and, when min_free > 0, strictly more than
    `min_free` bytes would remain. A zero reserve still requires the bytes themselves."""
    if free < need:
        return False
    if min_free <= 0:
        return True
    return free - need > min_free


def iter_outputs(project: Path) -> list[Path]:
    """Published files to bring back: everything regular under out/ except dot-directories
    (staging, the local fetch cache), plus credits.txt when the build rewrote it."""
    files: list[Path] = []
    out = project / "out"
    if out.is_dir():
        for path in sorted(out.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(project)
            if any(part.startswith(".") for part in rel.parts):
                continue
            files.append(path)
    credits = project / "credits.txt"
    if credits.is_file() and not credits.is_symlink():
        files.append(credits)
    return files


def write_chunks(src: Path, chunk_dir: Path, chunk_bytes: int) -> dict:
    """Split `src` into `chunk_dir`/`000000`... and return the file record (without `rel`)."""
    if chunk_bytes < 1:
        raise TransferError("chunk size must be positive")
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    whole = hashlib.sha256()
    size = 0
    index = 0
    with src.open("rb") as fh:
        while True:
            blob = fh.read(chunk_bytes)
            if not blob:
                break
            name = f"{index:06d}"
            digest = sha256_bytes(blob)
            tmp = chunk_dir / f".{name}.tmp"
            tmp.write_bytes(blob)
            os.replace(tmp, chunk_dir / name)
            chunks.append({"name": name, "size": len(blob), "sha256": digest})
            whole.update(blob)
            size += len(blob)
            index += 1
    return {"size": size, "sha256": whole.hexdigest(), "chunks": chunks}


def publish_outputs(project: Path, fetch_root: Path, chunk_bytes: int) -> dict:
    """Write the fetch manifest and chunk files for every published output."""
    files = []
    chunks_root = fetch_root / "chunks"
    for index, path in enumerate(iter_outputs(project)):
        rel = path.relative_to(project).as_posix()
        safe_rel(rel)
        record = write_chunks(path, chunks_root / f"{index:04d}", chunk_bytes)
        record["rel"] = rel
        record["index"] = index
        files.append(record)
    manifest = {"version": 1, "files": files}
    fetch_root.mkdir(parents=True, exist_ok=True)
    tmp = fetch_root / ".manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, fetch_root / "manifest.json")
    return manifest


def verified_prefix(chunk_dir: Path, chunks: list[dict]) -> int:
    """How many leading chunks exist and match size and sha256. A hole or a mismatch stops the prefix."""
    count = 0
    for chunk in chunks:
        path = chunk_dir / chunk["name"]
        if not path.is_file():
            break
        if path.stat().st_size != chunk["size"] or file_sha256(path) != chunk["sha256"]:
            break
        count += 1
    return count


def discard_after(chunk_dir: Path, chunks: list[dict], keep: int) -> None:
    for chunk in chunks[keep:]:
        (chunk_dir / chunk["name"]).unlink(missing_ok=True)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def assemble_file(chunk_dir: Path, chunks: list[dict], dest: Path, *, size: int, sha256: str) -> None:
    """Concatenate a verified chunk set onto `dest` via a temporary file and `os.replace`.

    On a hash or size mismatch the temporary file is removed and `dest` is left unchanged.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.partial")
    whole = hashlib.sha256()
    written = 0
    try:
        with tmp.open("wb") as out:
            for chunk in chunks:
                blob = (chunk_dir / chunk["name"]).read_bytes()
                if len(blob) != chunk["size"] or sha256_bytes(blob) != chunk["sha256"]:
                    raise TransferError(f"chunk {chunk['name']} changed before assemble")
                out.write(blob)
                whole.update(blob)
                written += len(blob)
            out.flush()
            os.fsync(out.fileno())
        if written != size or whole.hexdigest() != sha256:
            raise TransferError(f"{dest.name} sha256 or size does not match the manifest")
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def fetch_file(record: dict, reader: Callable[[str], bytes], dest: Path, parts: Path, *,
               remote_for: Callable[[dict], str], min_free: int,
               free_bytes: Callable[[Path], int] | None = None) -> dict:
    """Resume `record` into `dest`. Returns `{resumed, downloaded}` chunk counts."""
    chunks = list(record.get("chunks") or [])
    parts.mkdir(parents=True, exist_ok=True)
    measure = free_bytes or (lambda path: shutil.disk_usage(path if path.exists() else path.parent).free)
    keep = verified_prefix(parts, chunks)
    discard_after(parts, chunks, keep)
    downloaded = 0
    for chunk in chunks[keep:]:
        anchor = parts if parts.exists() else dest.parent
        if not room_for(measure(anchor), int(chunk["size"]), min_free):
            raise DiskLow(f"not enough free disk to fetch {record.get('rel')} chunk {chunk['name']} "
                          f"while keeping more than {min_free} bytes free")
        data = reader(remote_for(chunk))
        if len(data) != chunk["size"] or sha256_bytes(data) != chunk["sha256"]:
            raise TransferError(f"chunk {chunk['name']} of {record.get('rel')} failed sha256 verification")
        _atomic_bytes(parts / chunk["name"], data)
    # The assembled file needs another `size` bytes until the parts can be deleted.
    if chunks and not room_for(measure(dest.parent if dest.parent.exists() else parts), int(record["size"]), min_free):
        raise DiskLow(f"not enough free disk to assemble {record.get('rel')} "
                      f"while keeping more than {min_free} bytes free")
    assemble_file(parts, chunks, dest, size=int(record["size"]), sha256=record["sha256"])
    return {"resumed": keep, "downloaded": downloaded + (len(chunks) - keep)}


def download_manifest(manifest: dict, reader: Callable[[str], bytes], project: Path, *,
                      run_id: str, min_free: int, free_bytes: Callable[[Path], int] | None = None,
                      remote_chunk: Callable[[int, dict], str] | None = None) -> dict:
    """Download every file in a fetch manifest into `project`, resuming from `out/.modal-fetch/<run_id>`."""
    if manifest.get("version") != 1:
        raise TransferError(f"unsupported fetch manifest version {manifest.get('version')!r}")
    parts_root = project / "out" / ".modal-fetch" / run_id / "parts"
    stats: list[dict] = []
    for record in manifest.get("files") or []:
        rel = safe_rel(str(record.get("rel") or ""))
        dest = project / rel
        parts = parts_root / f"{int(record.get('index', len(stats))):04d}"
        index = int(record.get("index", len(stats)))

        def one(chunk: dict, _index: int = index) -> str:
            if remote_chunk is not None:
                return remote_chunk(_index, chunk)
            return f"/runs/{run_id}/fetch/chunks/{_index:04d}/{chunk['name']}"

        stat = fetch_file(record, reader, dest, parts, remote_for=one, min_free=min_free, free_bytes=free_bytes)
        stat["rel"] = rel.as_posix()
        stats.append(stat)
    return {"files": stats}


def annotate_reports(project: Path, remote: dict) -> list[str]:
    """Add `remote` to the build report and manifest. The video file is not touched.

    The rewritten JSON is replaced atomically. A local report therefore differs from a
    report written by a build on this machine by that object (and by JSON formatting).
    """
    done = []
    for rel in ("out/build_report.json", "out/build_report.failed.json", "out/build_manifest.json",
                "out/draft/build_report.json", "out/draft/build_manifest.json"):
        path = project / rel
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(doc, dict):
            continue
        doc["remote"] = remote
        tmp = path.with_name(f".{path.name}.remote.tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        done.append(rel)
    return done


def remote_record(*, host: dict, image_id: str, toolchain_pin_sha256: str, cpu: tuple[float, float],
                  memory_mb: tuple[int, int], gpu: str, wall_s: float, peak_rss_mb: float | None,
                  vcpu: int | None, timeout_s: int, toolchain: dict | None = None,
                  mismatches: list[str] | None = None, remote_cache: dict | None = None) -> dict:
    """Facts a remote build records in build_report.json and build_manifest.json."""
    request_core_s = round(cpu[0] * wall_s, 3)
    request_gib_s = round((memory_mb[0] / 1024) * wall_s, 3)
    rec = {
        "remote": True,
        "host": host,
        "image_id": image_id,
        "image_digest": image_id or toolchain_pin_sha256,
        "toolchain_pin_sha256": toolchain_pin_sha256,
        "cpu": {"request": cpu[0], "limit": cpu[1], "observed": vcpu},
        "memory_mb": {"request": memory_mb[0], "limit": memory_mb[1], "peak_rss_mb": peak_rss_mb},
        "gpu": gpu or "none",
        "wall_s": round(wall_s, 3),
        "timeout_s": timeout_s,
        "toolchain": toolchain or {},
        "toolchain_mismatch": mismatches or [],
        "cost": {
            "cpu_request_core_seconds": request_core_s,
            "memory_request_gib_seconds": request_gib_s,
            "gpu": gpu or "none",
            "note": ("Modal bills the greater of the resource request and observed use, up to the limit, "
                     "for the wall time of the container. The products above are the request-based lower bound. "
                     "GPU time is billed only when a GPU is requested."),
        },
    }
    if remote_cache is not None:
        rec["remote_cache"] = remote_cache
    return rec


def _iter_cache_entries(root: Path) -> list[tuple[Path, Path]]:
    """Valid cached entry files `(src, rel_path)` across `cache.KINDS`, skipping locks, leases, symlinks, `.tmp`
    writers, and empty files."""
    from . import cache

    entries: list[tuple[Path, Path]] = []
    for kind in cache.KINDS:
        kdir = root / kind
        if not kdir.is_dir():
            continue
        for p in sorted(kdir.rglob("*")):
            if not p.is_file() or p.is_symlink() or p.name.endswith(".tmp"):
                continue
            try:
                if p.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            entries.append((p, p.relative_to(root)))
    return entries


def _copy_atomic_preserve(src: Path, dst: Path) -> int:
    """Copy `src` onto `dst` atomically via a sibling `.tmp` file + `os.replace`, preserving mtime for LRU order."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(src, tmp)
        size = tmp.stat().st_size
        os.replace(tmp, dst)
        return size
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def pull_remote_cache(remote_cache: Path, local_cache: Path) -> dict:
    """Seed local NVMe `local_cache` with valid entries (`cache.KINDS`) from volume path `remote_cache`.

    Existing paths in `local_cache` (such as CAS-materialised closure files) are left intact so verified closure inputs
    always win. Never acquires `fcntl.flock` on `remote_cache`."""
    stats = {"files": 0, "skipped": 0, "bytes": 0}
    if not remote_cache.is_dir():
        return stats
    local_cache.mkdir(parents=True, exist_ok=True)
    for src, rel in _iter_cache_entries(remote_cache):
        dst = local_cache / rel
        if dst.exists():
            stats["skipped"] += 1
            continue
        stats["bytes"] += _copy_atomic_preserve(src, dst)
        stats["files"] += 1
    return stats


def push_remote_cache(local_cache: Path, remote_cache: Path, max_bytes: int | None = None) -> dict:
    """Prune NVMe `local_cache` with `cache.prune`, mirror new/touched `cache.KINDS` entries atomically back to
    `remote_cache`, and remove entries no longer kept after pruning so volume cache stays bounded."""
    from . import cache

    stats = {"files": 0, "touched": 0, "skipped": 0, "removed": 0, "pruned_local": 0, "bytes": 0}
    if not local_cache.is_dir():
        return stats
    if max_bytes is None and (raw_mb := os.environ.get("VIDEO_ENGINE_CACHE_MAX_MB")):
        try:
            max_bytes = int(raw_mb) * 1024 * 1024
        except ValueError:
            max_bytes = None
    pruned = cache.prune(max_bytes=max_bytes, root=local_cache)
    stats["pruned_local"] = len(pruned.deleted)
    entries = _iter_cache_entries(local_cache)
    keep = {rel for _, rel in entries}
    remote_cache.mkdir(parents=True, exist_ok=True)
    for kind in cache.KINDS:
        kdir = remote_cache / kind
        if not kdir.is_dir():
            continue
        for p in sorted(kdir.rglob("*")):
            if (p.is_file() or p.is_symlink()) and p.relative_to(remote_cache) not in keep:
                p.unlink(missing_ok=True)
                stats["removed"] += 1
    for src, rel in entries:
        dst = remote_cache / rel
        try:
            src_st = src.stat()
        except FileNotFoundError:
            continue
        if dst.is_file():
            dst_st = dst.stat()
            if dst_st.st_size == src_st.st_size:
                if abs(dst_st.st_mtime - src_st.st_mtime) < 1e-3:
                    stats["skipped"] += 1
                    continue
                try:
                    os.utime(dst, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
                    stats["touched"] += 1
                    continue
                except OSError:
                    pass
        stats["bytes"] += _copy_atomic_preserve(src, dst)
        stats["files"] += 1
    return stats
