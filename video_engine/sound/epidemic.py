"""Pure helpers for the agent that answers a sound request with the Epidemic Sound connector (MCP namespace
user-epidemic-sound). No network access and no keys: the agent calls the connector, downloads the signed URL into
the bundle's files/ directory and writes response.json with response_item / write_response.

Connector calls per item:
  SFX:   SearchSoundEffects(sfx_search_args(kind)) -> sfx_candidates -> DownloadSoundEffect(id, {fileType: WAV})
  music: SearchRecordings(music_search_args(brief)) -> music_candidates -> DownloadRecording(id,
         {fileType: MP3, stemType: FULL})
A RETRY_LATER error is retried after retry_after(error) seconds (default RETRY_DEFAULT_S), at most MAX_RETRIES times.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import SFX_KINDS

RETRY_DEFAULT_S = 5
MAX_RETRIES = 3
# SFX kind -> (search term, max duration ms)
SFX_SEARCH = {
    "pop": ("pop", 1500), "tick": ("tick", 600), "whoosh": ("whoosh", 2500), "click": ("click", 500),
    "beep": ("beep", 1000), "counter_tick": ("counter tick", 400),     "impact": ("impact", 3000),
    "riser": ("riser", 5000),
    "rumble": ("deep rumble", 4000),
}
ENERGY_WORD = {"low": "calm", "medium": "", "high": "energetic"}
MUSIC_MAX_MS = 300000


def sfx_search_args(kind: str, first: int = 10) -> dict:
    if kind not in SFX_KINDS:
        raise ValueError(f"unknown SFX kind '{kind}'")
    term, max_ms = SFX_SEARCH[kind]
    return {"query": {"term": term}, "filter": {"duration": {"max": max_ms}}, "first": first}


def music_search_args(brief: dict, first: int = 10) -> dict:
    """Instrumental tracks at least the brief's duration long, within its BPM range."""
    term = " ".join(w for w in (brief["mood"].strip(), ENERGY_WORD[brief["energy"]]) if w)
    min_ms = min(MUSIC_MAX_MS, round(brief["duration_s"] * 1000))
    return {"query": {"term": term},
            "filter": {"duration": {"min": min_ms, "max": MUSIC_MAX_MS},
                       "bpm": {"min": brief["bpm_min"], "max": brief["bpm_max"]}, "vocals": False},
            "first": first}


def sfx_candidates(result: dict) -> list[dict]:
    """SearchSoundEffects result -> [{id, title, artist, duration_ms}] in result order (artist "Epidemic Sound")."""
    nodes = ((result.get("data") or result).get("soundEffects") or {}).get("nodes") or []
    return [{"id": n["soundEffect"]["id"], "title": n["soundEffect"]["title"], "artist": "Epidemic Sound",
             "duration_ms": int(n["soundEffect"]["audioFile"]["durationInMilliseconds"])} for n in nodes]


def music_candidates(result: dict) -> list[dict]:
    """SearchRecordings result -> [{id, title, artist, bpm, duration_ms}]; artist = MAIN_ARTIST names joined
    with ", " (COMPOSER names when no main artist is listed)."""
    out = []
    for n in ((result.get("data") or result).get("recordings") or {}).get("nodes") or []:
        r = n["recording"]
        names = lambda role: [c["artist"]["name"] for c in r.get("credits", []) if c["role"] == role]  # noqa: E731
        out.append({"id": r["id"], "title": r["title"], "artist": ", ".join(names("MAIN_ARTIST") or names("COMPOSER")),
                    "bpm": r.get("bpm"), "duration_ms": int(r["audioFile"]["durationInMilliseconds"])})
    return out


def pick_sfx(cands: list[dict], kind: str) -> dict | None:
    """First candidate within the kind's duration cap."""
    cap = SFX_SEARCH[kind][1]
    return next((c for c in cands if 0 < c["duration_ms"] <= cap), None)


def pick_music(cands: list[dict], brief: dict) -> dict | None:
    """First candidate at least the brief's duration long with a BPM inside the brief's range."""
    need = min(MUSIC_MAX_MS, round(brief["duration_s"] * 1000))
    return next((c for c in cands if c["duration_ms"] >= need
                 and (c.get("bpm") is None or brief["bpm_min"] <= c["bpm"] <= brief["bpm_max"])), None)


def retry_after(error) -> float | None:
    """Seconds to wait for a RETRY_LATER error (dict, GraphQL error list or text); None = not RETRY_LATER."""
    text = error if isinstance(error, str) else json.dumps(error)
    if "RETRY_LATER" not in text:
        return None
    m = re.search(r'"?retryAfterSeconds"?\s*[:=]\s*"?(\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else float(RETRY_DEFAULT_S)


def asset_url(result: dict) -> str:
    """assetUrl of a Download* result (a signed URL: pass it to the downloader, never print or store it)."""
    data = result.get("data") or result
    for v in data.values():
        if isinstance(v, dict) and v.get("assetUrl"):
            return v["assetUrl"]
    raise ValueError("no assetUrl in the download result")


def redact(url: str) -> str:
    """URL without query string and fragment (safe to log)."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def file_ext(url: str) -> str:
    """Audio extension of a download URL path (.wav or .mp3)."""
    ext = Path(urlsplit(url).path).suffix.lower()
    if ext not in (".wav", ".mp3"):
        raise ValueError(f"unexpected download file type '{ext}'")
    return ext


def response_item(slot: str, file: str, cand: dict, duration_ms: int | None = None) -> dict:
    """response.json item for a downloaded candidate; file is relative to the bundle directory."""
    return {"slot": slot, "file": file, "source": "epidemic", "id": cand["id"], "title": cand["title"],
            "artist": cand["artist"], "duration_ms": int(cand["duration_ms"] if duration_ms is None else duration_ms)}


def write_response(bundle: Path, request_id: str, items: list[dict]) -> Path:
    p = Path(bundle) / "response.json"
    p.write_text(json.dumps({"request_id": request_id, "items": items}, indent=1, ensure_ascii=False))
    return p
