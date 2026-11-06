"""Sound cache under <engine cache>/sound/ and the mixer's resolver adapter.

Layout (root = config.CACHE_DIR / "sound"):
  files/<blake3>.<ext>     audio file, named by the BLAKE3 of its bytes
  files/<blake3>.json      sidecar: {"type", "kind" | "brief", "file", "duration_ms", "provenance"}
  index/sfx-<kind>.json    {source: "<blake3>.<ext>"}
  index/music-<key>.json   {source: "<blake3>.<ext>"}; key = brief_key(brief)
An entry is a hit only when its index line, audio file and sidecar all exist; a hit touches all three (LRU).
Inside an engine cache session (video_engine.cache.session) a lookup or put leases the index, file and sidecar
before reading or writing them; eviction and prune skip leased files.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import cache as engine_cache, config
from ..timeline import MusicBrief
from ..util import EngineError, blake3_files, blake3_json
from . import AUDIO_EXTS, EPIDEMIC, LOCAL_TEST, PROVENANCE_KEYS, SFX_KINDS, SOURCES, required_source

EPIDEMIC_URL = "https://www.epidemicsound.com"
EPIDEMIC_LICENCE = "Epidemic Sound licence"
CREDITS_HEADER = "Audio (Epidemic Sound)"


@dataclass(frozen=True)
class ResolvedSound:
    path: Path
    provenance: dict     # source, id, title, artist, credit


def root() -> Path:
    return config.CACHE_DIR / "sound"


# ---------------------------------------------------------------------------------------------------- keys

def brief_key(brief: dict) -> str:
    """BLAKE3 of the validated music brief: mood stripped and lower-cased, duration_s rounded to 3 decimals."""
    b = MusicBrief.model_validate(brief).model_dump()
    b["mood"] = b["mood"].strip().lower()
    b["duration_s"] = round(float(b["duration_s"]), 3)
    return blake3_json(b)


def slot(typ: str, key: str) -> str:
    """Index name of an SFX kind ("sfx", kind) or a music brief key ("music", brief_key)."""
    if typ == "sfx":
        if key not in SFX_KINDS:
            raise EngineError(f"unknown SFX kind '{key}' (known: {', '.join(SFX_KINDS)})")
    elif typ != "music":
        raise EngineError(f"unknown sound type '{typ}'")
    return f"{typ}-{key}"


def credit_line(source: str, id_: str, title: str, artist: str) -> str:
    """credits.txt line body of an Epidemic Sound file (same layout as the visual credits)."""
    if source != EPIDEMIC:
        raise EngineError(f"no publishable credit for source '{source}'")
    return f"{id_} — {artist} — \"{title}\" — {EPIDEMIC_URL} — {EPIDEMIC_LICENCE}"


# ---------------------------------------------------------------------------------------------------- store

def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = engine_cache.acquire(engine_cache.tmp_name(p))
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check_provenance(prov: dict) -> dict:
    if not isinstance(prov, dict) or set(prov) != set(PROVENANCE_KEYS):
        raise EngineError(f"provenance must have exactly the keys {', '.join(PROVENANCE_KEYS)}: {prov!r}")
    if prov["source"] not in SOURCES:
        raise EngineError(f"provenance source '{prov['source']}' unknown (known: {', '.join(SOURCES)})")
    for k in PROVENANCE_KEYS:
        if not isinstance(prov[k], str) or (k != "artist" and not prov[k].strip()):
            raise EngineError(f"provenance {k} must be a non-empty string: {prov!r}")
    return dict(prov)


def put(typ: str, key: str, src: Path, provenance: dict, duration_ms: int, extra: dict | None = None) -> ResolvedSound:
    """Store an audio file under (type, key) for provenance["source"]; returns the cached entry."""
    name_slot = slot(typ, key)
    prov = check_provenance(provenance)
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in AUDIO_EXTS or not src.is_file() or src.stat().st_size == 0:
        raise EngineError(f"{src}: not a non-empty audio file ({', '.join(AUDIO_EXTS)})")
    h = blake3_files([src])[0]
    files = root() / "files"
    files.mkdir(parents=True, exist_ok=True)
    dst = engine_cache.acquire(files / f"{h}{ext}")
    side = engine_cache.acquire(files / f"{h}.json")
    idx = engine_cache.acquire(root() / "index" / f"{name_slot}.json")
    if not dst.is_file():
        tmp = engine_cache.acquire(engine_cache.tmp_name(dst))
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    _write_json(side, {"type": typ, ("kind" if typ == "sfx" else "brief"): key, "file": dst.name,
                                      "duration_ms": int(duration_ms), "provenance": prov, **(extra or {})})
    entries = read_json(idx) or {}
    entries[prov["source"]] = dst.name
    _write_json(idx, entries)
    engine_cache.prune(protect=(dst, side, idx))
    return ResolvedSound(dst, prov)


def lookup(typ: str, key: str, source: str) -> ResolvedSound | None:
    """Cached entry of (type, key) from source, or None (missing or broken entry)."""
    idx = engine_cache.acquire(root() / "index" / f"{slot(typ, key)}.json")
    name = (read_json(idx) or {}).get(source)
    if not name:
        return None
    f = engine_cache.acquire(root() / "files" / name)
    side = engine_cache.acquire(f.with_suffix(".json"))
    meta = read_json(side)
    try:
        if not f.is_file() or f.stat().st_size == 0 or not meta or meta.get("provenance", {}).get("source") != source:
            return None
        for p in (f, side, idx):
            os.utime(p)
    except FileNotFoundError:     # removed by a pruner between the reads (only possible outside a session)
        return None
    return ResolvedSound(f, dict(meta["provenance"]))


def _pick(typ: str, key: str, profile) -> ResolvedSound | None:
    req = required_source(profile)
    for source in ([req] if req else [EPIDEMIC, LOCAL_TEST]):
        r = lookup(typ, key, source)
        if r:
            return r
    return None


# ---------------------------------------------------------------------------------------------------- resolver

def resolve(kind: str, profile) -> ResolvedSound:
    """SFX file for kind: the cached file from the profile's required source, else (no requirement) the cached
    Epidemic file, else a cached local-test file; with no such file, the generated local test tone (source
    local-test). Gate A02 fails local-test files when the profile requires a source."""
    r = _pick("sfx", kind, profile)
    if r:
        return r
    from .. import mixer
    t = mixer.local_sfx(kind)
    return ResolvedSound(t.path, t.provenance)


def resolve_music(brief: dict, profile) -> ResolvedSound | None:
    """Music file for a brief: the cached file from the profile's required source, else (no requirement) the cached
    Epidemic file, else a cached local-test file; None when none is cached."""
    return _pick("music", brief_key(brief), profile)


def missing(tl, profile) -> list[dict]:
    """Request items, [] without a profile [sound]. With a required source: every SFX kind of the timeline and the
    music brief with no cached file from that source. Without one: the music brief when no source has a file for it
    (the mixer has no fallback for a brief; SFX fall back to local-test tones)."""
    from .. import sfx
    if profile.sound is None:
        return []
    req = required_source(profile)
    out = [] if req is None else [{"slot": slot("sfx", k), "type": "sfx", "kind": k}
                                  for k in sfx.kinds_used(tl) if not lookup("sfx", k, req)]
    mb = tl.audio_chain.music_brief
    if mb is not None:
        key = brief_key(mb.model_dump())
        if not any(lookup("music", key, s) for s in ((req,) if req else SOURCES)):
            out.append({"slot": slot("music", key), "type": "music", "brief": mb.model_dump()})
    return out


# ---------------------------------------------------------------------------------------------------- credits

def credit_lines(provenances) -> list[str]:
    """Unique credit lines of Epidemic Sound files, in order; other sources have no publishable credit."""
    return list(dict.fromkeys(p["credit"] for p in provenances if p and p.get("source") == EPIDEMIC))


def append_credits(project_dir: Path, provenances) -> list[str]:
    """Append the credit lines of the given provenances that are not yet in <project>/credits.txt, under the line
    CREDITS_HEADER (added once). Returns the appended lines. The read-modify-write runs under the sound cache lock
    so two builds of the same project cannot drop each other's lines; the final file replaces the old one atomically."""
    f = Path(project_dir) / "credits.txt"
    with engine_cache.locked(config.CACHE_DIR / "sound", exclusive=True):
        text = f.read_text() if f.is_file() else ""
        have = {ln.strip()[2:] for ln in text.splitlines() if ln.strip().startswith("- ")}
        new = [c for c in credit_lines(provenances) if c not in have]
        if not new:
            return []
        add = "" if not text or text.endswith("\n") else "\n"
        if CREDITS_HEADER not in text.splitlines():
            add += ("\n" if text else "") + CREDITS_HEADER + "\n"
        add += "".join(f"- {c}\n" for c in new)
        tmp = engine_cache.acquire(engine_cache.tmp_name(f))
        tmp.write_text(text + add)
        os.replace(tmp, f)
        return new


# ---------------------------------------------------------------------------------------------------- cleanup

def prune(dry_run: bool = False, cache_dir: Path | None = None) -> dict:
    """Remove broken entries: index lines whose file or sidecar is gone, sidecars without a file, files without a
    sidecar, unreferenced files and temp files. Leased files (an in-flight write leases its temp file) are kept and listed in
    "skipped_leased". Size-based LRU eviction is engine cache.prune().
    cache_dir: engine cache root (default config.CACHE_DIR); paths are reported relative to <cache_dir>/sound."""
    base = Path(cache_dir) if cache_dir is not None else config.CACHE_DIR
    r = base / "sound"
    with engine_cache.locked(base, exclusive=True):
        held, reclaimed = engine_cache.leased(base)
        return {**_prune(r, {str(Path(p).relative_to("sound")) for p in held if p.startswith("sound/")}, dry_run),
                "reclaimed_leases": reclaimed}


def _prune(r: Path, held: set[str], dry_run: bool) -> dict:
    files, index = r / "files", r / "index"
    gone: list[Path] = []
    rewrite: dict[Path, dict] = {}
    refs: set[str] = set()
    for idx in sorted(index.glob("*.json")) if index.is_dir() else []:
        entries = read_json(idx) or {}
        keep = {s: n for s, n in entries.items() if (files / n).is_file() and (files / n).with_suffix(".json").is_file()}
        refs |= set(keep.values())
        if not keep:
            gone.append(idx)
        elif keep != entries:
            rewrite[idx] = keep
    stems = {Path(n).stem for n in refs}
    for f in sorted(files.iterdir()) if files.is_dir() else []:
        if f.name.endswith(".tmp") or (f.name not in refs if f.suffix != ".json" else f.stem not in stems):
            gone.append(f)
    skipped = [f for f in gone if str(f.relative_to(r)) in held]
    gone = [f for f in gone if str(f.relative_to(r)) not in held]
    size = sum(f.stat().st_size for f in gone if f.is_file())
    if not dry_run:
        for idx, keep in rewrite.items():
            _write_json(idx, keep)
        for f in gone:
            f.unlink(missing_ok=True)
    return {("would_delete" if dry_run else "deleted"): [str(p.relative_to(r)) for p in gone],
            "rewritten": [str(p.relative_to(r)) for p in rewrite], "bytes": size,
            "skipped_leased": [str(p.relative_to(r)) for p in skipped]}
