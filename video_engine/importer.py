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
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .util import EngineError, probe

STILL_PRESETS = ("ken_burns_zoom_in", "ken_burns_zoom_out")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


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
    shots, credits = [], {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            m = re.fullmatch(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", r.get("on_screen_s", ""))
            if not m:
                raise EngineError(f"shot {r.get('shot')!r}: on_screen_s {r.get('on_screen_s')!r} is not 'a-b'")
            shots.append(Shot(r["shot"], r.get("type", "source"), r["file"], r.get("title", ""), float(m[1]), float(m[2])))
            credits.setdefault(r["file"], {"source_url": r.get("source_url", "").strip(), "license": r.get("licence", ""),
                                           "author": r.get("author", "")})
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


def import_shots(csv_path: Path, dst: Path, search: list[Path], *, credits_txt: Path, voiceover: Path, fps: int = 30,
                 manifest: Path | None = None, substitute: dict[str, str] | None = None, exclude: set[str] | None = None,
                 watermark_checked: bool = False, settings: dict | None = None, force: bool = False) -> dict:
    substitute, exclude = substitute or {}, exclude or set()
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
    total = round(float(probe(voiceover)["format"]["duration"]) * fps)
    vo_rel = "assets/" + voiceover.name
    shutil.copyfile(voiceover, dst / vo_rel)
    shutil.copyfile(credits_txt, dst / "credits.txt")
    credits, segs, log = {}, [], []
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
        aid = _asset_id(f)
        rel = f"assets/{f}"
        if not (dst / rel).exists():
            shutil.copyfile(src, dst / rel)
        credits[aid] = {"asset_id": aid, "source_url": cred["source_url"], "license": norm_licence_text(cred["license"]),
                        "path": rel, "author": cred["author"] or None, "watermark_free": True if watermark_checked else None}
        a = round(s.t0 * fps) if i else 0
        b = round(kept[i + 1].t0 * fps) if i + 1 < len(kept) else total
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
    (dst / "master_timeline.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return {"segments": len(segs), "frames": total, "assets": len(credits), "substitutions": log,
            "excluded": sorted(exclude)}
