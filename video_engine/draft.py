"""`engine draft`: an AI provider drafts a master timeline from narration audio, script, credits.txt and the assets.

Flow: inputs (voiceover, script, credits.txt lines, asset inventory with metadata, Rust pause analysis, pinned
STT words) -> one planning request (narration audio, optional video proxies, asset thumbnails, prompt; parts
the provider cannot take are degraded by `ai.base.degrade`) -> assemble a Timeline -> validate + pre-flight gates ->
on failure feed the errors back (max_repairs rounds) -> save the gate-fixed draft + draft_report.json. Provider
uploads are deleted after the last round and each deletion is verified. Source URLs, licences and authors are
copied from credits.txt lines; the model only picks a line number. Agent provider: one bundle per round under
<project>/ai_request/draft/<round>/; earlier rounds are replayed from their response.json on resume."""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import hypoverum_core_rs as core
from pydantic import ValidationError

from . import ai
from .ai import tasks
from .ai.base import AwaitingAgent, Image, Media, Text
from . import assets, audio, config, draft_scene, geodata, pauses, profiles, recipes
from .hyperframes import HF_PRESETS
from .licence import is_licence_field
from .timeline import (HYPERFRAMES, IMAGE_EXT, NATIVE, ROLL_MAX, AudioChain, CaptionWord, CaptionsMeta, Settings, Timeline,
                       sec_to_frame)
from .util import EngineError, video_info
from . import framing
from .units import units

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
THUMB_PX = 384
VIDEO_THUMBS = 3
PROXY_SECONDS = 20.0
DEFAULT_REPAIRS = 2
URL_RE = re.compile(r"https?://\S+")
# Overlay slots at the reference canvas (units.py; scaled to the output size): kind -> position -> [x, y, w, h].
# OVERLAY_SLOTS: portrait 1080x1920; OVERLAY_SLOTS_LANDSCAPE: 1920x1080 (inside the 96/54 px landscape safe area).
OVERLAY_SLOTS = {
    "text_card": {"top": [90, 230, 900, 300], "bottom": [90, 1420, 900, 300]},
    "label": {"top": [190, 260, 700, 110], "bottom": [190, 1560, 700, 110]},
    "lower_third": {"top": [90, 230, 800, 180], "bottom": [90, 1500, 800, 180]},
}
OVERLAY_SLOTS_LANDSCAPE = {
    "text_card": {"top": [96, 96, 760, 180], "bottom": [96, 790, 760, 180]},
    "label": {"top": [96, 120, 520, 80], "bottom": [96, 880, 520, 80]},
    "lower_third": {"top": [96, 96, 760, 140], "bottom": [96, 820, 760, 140]},
}


@dataclass
class CreditLine:
    n: int                 # 1-based line number among URL lines
    text: str
    url: str
    license: str
    author: str | None
    title: str | None


@dataclass
class AssetInfo:
    id: str
    path: str              # project-relative
    kind: str              # image | video
    width: int
    height: int
    duration_s: float | None = None
    hints: list[str] = field(default_factory=list)
    credit_line: int | None = None   # deterministic match (manifest / sidecar / existing timeline URL)
    map_bounds: list[float] | None = None   # sidecar "map_bounds" [south, west, north, east]: lat/lng pins on a flat map
    map_projection: str | None = None       # sidecar "projection": equirectangular (default) or mercator


@dataclass
class Inputs:
    pdir: Path
    project_id: str
    settings: Settings
    audio_chain: AudioChain
    caption_style: dict
    script: str | None
    credit_lines: list[CreditLine]
    assets: list[AssetInfo]
    pause_report: pauses.PauseReport
    words: list[CaptionWord]
    words_meta: CaptionsMeta
    total_frames: int
    profile: str | None = None
    profile_overrides: dict = field(default_factory=dict)   # the existing timeline's project values over the profile


def _profile(inp: Inputs) -> profiles.Profile:
    """Effective profile of the draft: the channel profile (default when none) with the project's overrides, the
    same one the drafted Timeline validates against."""
    return profiles.load(inp.profile, inp.profile_overrides)


# ---------------------------------------------------------------------------------------------------------- inputs
def _norm_url(u: str) -> str:
    return unquote(u.strip().rstrip(".,;)")).replace(" ", "_").lower()


def parse_credits(text: str) -> list[CreditLine]:
    """Every credits.txt line with an http(s) URL. Fields split on ' | ' or ' — '; licence = the allow-listed
    (or CC-*) field; author = the field after the title."""
    out = []
    for line in text.splitlines():
        m = URL_RE.search(line)
        if not m:
            continue
        url = m.group(0).rstrip(".,;)")
        body = line.strip().lstrip("-*• ").strip()
        parts = [p.strip() for p in re.split(r"\s+[|—]\s+", body) if p.strip()]
        rest = [p for p in parts if not URL_RE.search(p)]
        lic = next((p for p in rest if is_licence_field(p)), None)
        others = [p for p in rest if p != lic]
        out.append(CreditLine(len(out) + 1, body, url, lic or "unknown", others[1] if len(others) > 1 else None,
                              others[0] if others else None))
    return out


def _find_voiceover(pdir: Path, explicit: str | None, ac: AudioChain) -> str:
    if explicit:
        return explicit
    if ac.voiceover_path:
        return ac.voiceover_path
    cands = [p for p in sorted(pdir.glob("*")) + sorted((pdir / "assets").glob("*"))
             if p.suffix.lower() in AUDIO_EXT and re.search(r"narasi|narration|voice|vo\b|vo\.", p.name.lower())]
    if not cands:
        raise EngineError("no voiceover: pass --voiceover or set audio_chain.voiceover_path")
    return str(cands[0].relative_to(pdir))


def _find_script(pdir: Path, explicit: str | None) -> str | None:
    for p in ([Path(explicit)] if explicit else [pdir / n for n in ("script.txt", "narasi.txt", "narration.txt")]):
        p = p if p.is_absolute() else pdir / p
        if p.is_file():
            return p.read_text(errors="replace").strip()
    return None


def _manifest_rows(pdir: Path) -> dict[str, dict]:
    rows = {}
    for mf in [pdir / "assets" / "manifest.csv", pdir / "manifest.csv"]:
        if mf.is_file():
            with mf.open(newline="", encoding="utf-8", errors="replace") as fh:
                for r in csv.DictReader(fh):
                    if r.get("file"):
                        rows[Path(r["file"]).name] = r
    return rows


def _sidecar(p: Path) -> dict:
    for sc in (p.with_suffix(".json"), p.with_name(p.name + ".json")):
        if sc.is_file():
            try:
                d = json.loads(sc.read_text(errors="replace"))
                return d if isinstance(d, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _asset_id(p: Path, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", p.stem).strip("_").lower()[:40] or "asset"
    aid, i = base, 2
    while aid in taken:
        aid, i = f"{base}_{i}", i + 1
    taken.add(aid)
    return aid


def inventory(pdir: Path, credit_lines: list[CreditLine], exclude: set[str], existing: Timeline | None) -> list[AssetInfo]:
    """Images and videos under assets/ (recursive), with size, duration, manifest/sidecar hints and the credit line
    whose URL matches the manifest, sidecar or existing timeline credit (None = model must pick)."""
    by_url = {_norm_url(c.url): c.n for c in credit_lines}
    rows = _manifest_rows(pdir)
    old_urls = {Path(c.path).name: c.source_url for c in (existing.credits_manifest if existing else []) if c.path}
    out, taken = [], set()
    for p in sorted((pdir / "assets").rglob("*")):
        rel = str(p.relative_to(pdir))
        ext = p.suffix.lower()
        if not p.is_file() or rel in exclude or ext not in IMAGE_EXT | VIDEO_EXT:
            continue
        try:
            if ext in IMAGE_EXT:
                from .native import image_size
                w, h = image_size(p)
                a = AssetInfo(_asset_id(p, taken), rel, "image", w, h)
            else:
                vi = video_info(p)
                a = AssetInfo(_asset_id(p, taken), rel, "video", vi["width"], vi["height"], round(vi["duration"], 2))
        except Exception:   # unreadable media is not offered to the model
            continue
        row, sc = rows.get(p.name, {}), _sidecar(p)
        for k in ("scene_or_line", "narration_excerpt", "notes"):
            if row.get(k):
                a.hints.append(f"{k}: {row[k][:160]}")
        for k in ("title", "description", "objectname"):
            if isinstance(sc.get(k), str) and sc[k].strip():
                a.hints.append(f"{k}: {re.sub(r'<[^>]+>', '', sc[k])[:160]}")
        mb = sc.get("map_bounds")
        if isinstance(mb, list) and len(mb) == 4 and all(isinstance(v, (int, float)) for v in mb):
            a.map_bounds = [float(v) for v in mb]
            a.hints.append(f"map_bounds [south, west, north, east]: {a.map_bounds}")
            if sc.get("projection") in ("equirectangular", "mercator"):
                a.map_projection = sc["projection"]
                a.hints.append(f"projection: {a.map_projection}")
        for u in (row.get("source_url"), sc.get("url") if isinstance(sc.get("url"), str) else None,
                  sc.get("source_url") if isinstance(sc.get("source_url"), str) else None, old_urls.get(p.name)):
            if u and _norm_url(u) in by_url:
                a.credit_line = by_url[_norm_url(u)]
                break
        out.append(a)
    return out


def collect(pdir: Path, voiceover: str | None = None, script: str | None = None, use_cache: bool = True,
            profile: str | None = None) -> Inputs:
    """profile: channel profile name; default = the existing timeline's profile, else the default profile."""
    pdir = pdir.resolve()
    existing = None
    if (pdir / "master_timeline.json").is_file():
        from . import timeline
        existing = timeline.load(pdir)
    profile = profile or (existing.profile if existing else None)
    dp = profiles.load(profile or profiles.DEFAULT).draft
    settings = existing.settings if existing else Settings()
    ac = existing.audio_chain.model_copy(deep=True) if existing else AudioChain()
    ac.voiceover_path = _find_voiceover(pdir, voiceover, ac)
    if not (pdir / ac.voiceover_path).is_file():
        raise EngineError(f"voiceover not found: {ac.voiceover_path}")
    ctxt = pdir / "credits.txt"
    if not ctxt.is_file():
        raise EngineError("credits.txt missing in project dir (the draft copies URLs from it)")
    lines = parse_credits(ctxt.read_text(errors="replace"))
    exclude = {ac.voiceover_path}
    if ac.bgm_path:
        exclude.add(ac.bgm_path)
    assets = inventory(pdir, lines, exclude, existing)
    if not assets:
        raise EngineError("no images or videos under assets/")
    fps = settings.fps
    rep = pauses.analyze(pdir / ac.voiceover_path, fps, min_seg_s=dp.min_seg_s, max_seg_s=dp.max_seg_s)
    total = rep.total_frames + fps // 2
    a0 = assets[0]
    probe = Timeline(project_id="draft", settings=settings, audio_chain=ac, segments=[{
        "seg_index": 0, "engine_type": NATIVE, "start_frame": 0, "end_frame": total,
        ("source_image" if a0.kind == "image" else "source_video"): a0.path}])
    words, meta = audio.transcribe_captions(probe, pdir, use_cache=use_cache)
    pid = existing.project_id if existing else config.safe_project_id(pdir.name)
    style = {k: v for k, v in (existing.caption_style if existing else {}).items() if k != "y_overrides"}
    return Inputs(pdir, pid, settings, ac, style, _find_script(pdir, script), lines, assets, rep, words, meta, total,
                  profile, dict(existing.profile_overrides) if existing else {})


# ---------------------------------------------------------------------------------------------------------- prompt
def schema(inp: Inputs) -> dict:
    ids = [a.id for a in inp.assets]
    s, n, i, b = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
    overlay = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": list(OVERLAY_SLOTS)}, "position": {"type": "string", "enum": ["top", "bottom"]},
        "text": s, "subtitle": s, "start_s": n, "end_s": n}, "required": ["kind", "position", "text"]}
    seg = {"type": "object", "properties": {
        "start_frame": i, "end_frame": i, "kind": {"type": "string", "enum": ["image", "video", "map"]},
        "asset": {"type": "string", "enum": ids}, "narration": s, "why": s, "goal": s, "source_in_s": n,
        "camera_preset": {"type": "string", "enum": core.camera_presets()}, "intensity": n,
        "focus_box": {"type": "array", "items": n},
        "intent": {"type": "object", "properties": {
            "motion": {"type": "string", "enum": ["static", "moving"]},
            "negative_space": b, "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "shot": {"type": "string", "enum": list(framing.SHOTS)}},
            "required": ["reason"], "additionalProperties": False},
        "creative_autofix": b,
        "map_title": s, "map_camera": {"type": "string", "enum": list(HF_PRESETS)},
        "map_focus": {"type": "array", "items": n},
        "map_pins": {"type": "array", "items": {"type": "object", "properties": {
            "label": s, "x": n, "y": n, "lat": n, "lng": n, "at_s": n, "flag": s,
            "label_side": {"type": "string", "enum": ["left", "right"]}}, "required": ["label"]}},
        "map_card": {"type": "object", "properties": {"title": s, "subtitle": s,
                                                      "position": {"type": "string", "enum": ["top", "bottom"]}},
                     "required": ["title"]},
        "overlays": {"type": "array", "items": overlay}},
        "required": ["start_frame", "end_frame", "kind", "asset", "narration", "why"]}
    prof = _profile(inp)
    seg["properties"].update(draft_scene.schema_props(prof.draft, prof.layout is not None, prof.transitions.map_morph))
    seg["properties"]["map_recipe"] = RECIPE_SCHEMA
    if prof.draft.map_path:
        seg["properties"]["map_path"] = {"type": "array", "items": {
            "type": "object", "properties": {"at_s": n, "x": n, "y": n, "zoom": n, "roll": n, "tilt": n,
                                             "move": {"type": "string", "enum": ["glide", "whip", "reveal", "flow", "settle"]}},
            "required": ["at_s", "x", "y"]}}
    cred = {"type": "object", "properties": {"asset": {"type": "string", "enum": ids}, "credit_line": i,
                                             "watermark_free": b}, "required": ["asset", "credit_line", "watermark_free"]}
    return {"type": "object", "properties": {"title": s, "segments": {"type": "array", "items": seg},
                                             "credits": {"type": "array", "items": cred}},
            "required": ["segments", "credits"]}


def _words_text(inp: Inputs) -> str:
    return " ".join(f"[{w.start_frame}]{w.word}" for w in inp.words)


def prompt(inp: Inputs) -> str:
    S, fps = inp.settings, inp.settings.fps
    assets = "\n".join(
        f"- {a.id}: {a.kind} {a.width}x{a.height}" + (f", {a.duration_s:.1f}s" if a.duration_s else "")
        + (f", credit line {a.credit_line} (fixed)" if a.credit_line else ", credit line: choose")
        + ("".join(f"\n    {h}" for h in a.hints)) for a in inp.assets)
    credits = "\n".join(f"{c.n}. {c.text}" for c in inp.credit_lines)
    segs = "\n".join(f"- frames {a}-{b}" for a, b in inp.pause_report.segments)
    prof = _profile(inp)
    dp, mz = prof.draft, prof.camera.max_zoom
    lo, hi = dp.min_seg_s, dp.max_seg_s
    extra = ("\n\nCHANNEL CREATIVE DIRECTION (identity, defaults and alternatives; defaults are not hard constraints)"
             + "".join(f"\n- {r}" for r in dp.rules)) if dp.rules else ""
    path_rule = ("\n   map_path (optional, instead of map_camera): camera keys {at_s, x, y, zoom, move}; the first at_s 0,"
                 "\n   then increasing; x,y 0..1 = the map point at the frame centre, zoom 1.0-" + f"{mz:.1f}" + "; the camera travels key"
                 "\n   to key inside one segment (one map segment visits several places). move = how the camera reaches"
                 "\n   the key: glide (default) or whip (fast blurred move for a far jump; the camera drifts until it); roll = optional"
                 "\n   map turn in degrees (-25..25) reached at the key."
                 if dp.map_path else "")
    scene_rule = draft_scene.prompt(prof.draft, prof.layout is not None, prof.transitions.map_morph)
    recipes_rule = RECIPE_RULE
    return f"""You are the editor of a {_format_words(S)} ({S.width}x{S.height}, {fps} fps, Indonesian narration).
Draft the visual edit for the attached narration. Total length: frames 0..{inp.total_frames} (exclusive end).

NARRATION WORDS with start frame (pinned STT transcript, authoritative timing):
{_words_text(inp)}

SCRIPT (author's text, for meaning and names):
{inp.script or '(no script file; use the words above)'}

PAUSE-BASED SUGGESTED CUTS (Rust silence analysis; cut on a pause when you can):
{segs}

ASSETS (thumbnails of every asset follow in this request, labelled by id; some videos also attached as low-res proxies):
{assets}

CREDITS.TXT LINES (numbered; you only pick numbers, never write URLs):
{credits}

RULES
1. Segments are contiguous: first start_frame 0, each start_frame = previous end_frame, last end_frame = {inp.total_frames}.
   Suggested length {lo}-{hi} s each ({round(lo * fps)}-{round(hi * fps)} frames); let the thought and readability decide.
2. Design every segment from ITS OWN narration: put the words it covers in "narration", pick the asset that shows
   what those words say, and explain the choice in "why". No template: segment lengths, camera moves, overlays and the
   number of maps follow the content, not a fixed pattern. An opener need not be a moving map; a keyword callback is optional.
   Optional intent = {{"motion":"static" or "moving", "negative_space":true/false, "reason":"specific purpose"}};
   provide a motion choice, negative_space true or a shot, and a nonblank reason. Optional intent.shot names what the
   shot communicates; the same story works for 9:16 and 16:9 and the engine frames it for this frame:
{framing.describe(units(S).orientation, prof.data.framing)}
   Intent is local to that segment, not a waiver
   of black frames, broken sources or factual integrity. Static intent describes a deliberate hold; set camera_preset
   (or map_camera) to static and do not add moving camera paths for a still shot. creative_autofix is false by default;
   set true only when camera-intensity adjustment has been explicitly requested, never to silence a warning.
3. kind image -> camera_preset (and intensity 0.6-2.0; use a focus_box [ymin,xmin,ymax,xmax] 0..1000 on the subject
   you saw in the thumbnail when the move should end on it). kind video -> source_in_s within the clip duration minus
   the segment length; camera_preset optional. kind map -> only for a map asset; map_pins x,y are 0..1 positions
   on the map image you saw (place each pin on the place it names), at_s relative to the segment start;
   map_camera from the enum (intensity 0.3-2.0 scales its zoom and travel); optional map_card.{path_rule}{scene_rule}{recipes_rule}
4. Overlays (optional, images/videos only): kind text_card (title + subtitle), label (short name), lower_third
   (person + role); position top or bottom. Text adds information not spoken in that segment (date, place, name);
   never repeat the spoken words. At most one overlay per segment; 2-6 words title.
5. credits: one entry per asset you use; credit_line = the matching numbered line above (use the fixed number
   when given); watermark_free = false if you see a stock watermark or channel logo in the thumbnail.{extra}
Return JSON only, matching the schema."""


def _thumb_jpeg(path: Path, kind: str, duration: float | None) -> list[bytes]:
    from io import BytesIO

    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    if kind == "image":
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_PX, THUMB_PX))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=80)
            return [buf.getvalue()]
    outs = []
    for k in range(VIDEO_THUMBS):
        t = (duration or 1.0) * (k + 0.5) / VIDEO_THUMBS
        r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                            f"scale='min({THUMB_PX},iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            outs.append(r.stdout)
    return outs


AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac",
              ".ogg": "audio/ogg", ".aac": "audio/aac"}


def media_parts(inp: Inputs, video_proxies: int, workdir: Path) -> list:
    """Narration audio, up to `video_proxies` 360p proxies (first PROXY_SECONDS s) and thumbnails of every asset."""
    vo = inp.pdir / inp.audio_chain.voiceover_path
    words = [{"word": w.word, "start_frame": w.start_frame, "end_frame": w.end_frame} for w in inp.words]
    parts: list = [Text("NARRATION AUDIO:"), Media(vo, "audio", AUDIO_MIME[vo.suffix.lower()], "narration audio",
                                                   transcript=_words_text(inp), words=words)]
    for a in [a for a in inp.assets if a.kind == "video"][:video_proxies]:
        secs = min(PROXY_SECONDS, a.duration_s or PROXY_SECONDS)
        px = tasks.make_proxy(inp.pdir / a.path, workdir / "proxy" / f"{a.id}.mp4", 0.0, secs)
        parts += [Text(f"VIDEO PROXY {a.id} (first {secs:.0f} s):"), Media(px, "video", "video/mp4", f"proxy {a.id}")]
    for a in inp.assets:
        for k, jpg in enumerate(_thumb_jpeg(inp.pdir / a.path, a.kind, a.duration_s)):
            label = f"THUMBNAIL {a.id}" + (f" frame {k + 1}/{VIDEO_THUMBS}" if a.kind == "video" else "")
            parts += [Text(label), Image(jpg, "image/jpeg", label)]
    return parts


def attachments(inp: Inputs) -> dict:
    """Request files for the agent bundle: asset inventory and numbered credits.txt lines."""
    return {"inventory.json": [{"id": a.id, "path": a.path, "kind": a.kind, "width": a.width, "height": a.height,
                                "duration_s": a.duration_s, "credit_line": a.credit_line, "hints": a.hints}
                               for a in inp.assets],
            "credits.json": [{"n": c.n, "text": c.text} for c in inp.credit_lines]}


# -------------------------------------------------------------------------------------------------------- assemble
def _format_words(S) -> str:
    """How the draft prompt names the format (units.py orientation)."""
    return "landscape 16:9 documentary video" if units(S).landscape else "vertical documentary short"


def _slot(kind: str, pos: str, S: Settings) -> list[float]:
    u = units(S)
    x, y, w, h = (OVERLAY_SLOTS_LANDSCAPE if u.landscape else OVERLAY_SLOTS)[kind][pos]
    kx, ky = u.kx, u.ky
    return [round(x * kx), round(y * ky), round(w * kx), round(h * ky)]


def normalise(segs: list[dict], total: int) -> tuple[list[dict], list[str]]:
    """Sorted, contiguous 0..total. Records every change it makes."""
    notes = []
    segs = sorted(segs, key=lambda s: (int(s.get("start_frame", 0)), int(s.get("end_frame", 0))))
    out, prev = [], 0
    for k, s in enumerate(segs):
        a, b = int(s["start_frame"]), int(s["end_frame"])
        if a != prev:
            notes.append(f"segment {k}: start {a} -> {prev} (contiguity)")
        a = prev
        if k == len(segs) - 1 and b != total:
            notes.append(f"segment {k}: end {b} -> {total} (timeline end)")
            b = total
        b = min(b, total)
        if b <= a:
            notes.append(f"segment {k}: dropped (empty after normalisation)")
            continue
        out.append({**s, "start_frame": a, "end_frame": b})
        prev = b
    if out and out[-1]["end_frame"] != total:
        notes.append(f"segment {len(out) - 1}: end {out[-1]['end_frame']} -> {total} (timeline end)")
        out[-1]["end_frame"] = total
    return out, notes


def _camera_path(keys: list[dict], n: int, fps: int, max_zoom: float = 4.0) -> list[dict]:
    """Model map_path -> camera_path keys: frames from at_s (first key at 0, strictly increasing, < n), x/y and zoom
    clamped to their ranges, move "whip" or "reveal" kept (glide otherwise). Raises ValueError when fewer than 2 distinct key frames remain."""
    out = []
    for k in sorted(keys, key=lambda k: float(k.get("at_s") or 0.0)):
        f = 0 if not out else min(n - 1, max(0, sec_to_frame(float(k.get("at_s") or 0.0), fps)))
        if out and f <= out[-1]["at_frame"]:
            continue
        out.append({"at_frame": f, "focus": [min(1.0, max(0.0, float(k["x"]))), min(1.0, max(0.0, float(k["y"])))],
                    "zoom": min(max_zoom, max(1.0, float(k.get("zoom") or 1.0))),
                    **({"move": k["move"]} if k.get("move") in ("whip", "reveal", "flow", "settle") and out else {}),
                    **({"tilt": min(45.0, max(0.0, float(k["tilt"])))} if k.get("tilt") else {}),
                    **({"roll": min(ROLL_MAX, max(-ROLL_MAX, float(k["roll"])))} if k.get("roll") else {})})
    if len(out) < 2:
        raise ValueError(f"map_path needs at least 2 keys at distinct times inside the segment, got {keys!r}")
    return out


_PLACE = {"type": "object", "properties": {"label": {"type": "string"}, "lat": {"type": "number"},
                                          "lng": {"type": "number"}, "value": {"type": "number"},
                                          "unit": {"type": "string"}, "decimals": {"type": "integer"}},
          "required": ["lat", "lng"]}
_LL = {"type": "array", "items": {"type": "number"}}
RECIPE_SCHEMA = {"type": "object", "properties": {
    "split": {"type": "boolean"},
    "pattern": {"type": "string", "enum": list(recipes.PATTERNS)},
    "countries": {"type": "array", "items": {"type": "string"}}, "labels": {"type": "array", "items": {"type": "string"}},
    "points": {"type": "array", "items": _LL}, "paths": {"type": "array", "items": {"type": "array", "items": _LL}},
    "a": _PLACE, "b": _PLACE, "place": _PLACE, "value": {"type": "number"}, "unit": {"type": "string"},
    "decimals": {"type": "integer"}, "source": {"type": "string"}, "label": {"type": "string"},
    "legend": {"type": "string"}, "legend_note": {"type": "string"}, "at_s": {"type": "number"},
    "speed_px": {"type": "number"},
    "draw_s": {"type": "number"}}, "required": ["pattern"]}
RECIPE_RULE = """
   map_recipe (optional, kind map; needs a map asset with map_bounds): name the story pattern and its facts, the engine
   computes geometry, camera, timing and text placement: country_reveal {countries: ISO codes, labels}; follow_route
   {points: [[lat, lng], ...] start -> end, labels: [start, end]}; compare_places {a, b: {label, lat, lng, value, unit,
   decimals}, source, split (true = two stacked panels, one camera each)}; explain_flow {paths: [[[lat, lng], ...],
   ...], labels (one per path: a colour legend), legend, legend_note}; stat_emphasis {value, unit, decimals, source,
   label, place {label, lat, lng}}; at_s = when it starts. Do not also give the map_* fields it fills
   (camera, regions, routes, pins, figures, cards). Values you cannot source are shown as illustrative."""
RECIPE_FIELDS = {"camera_path": "map_path", "regions": "map_regions", "routes": "map_routes", "pins": "map_pins",
                 "figures": "map_figures", "cards": "map_card", "flows": "map_flows"}


def _recipe(s: dict, i: int, a, inp: Inputs, n: int, prof) -> recipes.Built:
    """Draft map_recipe -> recipes.Built on this segment's map (bounds and projection from the map's sidecar)."""
    path = f"segment {i}: map_recipe"
    if not a.map_bounds:
        raise ValueError(f"{path}: the map {a.id} has no sidecar map_bounds; a recipe places lat/lng on the map")
    for target, key in RECIPE_FIELDS.items():
        if s.get(key):
            raise ValueError(f"{path}: drop {key}; the recipe fills {target}")
    m = recipes.MapInfo(a.path, a.id, a.width, a.height, a.map_bounds, a.map_projection or "equirectangular")
    f = recipes.Frame(inp.settings.width, inp.settings.height, inp.settings.fps, n, prof.camera.max_zoom)
    r = s["map_recipe"]
    try:
        geo = geodata.admin0() if isinstance(r, dict) and r.get("pattern") in recipes.GEO_PATTERNS else None
        return recipes.compile(r, m, f, geojson=geo)
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{path}: {e}") from None


PIN_KEYS = ("label", "x", "y", "lat", "lng", "at_s", "flag", "label_side")


def _pin(p: dict, path: str, n: int, fps: int) -> dict:
    """Model map pin -> Pin input. x/y clamped to 0..1 and at_s to the segment (the old behaviour); unknown keys and
    an unknown flag code fail with the pin's location."""
    draft_scene._keys(path, p, PIN_KEYS)
    d = {"label": p.get("label")}
    if p.get("x") is not None and p.get("y") is not None:
        d.update(x=min(1.0, max(0.0, float(p["x"]))), y=min(1.0, max(0.0, float(p["y"]))))
    for k in ("lat", "lng", "label_side"):
        if p.get(k) is not None:
            d[k] = p[k]
    if p.get("at_s") is not None:
        d["at_frame"] = min(n - 1, max(0, sec_to_frame(p["at_s"], fps)))
    if p.get("flag"):
        d["flag"] = draft_scene._flag(f"{path}.flag", p["flag"])
    return d


def assemble(inp: Inputs, data: dict) -> tuple[Timeline, list[str], list[dict]]:
    """Model JSON -> (Timeline, normalisation notes, per-segment rationale). Raises ValueError / ValidationError with a message fit for the repair prompt."""
    S, fps = inp.settings, inp.settings.fps
    by_id = {a.id: a for a in inp.assets}
    prof = _profile(inp)
    max_zoom, layout_on = prof.camera.max_zoom, prof.layout is not None
    seg_keys = set(schema(inp)["properties"]["segments"]["items"]["properties"]) | set(draft_scene.FIELDS)   # gated
                                                                         # scene keys: draft_scene names the profile
    lines = {c.n: c for c in inp.credit_lines}
    segs, notes = normalise(list(data.get("segments") or []), inp.total_frames)
    if not segs:
        raise ValueError("no segments")
    out, used = [], []
    for i, s in enumerate(segs):
        a = by_id.get(s.get("asset"))
        if a is None:
            raise ValueError(f"segment {i}: unknown asset {s.get('asset')!r}")
        used.append(a.id)
        unknown = sorted(set(s) - seg_keys)
        if unknown:
            raise ValueError(f"segment {i}: {unknown[0]}: unknown field (allowed: {', '.join(sorted(seg_keys))})")
        d: dict = {"seg_index": i, "start_frame": s["start_frame"], "end_frame": s["end_frame"], "asset_id": a.id}
        if s.get("goal"):
            d["goal"] = str(s["goal"]).strip()[:200]
        for key in ("intent", "creative_autofix"):
            if key in s:
                d[key] = s[key]
        kind = s.get("kind")
        n = s["end_frame"] - s["start_frame"]
        if kind == "map":
            if a.kind != "image":
                raise ValueError(f"segment {i}: map segment needs an image asset, {a.id} is a {a.kind}")
            pins = [_pin(p, f"segment {i}: map_pins[{k}]", n, fps) for k, p in enumerate(s.get("map_pins") or [])]
            cards = []
            if s.get("map_card"):
                c = s["map_card"]
                cards.append({"title": c["title"], **({"subtitle": c["subtitle"]} if c.get("subtitle") else {}),
                              "position": c.get("position", "top")})
            scene = {
                "map_asset": a.path, "map_asset_id": a.id, "pins": pins, "cards": cards,
                "camera_preset": s.get("map_camera") or "zoom_in",
                **({"camera_intensity": float(s["intensity"])} if s.get("intensity") is not None else {}),
                **({"camera_path": _camera_path(s["map_path"], n, fps, max_zoom)} if s.get("map_path") else {}),
                **({"title": s["map_title"]} if s.get("map_title") else {}),
                **({"focus": [float(v) for v in s["map_focus"][:2]]} if len(s.get("map_focus") or []) >= 2 else {}),
                **({"map_bounds": a.map_bounds} if a.map_bounds else {}),
                **({"map_projection": a.map_projection} if a.map_bounds and a.map_projection == "mercator" else {})}
            if s.get("map_recipe") is not None:
                built = _recipe(s, i, a, inp, n, prof)
                scene.update(built.scene)
                notes.append(f"segment {i}: {built.why}")
            scene = draft_scene.convert(s, i, n=n, fps=fps, assets=by_id, map_asset=a, used=used, dp=prof.draft,
                                        layout_on=layout_on, scene=scene, morph_on=prof.transitions.map_morph)
            d.update(engine_type=HYPERFRAMES, gsap_scene_config=scene)
        else:
            extra = [k for k in draft_scene.FIELDS if k in s] + [k for k in ("map_pins", "map_path", "map_card", "map_recipe")
                                                                   if s.get(k)]
            if extra:
                raise ValueError(f"segment {i}: {extra[0]} applies to a map segment (kind map), this is kind {kind}")
            d["engine_type"] = NATIVE
            if a.kind == "image":
                d["source_image"] = a.path
            else:
                d["source_video"] = a.path
                d["source_in_frame"] = max(0, sec_to_frame(float(s.get("source_in_s") or 0.0), fps))
            if a.kind == "image" or s.get("camera_preset"):
                cm = {"preset": s.get("camera_preset") or "ken_burns_zoom_in",
                      "intensity": float(s.get("intensity") or 1.0)}
                if len(s.get("focus_box") or []) == 4:
                    cm["focus_box"] = [float(v) for v in s["focus_box"]]
                d["camera_move"] = cm
            ovs = []
            for k, o in enumerate(s.get("overlays") or []):
                ov = {"id": f"s{i}_{o['kind']}{k}", "kind": o["kind"], "box": _slot(o["kind"], o.get("position", "top"), S),
                      "text": o["text"]}
                if o.get("subtitle"):
                    ov["subtitle"] = o["subtitle"]
                if o.get("start_s") is not None:
                    ov["start_frame"] = min(n - 1, max(0, sec_to_frame(o["start_s"], fps)))
                if o.get("end_s") is not None:
                    ov["end_frame"] = min(n, max(ov.get("start_frame", 0) + 1, sec_to_frame(o["end_s"], fps)))
                ovs.append(ov)
            if ovs:
                d["overlays"] = ovs
        out.append(d)
    picks = {c["asset"]: c for c in data.get("credits") or [] if isinstance(c, dict) and c.get("asset")}
    credits = []
    for aid in dict.fromkeys(used):
        a = by_id[aid]
        n = a.credit_line or (picks.get(aid) or {}).get("credit_line")
        line = lines.get(int(n)) if n is not None else None
        if line is None:
            raise ValueError(f"asset {aid}: no credit line (fixed match missing and credits[].credit_line "
                             f"{n!r} is not one of 1..{len(lines)}); use another asset or pick its line")
        if a.credit_line is None and n is not None:
            notes.append(f"credit {aid}: model picked line {n}")
        wm = (picks.get(aid) or {}).get("watermark_free")
        credits.append({"asset_id": aid, "path": a.path, "source_url": line.url, "license": line.license,
                        **({"author": line.author} if line.author else {}), "watermark_free": wm is not False})
    if inp.audio_chain.bgm_path and inp.audio_chain.bgm_asset_id:
        old = _existing_credit(inp, inp.audio_chain.bgm_asset_id)
        if old:
            credits.append(old)
    tl = Timeline(project_id=inp.project_id, profile=inp.profile, settings=S, credits_manifest=credits, audio_chain=inp.audio_chain,
                  segments=out, captions=inp.words, captions_meta=inp.words_meta, caption_style=dict(inp.caption_style),
                  profile_overrides=dict(inp.profile_overrides))
    try:   # open assets the scene names (flag-icons flags): credits from their registry rows, as the build adds them
        tl, auto = assets.ensure_credits(tl, inp.pdir)
    except EngineError as e:
        raise ValueError(f"credits: {e}") from None
    notes += [f"credit {r['asset_id']}: added from the open-asset registry ({r['license']})" for r in auto]
    why = [{"seg": i, "narration": s.get("narration"), "why": s.get("why"), **({"goal": s["goal"]} if s.get("goal") else {})}
           for i, s in enumerate(segs)]
    return tl, notes, why


def _existing_credit(inp: Inputs, aid: str) -> dict | None:
    p = inp.pdir / "master_timeline.json"
    if not p.is_file():
        return None
    from . import timeline
    c = timeline.load(inp.pdir).credit_for(ref=aid)
    return c.model_dump(exclude_none=True) if c else None


# ----------------------------------------------------------------------------------------------------------- gates
def check(tl: Timeline, pdir: Path, dom: bool = True):
    """Pre-flight gates as the build runs them: open-asset credits (registry rows, e.g. flag-icons) count as listed,
    like the build's ensure_credits step (EXTRA_CREDIT_URLS)."""
    from .gates import run_preflight
    from .gates.layer1 import EXTRA_CREDIT_URLS
    token = EXTRA_CREDIT_URLS.set(tuple(c.source_url for c in tl.credits_manifest if (c.path or "").startswith("open:")))
    try:
        return run_preflight(tl, pdir, tl.captions, dom=dom)
    finally:
        EXTRA_CREDIT_URLS.reset(token)


def _failures_text(errs: list[str]) -> str:
    return "\n".join(f"- {e}" for e in errs[:40])


@dataclass
class DraftResult:
    timeline_path: Path | None
    report_path: Path
    ok: bool
    report: dict


def _attempt(provider, inp, contents, sch, rid, files, pdir, dom):
    """One planning request -> (entry, tl, rep, why, data); entry["errors"] empty when the draft passes."""
    r = provider.generate_json("planning", contents, sch, rid, files)
    entry = {"request": rid, "ai": r.meta(), "notes": [], "errors": []}
    tl, rep, why = None, None, []
    errs = [f"schema: {e}" for e in ai.base.schema_errors(r.data, sch)][:20]   # structure first: JSON paths
    if not errs:
        try:
            tl, entry["notes"], why = assemble(inp, r.data)
        except (ValueError, ValidationError, KeyError, TypeError) as e:
            errs = [f"assembly: {e}"]
            tl = None
    if tl is not None:
        rep = check(tl, pdir, dom=dom)
        errs = [f.line() for f in rep.failed]
        entry["preflight"] = rep.summary()
    entry["errors"] = errs
    return entry, tl, rep, why, r.data


def _finish(tl: Timeline, rep) -> Timeline:
    if rep is not None and rep.caption_y_overrides:
        tl.caption_style["y_overrides"] = [list(o) for o in rep.caption_y_overrides]
    return tl


def draft(pdir: Path, *, out: Path | None = None, provider=None, max_repairs: int = DEFAULT_REPAIRS,
          video_proxies: int | None = None, voiceover: str | None = None, script: str | None = None,
          use_cache: bool = True, dom: bool = True, profile: str | None = None, director: bool = True,
          review: bool = False, review_rounds: int = 1, frame_sampler=None) -> DraftResult:
    """provider: `ai.get(...)` instance (default gemini). video_proxies: default 2, agent provider 0 (it reads the
    thumbnails). profile: channel profile (default: the existing timeline's). director: storyboard stage first
    (<project>/draft/storyboards.json). review: frame review + at most `review_rounds` (1-2) revisions, versions kept
    as <project>/draft/timeline.v<N>.json. frame_sampler: director.sample_frames replacement (tests). Raises
    AwaitingAgent when the agent provider wrote a request bundle."""
    from . import director as dr
    if review_rounds not in (1, 2):
        raise EngineError("review_rounds must be 1 or 2")
    t0 = time.perf_counter()
    pdir = Path(pdir).resolve()
    out = Path(out) if out else pdir / "master_timeline.draft.json"
    report_path = out.with_name("draft_report.json")
    ddir = pdir / "draft"
    inp = collect(pdir, voiceover, script, use_cache, profile)
    channel = inp.profile or profiles.DEFAULT
    provider = provider or ai.get()
    if video_proxies is None:
        video_proxies = 0 if provider.name == "agent" else 2
    if hasattr(provider, "reset"):
        provider.reset("draft")
    workdir = config.workdir(f"draft_{inp.project_id}")
    rounds: list[dict] = []
    tl, rep, ok, why = None, None, False, []
    report: dict = {"project": str(pdir), "provider": provider.name, "model": None, "fallback": False, "inputs": {
        "voiceover": inp.audio_chain.voiceover_path, "script": bool(inp.script), "credit_lines": len(inp.credit_lines),
        "assets": [{"id": a.id, "path": a.path, "kind": a.kind, "credit_line": a.credit_line} for a in inp.assets],
        "total_frames": inp.total_frames, "words": len(inp.words), "pauses": len(inp.pause_report.pauses),
        "pause_threshold_db": inp.pause_report.threshold_db, "suggested_segments": inp.pause_report.segments}}
    error = None
    files = attachments(inp)
    sb = None
    try:
        media = media_parts(inp, video_proxies, workdir)
        base = media + [Text(prompt(inp))]
        if director:
            taste = dr.load_taste(channel)
            sb = dr.plan(provider, inp, media, attachments(inp), taste)
            ddir.mkdir(exist_ok=True)
            (ddir / "storyboards.json").write_text(json.dumps(
                {"channel": channel, **{k: v for k, v in sb.items() if k != "chosen"},
                 "chosen": sb["chosen"]["index"] if sb["chosen"] else None},
                indent=2, ensure_ascii=False) + "\n")
            report["storyboard"] = {"path": str(ddir / "storyboards.json"),
                                    "chosen": sb["chosen"]["index"] if sb["chosen"] else None,
                                    "direction": sb["chosen"]["concept"].get("direction") if sb["chosen"] else None,
                                    "scores": [{"index": x["index"], "valid": x["valid"], **x["score"]} for x in sb["ranked"]],
                                    **({"warning": sb["warning"]} if sb.get("warning") else {})}
            if sb["chosen"]:
                base = base + [Text(dr.chosen_text(inp, sb["chosen"]["concept"]))]
                files = {**files, "storyboard.json": sb["chosen"]["concept"]}
        contents, sch = base, schema(inp)
        data = None
        for rnd in range(max_repairs + 1):
            entry, tl, rep, why, data = _attempt(provider, inp, contents, sch, f"draft/{rnd}", files, pdir, dom)
            entry["round"] = rnd
            rounds.append(entry)
            errs = entry["errors"]
            if not errs:
                ok = True
                break
            if rnd < max_repairs:
                contents = base + [
                    Text("YOUR PREVIOUS DRAFT:\n" + json.dumps(data, ensure_ascii=False)),
                    Text("THE ENGINE REJECTED IT WITH THESE GATE ERRORS:\n" + _failures_text(errs)
                         + "\nReturn the complete corrected JSON (all segments and credits), fixing every error.")]
                files = {**files, "previous_response.json": data, "gate_errors.txt": _failures_text(errs)}
        if ok and review:
            tl, rep, why = _review(provider, inp, pdir, ddir, tl, rep, why, data, base, sch, files, dom, review_rounds,
                                   frame_sampler or dr.sample_frames, workdir, report)
    except AwaitingAgent as e:
        error = e
        report["awaiting"] = str(e.bundle)
    except Exception as e:
        error = e
        report["error"] = f"{type(e).__name__}: {e}"[:2000]
    finally:
        report["file_deletions"] = provider.cleanup()
        shutil.rmtree(workdir, ignore_errors=True)
    ai_rounds = rounds + [x for x in report.get("review", []) if "ai" in x]
    last = rounds[-1]["ai"] if rounds else {}
    answer = {"provider": last.get("provider", provider.name), "model": last.get("model"),
              "fallback": any(x["ai"]["fallback"] for x in ai_rounds)}
    if error is not None:
        report.update({"ok": False, **answer, "rounds": rounds, "seconds": round(time.perf_counter() - t0, 2)})
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        raise error
    report.update({
        "ok": ok, **answer,
        "warnings": [x["ai"]["warning"] for x in ai_rounds if x["ai"]["warning"]],
        "retries_total": sum(x["ai"]["retries"] for x in ai_rounds), "repair_rounds": max(0, len(rounds) - 1),
        "rounds": rounds, "seconds": round(time.perf_counter() - t0, 2)})
    path = None
    if tl is not None:
        if ok:
            _finish(tl, rep)
            if rep is not None:
                report["fixes"] = rep.fixes
        from . import timeline
        target = out if ok else out.with_name(out.stem + ".rejected.json")
        timeline.save(tl, target)
        path = target
        report["timeline"] = str(target)
        report["segments"] = [{"seg": s.seg_index, "frames": [s.start_frame, s.end_frame], "engine": s.engine_type,
                               "asset": s.asset_id, **(why[s.seg_index] if s.seg_index < len(why) else {})}
                              for s in tl.segments]
        if ok and sb and sb["chosen"]:
            c = sb["chosen"]["concept"]
            dr.remember(channel, c.get("opener"), [b.get("transition") for b in (c.get("spans") or c.get("beats") or [])])
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return DraftResult(path if ok else None, report_path, ok, report)


def _review(provider, inp, pdir, ddir, tl, rep, why, data, base, sch, files, dom, max_rounds, sampler, workdir, report):
    """Frame review loop. Every accepted timeline is saved as draft/timeline.v<N>.json (v0 = the gate-passing draft);
    a revision that fails the gates is recorded and the previous version stays."""
    from . import director as dr
    from . import timeline as tlm
    ddir.mkdir(exist_ok=True)
    log: list[dict] = []
    report["review"] = log
    versions = [ddir / "timeline.v0.json"]
    tlm.save(_finish(tl.model_copy(deep=True), rep), versions[0])
    for k in range(max_rounds):
        times = dr.critical_times(tl)
        frames = sampler(tl, pdir, times, workdir / f"review{k}")
        goals = {s.seg_index: s.goal for s in tl.segments if s.goal}
        r = provider.generate_json("planning", dr.review_parts(tl, inp, frames, goals), dr.review_schema(tl),
                                   f"draft/review{k}", {"timeline.json": tl.model_dump(mode="json", exclude_none=True)})
        fs = r.data.get("findings") or []
        entry = {"round": k, "request": f"draft/review{k}", "ai": r.meta(), "version": versions[-1].name,
                 "times": [t for t, _ in times], "verdict": r.data.get("verdict"), "findings": fs}
        log.append(entry)
        (ddir / f"review.v{len(versions) - 1}.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        major = dr.needs_revision(r.data)
        if not major:
            break
        contents = base + [Text("YOUR CURRENT DRAFT:\n" + json.dumps(data, ensure_ascii=False)),
                           Text("THE FRAME REVIEW FOUND THESE PROBLEMS (time, segment, fix):\n" + dr.findings_text(major)
                                + "\nReturn the complete corrected JSON (all segments and credits), fixing each problem "
                                  "without breaking the rules.")]
        rf = {**files, "previous_response.json": data, "review_findings.json": major}
        rev, tl2, rep2, why2, data2 = _attempt(provider, inp, contents, sch, f"draft/revise{k}", rf, pdir, dom)
        rev.update(round=k, revision=True)
        log.append(rev)
        if rev["errors"]:
            rev["kept"] = versions[-1].name
            break
        tl, rep, why, data = tl2, rep2, why2, data2
        versions.append(ddir / f"timeline.v{len(versions)}.json")
        tlm.save(_finish(tl.model_copy(deep=True), rep), versions[-1])
    report["versions"] = [str(v) for v in versions]
    return tl, rep, why
