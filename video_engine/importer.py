"""`engine import-shots`: builds an engine project from a shot-list CSV (edit decision list) of an existing edit.

Input CSV columns: shot, type (source|map), file, title, source_url, author, licence, on_screen_s ("a-b" seconds).
Files are located by name under the search directories and copied into <dst>/assets/. Sources are read only.
Credits come only from CSV rows or manifest rows (`file,source_url,licence,author`); the importer never writes a
URL that is not in one of them. `--substitute OLD=NEW` swaps a file for one whose credit is taken from the
manifest; `--exclude SHOT` merges a shot into the previous one (its time goes to the previous shot).
Map rows become HyperFrames map segments; stills alternate ken_burns_zoom_in / zoom_out; videos start at frame 0.
"""
from __future__ import annotations

import csv
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import timeline as timeline_mod
from .util import EngineError, probe

STILL_PRESETS = ("ken_burns_zoom_in", "ken_burns_zoom_out")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SHOT_KINDS = {"source", "map"}


@dataclass
class Shot:
    shot: str
    kind: str
    file: str
    title: str
    t0: float
    t1: float


def _asset_id(file: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", Path(file).stem.lower()).strip("_")


def norm_licence_text(s: str) -> str:
    """Own-work variants ('own work (...)', 'own composite of PD ...') -> 'Own'; others unchanged."""
    return "Own" if s.strip().lower().startswith("own") else s.strip()


def read_shots(csv_path: Path) -> tuple[list[Shot], dict[str, dict]]:
    shots, credits, seen = [], {}, set()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"shot", "file", "on_screen_s"} - set(reader.fieldnames or [])
        if missing:
            raise EngineError(f"{csv_path}: missing CSV column(s): {', '.join(sorted(missing))}")
        for line, r in enumerate(reader, start=2):
            shot, file = (r.get("shot") or "").strip(), (r.get("file") or "").strip()
            kind = (r.get("type") or "source").strip() or "source"
            if not shot or not file:
                raise EngineError(f"{csv_path}:{line}: shot and file are required")
            if shot in seen:
                raise EngineError(f"{csv_path}:{line}: duplicate shot {shot!r}")
            if kind not in SHOT_KINDS:
                raise EngineError(f"{csv_path}:{line}: type {kind!r} is not one of {', '.join(sorted(SHOT_KINDS))}")
            m = re.fullmatch(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", r.get("on_screen_s", ""))
            if not m:
                raise EngineError(f"shot {shot!r}: on_screen_s {r.get('on_screen_s')!r} is not 'a-b'")
            t0, t1 = float(m[1]), float(m[2])
            if not (math.isfinite(t0) and math.isfinite(t1)) or t0 < 0 or t1 <= t0:
                raise EngineError(f"shot {shot!r}: on_screen_s {t0:g}-{t1:g} needs 0 <= start < end")
            seen.add(shot)
            shots.append(Shot(shot, kind, file, r.get("title", ""), t0, t1))
            credits.setdefault(file, {"source_url": r.get("source_url", "").strip(), "license": r.get("licence", ""),
                                      "author": r.get("author", "")})
    if not shots:
        raise EngineError(f"{csv_path}: no shots")
    return shots, credits


def read_manifest(path: Path | None) -> dict[str, dict]:
    out = {}
    if path and path.is_file():
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[Path(r["file"]).name] = {"source_url": r.get("source_url", "").strip(), "license": r.get("licence", ""),
                                             "author": r.get("author", "")}
    return out


def _find(name: str, dirs: list[Path]) -> Path:
    for d in dirs:
        hits = sorted(d.rglob(name))
        if hits:
            return hits[0]
    raise EngineError(f"file {name!r} not found under {', '.join(map(str, dirs))}")


def _asset_for(f: str, used: dict[str, str]) -> str:
    base = _asset_id(f) or "asset"
    aid, n = base, 2
    while aid in used and used[aid] != f:
        aid, n = f"{base}_{n}", n + 1
    used[aid] = f
    return aid


def import_shots(csv_path: Path, dst: Path, search: list[Path], *, credits_txt: Path, voiceover: Path, fps: int = 30,
                 manifest: Path | None = None, substitute: dict[str, str] | None = None, exclude: set[str] | None = None,
                 watermark_checked: bool = False, settings: dict | None = None, force: bool = False) -> dict:
    substitute, exclude = substitute or {}, exclude or set()
    if fps < 1:
        raise EngineError(f"fps {fps} must be at least 1")
    if (dst / "master_timeline.json").exists() and not force:
        raise EngineError(f"{dst}/master_timeline.json exists; pass --force to overwrite")
    shots, row_credits = read_shots(csv_path)
    man = read_manifest(manifest)
    unknown = exclude - {s.shot for s in shots}
    if unknown:
        raise EngineError(f"--exclude names unknown shots: {sorted(unknown)}")
    if shots and shots[0].shot in exclude:
        raise EngineError("the first shot cannot be excluded (its time has no previous shot)")
    kept: list[Shot] = []
    for s in shots:
        if s.shot in exclude:
            kept[-1].t1 = s.t1
        else:
            kept.append(s)
    (dst / "assets").mkdir(parents=True, exist_ok=True)
    try:
        duration = float(probe(voiceover)["format"]["duration"])
    except (OSError, KeyError, TypeError, ValueError) as e:
        raise EngineError(f"cannot read voiceover duration from {voiceover}: {e}") from None
    total = round(duration * fps)
    if total <= 0:
        raise EngineError(f"voiceover {voiceover} has no frames at {fps} fps")
    for s in kept:
        if round(s.t1 * fps) > total:
            raise EngineError(f"shot {s.shot}: on_screen_s ends at {s.t1:g} s, after the {duration:g} s voiceover")
    vo_rel = "assets/" + voiceover.name
    shutil.copyfile(voiceover, dst / vo_rel)
    credits, segs, log, used = {}, [], [], {}
    still_i = 0
    for i, s in enumerate(kept):
        f = substitute.get(s.file, s.file)
        if f != s.file:
            if f not in man:
                raise EngineError(f"substitute {f!r} has no manifest row; its credit cannot be sourced")
            cred = man[f]
            log.append(f"{s.shot}: {s.file} -> {f} (credit from manifest)")
        else:
            cred = row_credits[s.file]
        if not cred["source_url"]:
            raise EngineError(f"{s.shot}: {f!r} has no source_url in the shot list or manifest; add it there")
        src = _find(f, search)
        aid = _asset_for(f, used)
        rel = f"assets/{f}"
        if not (dst / rel).exists():
            shutil.copyfile(src, dst / rel)
        credits[aid] = {"asset_id": aid, "source_url": cred["source_url"], "license": norm_licence_text(cred["license"]),
                        "path": rel, "author": cred["author"] or None, "watermark_free": True if watermark_checked else None}
        a = round(s.t0 * fps) if i else 0
        b = round(kept[i + 1].t0 * fps) if i + 1 < len(kept) else total
        if b <= a:
            raise EngineError(f"shot {s.shot}: timestamps leave no frames ({a}-{b})")
        seg = {"seg_index": i, "start_frame": a, "end_frame": b, "source_in_frame": 0, "overlays": []}
        if s.kind == "map":
            seg |= {"engine_type": "HYPERFRAMES_GSAP", "gsap_scene_config": {
                "template": "map_pins", "map_region": s.title or aid, "map_asset": rel, "map_asset_id": aid,
                "pins": [], "cards": [], "camera_preset": "zoom_in", "camera_intensity": 1.0}}
        else:
            key = "source_video" if Path(f).suffix.lower() in VIDEO_EXT else "source_image"
            seg |= {"engine_type": "NATIVE_RUST_FFMPEG", key: rel, "asset_id": aid}
            preset = "ken_burns_zoom_in"
            if key == "source_image":
                preset = STILL_PRESETS[still_i % 2]
                still_i += 1
            seg["camera_move"] = {"preset": preset, "intensity": 1.0, "auto_intensity": True}
        segs.append(seg)
    doc = {"project_id": dst.name, "settings": {"width": 1080, "height": 1920, "fps": fps, **(settings or {})},
           "credits_manifest": list(credits.values()),
           "audio_chain": {"voiceover_path": vo_rel, "auto_ducking": True, "language": "id", "whisper_model": "small", "auto_captions": True},
           "segments": segs}
    try:
        tl = timeline_mod.Timeline.model_validate(doc)
    except ValueError as e:
        raise EngineError(f"imported timeline is invalid: {e}") from None
    text = credits_txt.read_text(encoding="utf-8")
    urls = {c["source_url"] for c in credits.values()}
    missing = [u for u in sorted(urls) if u not in text]
    if missing:
        text = text.rstrip("\n") + "\n" + "\n".join(missing) + "\n"
    (dst / "credits.txt").write_text(text, encoding="utf-8")
    timeline_mod.save(tl, dst)
    return {"segments": len(segs), "frames": total, "assets": len(credits), "substitutions": log,
            "excluded": sorted(exclude)}
