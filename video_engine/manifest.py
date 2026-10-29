"""Locked build manifest: <project>/out/build_manifest.json, written before any segment renders.

Contents (every file entry is {"path", "sha256"}; path is portable: relative to the project dir, `cache:<rel>`
under the cache root, `engine:<rel>` under the engine checkout, else absolute):
  build_id    random hex id of this build; build_report.json carries the same id; deliver refuses a mismatch
  timeline    sha256 of master_timeline.json
  profile     {"name", "blake3"} of the merged profile (default < channel < project)
  inputs      every file the timeline references (images, clips, map sources, LUTs, voiceover, BGM)
  fonts       every file under config.FONTS_DIR
  sounds      {"music": {"brief_key", "path", "sha256", "provenance"} | null, "sfx": {kind: {...}}}
              Locked: a later build reuses these exact files (sha256 verified) and never asks the sound resolver
              again, until `engine build --relock`. A locked file that changed or vanished is a stage error naming
              --relock.
  toolchain   ffmpeg version line, x264 args, node / chrome / hyperframes pins (vendor/VERSIONS.json), engine version
  seeds       every random seed the render uses (generated local test tones)
  resources   worker budget of the build (video_engine/resources.py)
  stage_keys  cache keys of the composite and audio stages (filled after they run)

Stage cache keys (cmd_key): the exact ffmpeg command with every input path replaced by its sha256, so a key
changes exactly when one of its entries does. Visual (segments, composite), caption (the .ass and fonts, inside
the composite key) and audio (mix + loudness) caches are separate entries: a music change re-runs only the audio
stage and the stream-copy mux.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import config
from .util import EngineError, blake3_json

MANIFEST = "build_manifest.json"
VERSION = 1


@lru_cache(maxsize=4096)
def _sha(path: str, mtime_ns: int, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256(p: Path | str, fresh: bool = False) -> str:
    """sha256 of a file; memoised per (path, mtime, size) unless `fresh` (lock checks: a same-size rewrite within
    one mtime tick, or a copy keeping the mtime, must still be seen)."""
    st = os.stat(p)
    if fresh:
        return _sha.__wrapped__(str(p), st.st_mtime_ns, st.st_size)
    return _sha(str(Path(p).resolve()), st.st_mtime_ns, st.st_size)


ROOTS = (("cache:", lambda: config.CACHE_DIR), ("engine:", lambda: config.ENGINE_ROOT))


def portable(p: Path | str, project_dir: Path | None = None) -> str:
    """Manifest path of `p`: project-relative, else `cache:`/`engine:` prefixed relative path, else absolute."""
    rp = Path(p).resolve()
    if project_dir is not None and rp.is_relative_to(Path(project_dir).resolve()):
        return rp.relative_to(Path(project_dir).resolve()).as_posix()
    for prefix, root in ROOTS:
        r = Path(root()).resolve()
        if rp.is_relative_to(r):
            return prefix + rp.relative_to(r).as_posix()
    return str(rp)


def resolve(s: str, project_dir: Path | None = None) -> Path:
    for prefix, root in ROOTS:
        if s.startswith(prefix):
            return Path(root()) / s[len(prefix):]
    if os.path.isabs(s) or project_dir is None:
        return Path(s)
    return Path(project_dir) / s


def entry(p: Path | str, project_dir: Path | None = None) -> dict:
    return {"path": portable(p, project_dir), "sha256": sha256(p)}


@lru_cache(maxsize=1)
def ffmpeg_version() -> str:
    try:
        out = subprocess.run([config.FFMPEG, "-version"], capture_output=True, text=True, timeout=20).stdout
        return out.splitlines()[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def toolchain() -> dict:
    pins = json.loads((config.VENDOR / "VERSIONS.json").read_text())
    from importlib.metadata import PackageNotFoundError, version
    try:
        ev = version("video-engine")
    except PackageNotFoundError:
        ev = "unknown"
    return {"ffmpeg": ffmpeg_version(), "x264": list(config.X264), "node": pins["node"]["version"],
            "chrome": f"{pins['chrome']['product']} {pins['chrome']['version']}",
            "hyperframes": pins["npm"]["hyperframes"], "puppeteer-core": pins["npm"]["puppeteer-core"],
            "engine": ev}


def timeline_files(tl, project_dir: Path) -> list[Path]:
    """Every existing file named by a string value of the timeline (relative to the project dir or absolute),
    plus the profile grade LUT."""
    seen: dict[str, Path] = {}

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and 0 < len(v) < 512 and ("/" in v or "." in v) and "\n" not in v and not v.startswith(("http://", "https://")):
            if "/" not in v and " " in v:
                return
            p = Path(v) if os.path.isabs(v) else project_dir / v
            try:
                if p.is_file():
                    seen.setdefault(str(p.resolve()), p.resolve())
            except OSError:
                pass

    walk(json.loads(tl.model_dump_json()))
    from . import profiles
    g = profiles.of(tl).data.grade
    if g is not None and getattr(g, "lut", None):
        lut = config.PROFILES_DIR / g.lut
        if lut.is_file():
            seen.setdefault(str(lut.resolve()), lut.resolve())
    return [seen[k] for k in sorted(seen)]


def font_files() -> list[Path]:
    if not config.FONTS_DIR.is_dir():
        return []
    return sorted(p for p in config.FONTS_DIR.rglob("*") if p.is_file())


def load(out_dir: Path) -> dict | None:
    f = Path(out_dir) / MANIFEST
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text())
    except json.JSONDecodeError:
        return None
    return d if d.get("version") == VERSION else None


def music_brief_key(tl) -> str | None:
    ac = tl.audio_chain
    if ac.music_brief is not None:
        return blake3_json({"brief": ac.music_brief.model_dump(mode="json")})
    if ac.bgm_path:
        return blake3_json({"bgm_path": ac.bgm_path})
    return None


def _check(e: dict, what: str, project_dir: Path | None) -> Path:
    p = resolve(e["path"], project_dir)
    if not p.is_file():
        raise EngineError(f"locked {what} {p} is gone; run `engine build --relock` to resolve sounds again")
    if sha256(p, fresh=True) != e["sha256"]:
        raise EngineError(f"locked {what} {p} changed (sha256 differs from out/{MANIFEST}); "
                          f"run `engine build --relock` to resolve sounds again")
    return p


def locked_sounds(lock: dict | None, tl, kinds: list[str], project_dir: Path | None = None) -> dict | None:
    """{"music": (path, provenance) | None, "sfx": {kind: (path, provenance)}} from a manifest whose sound lock
    covers this timeline (same music brief, every SFX kind used); None when there is no usable lock."""
    if not lock or "sounds" not in lock:
        return None
    s = lock["sounds"]
    key = music_brief_key(tl)
    m = s.get("music")
    if (m or {}).get("brief_key") != key or (key is None) != (m is None):
        return None
    if any(k not in s.get("sfx", {}) for k in kinds):
        return None
    out = {"music": None if m is None else (_check(m, "music", project_dir), m["provenance"]), "sfx": {}}
    for k in kinds:
        e = s["sfx"][k]
        out["sfx"][k] = (_check(e, f"SFX '{k}'", project_dir), e["provenance"])
    return out


def sounds_entry(tl, mix, kinds_of: dict[str, str], project_dir: Path | None = None) -> dict:
    """Sound lock of a mix plan. kinds_of: asset_id -> SFX kind."""
    if mix is None:
        return {"music": None, "sfx": {}}
    music = None
    if mix.music is not None:
        music = {"brief_key": music_brief_key(tl), **entry(mix.music.path, project_dir), "provenance": mix.music.provenance}
    sfx = {kinds_of[aid]: {**entry(r.path, project_dir), "provenance": r.provenance} for aid, r in mix.sounds.items()
           if aid in kinds_of}
    return {"music": music, "sfx": sfx}


def write(out_dir: Path, tl, project_dir: Path, *, mix, kinds_of: dict[str, str], budget, relock: bool) -> dict:
    from . import profiles
    from .mixer import LOCAL_TONES
    prof = profiles.of(tl)
    tf = project_dir / "master_timeline.json"
    doc = {
        "version": VERSION,
        "build_id": uuid.uuid4().hex,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "relocked": relock,
        "timeline": entry(tf, project_dir) if tf.is_file() else None,
        "profile": {"name": prof.name, "blake3": prof.blake3},
        "inputs": [entry(p, project_dir) for p in timeline_files(tl, project_dir)],
        "fonts": [entry(p, project_dir) for p in font_files()],
        "sounds": sounds_entry(tl, mix, kinds_of, project_dir),
        "toolchain": toolchain(),
        "seeds": {f"local_tone_{k}": int(v.split("seed=")[1].split(":")[0]) for k, v in LOCAL_TONES.items()
                  if "seed=" in v},
        "resources": budget.report(),
        "stage_keys": {},
    }
    save(out_dir, doc)
    return doc


def save(out_dir: Path, doc: dict) -> Path:
    f = Path(out_dir) / MANIFEST
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    tmp.replace(f)
    return f


def cmd_key(kind: str, cmd: list, extra: dict | None = None) -> str:
    """Cache key of an ffmpeg command: argv with every existing input file replaced by its sha256 (paths of
    outputs and scratch dirs drop out), plus `extra` (e.g. the ffmpeg version)."""
    parts = []
    for c in cmd:
        s = str(c)
        if os.path.isabs(s) and os.path.isfile(s):
            parts.append("sha256:" + sha256(s))
        else:
            parts.append(s)
    return blake3_json({"kind": kind, "cmd": parts, "ffmpeg": ffmpeg_version(), **(extra or {})})
