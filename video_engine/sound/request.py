"""Agent file handoff for sounds: request bundle <project>/out/sound_request/, response import, credits.

Bundle files:
  request.json   {"request_id", "required_source", "items": [{"slot", "type", "kind" | "brief"}]}
  schema.json    JSON schema of response.json
  request.md     task and answer instructions
  response.json  written by the agent: {"request_id", "items": [{"slot", "file", "source", "id", "title",
                 "artist", "duration_ms"}]}; "file" is a path relative to the bundle directory
A missing sound with no response.json raises AwaitingAgent (CLI exit code 3). A valid response is imported into the
cache and the bundle directory is removed.
"""
from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from .. import profiles
from ..ai.base import AwaitingAgent, schema_errors
from ..util import EngineError, blake3_json, probe
from . import AUDIO_EXTS, EPIDEMIC, required_source
from . import cache

BUNDLE = Path("out") / "sound_request"
ITEM_KEYS = ("slot", "file", "source", "id", "title", "artist", "duration_ms")
SFX_ARTIST = "Epidemic Sound"
SCHEMA = {
    "type": "object", "required": ["request_id", "items"],
    "properties": {
        "request_id": {"type": "string"},
        "items": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": list(ITEM_KEYS),
            "properties": {"slot": {"type": "string"}, "file": {"type": "string"}, "source": {"type": "string"},
                           "id": {"type": "string"}, "title": {"type": "string"}, "artist": {"type": "string"},
                           "duration_ms": {"type": "integer"}}}}}}


def bundle_dir(project_dir: Path) -> Path:
    return Path(project_dir) / BUNDLE


def default_resume_cmd(project_dir: Path) -> str:
    return shlex.join(["python", "-m", "video_engine.sound", str(project_dir), "--resume"])


def request_doc(items: list[dict], source: str) -> dict:
    return {"request_id": blake3_json({"source": source, "items": items})[:16], "required_source": source,
            "items": items}


def _md(req: dict, resume_cmd: str) -> str:
    lines = [f"# Sound request {req['request_id']}", "",
             f"Source: {req['required_source']} (connector namespace user-epidemic-sound). For every item, find one "
             "file, download it into files/ in this directory and add one entry to `response.json` (valid against "
             "`schema.json`). Helpers: video_engine/sound/epidemic.py. Contract: docs/SOUND.md.", "",
             "Items:"]
    for it in req["items"]:
        what = f"SFX kind '{it['kind']}'" if it["type"] == "sfx" else f"music brief {json.dumps(it['brief'])}"
        lines.append(f"- slot `{it['slot']}`: {what}")
    lines += ["", f"Then run:\n\n    {resume_cmd}", ""]
    return "\n".join(lines)


def write_bundle(d: Path, req: dict, resume_cmd: str) -> None:
    shutil.rmtree(d, ignore_errors=True)
    (d / "files").mkdir(parents=True)
    (d / "request.json").write_text(json.dumps(req, indent=1, ensure_ascii=False))
    (d / "schema.json").write_text(json.dumps(SCHEMA, indent=1))
    (d / "request.md").write_text(_md(req, resume_cmd))


def _duration_ms(f: Path) -> int:
    try:
        return round(float(probe(f)["format"]["duration"]) * 1000)
    except (EngineError, KeyError, ValueError):
        raise EngineError(f"{f}: not a decodable audio file") from None


def read_response(d: Path, req: dict) -> list[dict]:
    """Validated response items: schema, request id, one item per requested slot, the required source, audio files
    inside the bundle, non-empty id and title, the measured duration within 250 ms of duration_ms."""
    resp = d / "response.json"
    try:
        data = json.loads(resp.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise EngineError(f"{resp}: invalid JSON ({e})") from None
    errs = schema_errors(data, SCHEMA) if isinstance(data, dict) else ["$: top level must be a JSON object"]
    if errs:
        raise EngineError(f"{resp} does not match schema.json:\n  " + "\n  ".join(errs[:20]))
    if data["request_id"] != req["request_id"]:
        raise EngineError(f"{resp}: request_id {data['request_id']!r} is not {req['request_id']!r}")
    want = {it["slot"] for it in req["items"]}
    got = [it["slot"] for it in data["items"]]
    if sorted(got) != sorted(want):
        raise EngineError(f"{resp}: items must answer each slot once: requested {sorted(want)}, got {sorted(got)}")
    base = d.resolve()
    for it in data["items"]:
        extra = set(it) - set(ITEM_KEYS)
        if extra:
            raise EngineError(f"{resp}: slot {it['slot']}: unknown keys {sorted(extra)}")
        if it["source"] != req["required_source"]:
            raise EngineError(f"{resp}: slot {it['slot']}: source {it['source']!r} is not {req['required_source']!r}")
        if not it["id"].strip() or not it["title"].strip():
            raise EngineError(f"{resp}: slot {it['slot']}: id and title must be non-empty")
        f = (d / it["file"]).resolve()
        if not f.is_relative_to(base) or f.suffix.lower() not in AUDIO_EXTS or not f.is_file():
            raise EngineError(f"{resp}: slot {it['slot']}: file {it['file']!r} must be an audio file "
                              f"({', '.join(AUDIO_EXTS)}) inside {d}")
        ms = _duration_ms(f)
        if abs(ms - it["duration_ms"]) > 250:
            raise EngineError(f"{resp}: slot {it['slot']}: file lasts {ms} ms, duration_ms says {it['duration_ms']}")
        it["path"], it["measured_ms"] = f, ms
    return data["items"]


def import_response(d: Path, req: dict) -> list[cache.ResolvedSound]:
    by_slot = {it["slot"]: it for it in req["items"]}
    out = []
    for it in read_response(d, req):
        r = by_slot[it["slot"]]
        prov = {"source": it["source"], "id": it["id"], "title": it["title"], "artist": it["artist"] or SFX_ARTIST,
                "credit": cache.credit_line(it["source"], it["id"], it["title"], it["artist"] or SFX_ARTIST)}
        key = r["kind"] if r["type"] == "sfx" else cache.brief_key(r["brief"])
        out.append(cache.put(r["type"], key, it["path"], prov, it["measured_ms"]))
    return out


def ensure_sounds(tl, project_dir: Path, resume: bool = False, resume_cmd: str | None = None
                  ) -> list[cache.ResolvedSound]:
    """Fill the cache for cache.missing(tl, profile); the request's source is the profile's required source, else
    Epidemic Sound. Nothing missing -> []. A response.json answering the current request -> imported (returned) and the bundle
    removed. --resume without a response -> EngineError. Otherwise the bundle is (re)written and AwaitingAgent
    is raised."""
    prof = profiles.of(tl)
    need = cache.missing(tl, prof)
    if not need:
        return []
    project_dir = Path(project_dir)
    d, req = bundle_dir(project_dir), request_doc(need, required_source(prof) or EPIDEMIC)
    resume_cmd = resume_cmd or default_resume_cmd(project_dir)
    old = cache.read_json(d / "request.json")
    current = old is not None and old.get("request_id") == req["request_id"]
    if current and (d / "response.json").is_file():
        got = import_response(d, req)
        left = cache.missing(tl, prof)
        if left:
            raise EngineError(f"sounds still missing after import: {[it['slot'] for it in left]}")
        shutil.rmtree(d, ignore_errors=True)
        return got
    if current and resume:
        raise EngineError(f"no response.json in {d}: write the answer there (valid against schema.json), "
                          f"then run: {resume_cmd}")
    write_bundle(d, req, resume_cmd)
    raise AwaitingAgent(d, resume_cmd)


def resolved(tl, project_dir: Path) -> list[cache.ResolvedSound]:
    """What the mixer resolves for the timeline: one file per SFX kind used, then the music (if any)."""
    from .. import mixer, sfx
    prof = profiles.of(tl)
    out = [cache.resolve(k, prof) for k in sfx.kinds_used(tl)]
    m = mixer.resolve_music(tl, Path(project_dir), prof) if prof.sound is not None else None
    return out + ([cache.ResolvedSound(m.path, m.provenance)] if m else [])
