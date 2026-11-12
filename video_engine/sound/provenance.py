"""Licence provenance for music supplied by a project path.

Cache-sourced music carries `{source, id, title, artist, credit}` on its sidecar, and gate A02 reads that
`source`. A file named by `audio_chain.bgm_path` has no cache sidecar, so the mix used to mark it `local-test`
and A02 rejected it whenever the profile required `epidemic`.

A path passes as Epidemic Sound when one of these names the same bytes:

- A sidecar next to the audio file (`<stem>.json`, `<filename>.json`, or `<filename>.licence.json`) with
  `source` = `epidemic`, a non-empty `id` (the track id), and `sha256` equal to the file.
  `title` and `artist` are optional. `credit` is optional; when it is absent the credit line is built from the
  id, title and artist.
- A sound-lock entry in `<project>/out/build_manifest.json` or `<project>/out/draft/build_manifest.json` whose
  `sha256` equals the file and whose `provenance` has `source` = `epidemic` and a non-empty `id`.

A sidecar that claims `epidemic` but whose `sha256` does not match the file is an error: the claim is not
silently dropped. When the sidecar and the lock both name a track and the ids differ, that is an error too.
No claim, or a claim for another source, leaves the file as project audio (`local-test` in the mix).
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import manifest
from ..util import EngineError
from . import EPIDEMIC, PROVENANCE_KEYS
from .cache import credit_line

def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def sidecars(audio: Path) -> list[Path]:
    """Sidecar paths consulted for `audio`, existing or not, in read order."""
    audio = Path(audio)
    names = [audio.with_suffix(".json"), audio.with_suffix(".licence.json"),
             Path(str(audio) + ".json"), Path(str(audio) + ".licence.json")]
    out, seen = [], set()
    for p in names:
        if p.resolve() not in seen and p.suffix == ".json":
            seen.add(p.resolve())
            out.append(p)
    return out


def _str(data: dict, key: str) -> str:
    v = data.get(key)
    return v.strip() if isinstance(v, str) else ""


def _finish(data: dict, sha: str, where: str) -> dict | None:
    """Provenance when `data` claims Epidemic Sound, else None. A bad claim raises EngineError."""
    source = _str(data, "source")
    if source != EPIDEMIC:
        return None
    track = _str(data, "id")
    claimed = _str(data, "sha256")
    if not track:
        raise EngineError(f"{where}: epidemic provenance needs a non-empty id")
    if not claimed:
        raise EngineError(f"{where}: epidemic provenance needs sha256 of the audio file")
    if claimed != sha:
        raise EngineError(f"{where}: sha256 does not match the audio file")
    title = _str(data, "title") or track
    artist = _str(data, "artist") or "Epidemic Sound"
    credit = _str(data, "credit") or credit_line(EPIDEMIC, track, title, artist)
    prov = {"source": EPIDEMIC, "id": track, "title": title, "artist": artist, "credit": credit}
    if set(prov) != set(PROVENANCE_KEYS):
        raise EngineError(f"{where}: provenance keys are {', '.join(PROVENANCE_KEYS)}")
    return prov


def from_sidecar(audio: Path) -> dict | None:
    """Epidemic provenance from the first sidecar that claims it. None when no sidecar claims a source."""
    audio = Path(audio)
    if not audio.is_file():
        return None
    sha = manifest.sha256(audio, fresh=True)
    for side in sidecars(audio):
        data = _read(side)
        if not data or _str(data, "source") != EPIDEMIC:
            continue
        return _finish(data, sha, str(side))
    return None


def _lock_hits(project_dir: Path, sha: str) -> list[dict]:
    hits = []
    for rel in ("out/build_manifest.json", "out/draft/build_manifest.json"):
        f = Path(project_dir) / rel
        if not f.is_file():
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        sounds = doc.get("sounds") or {}
        if not isinstance(sounds, dict):
            continue
        rows = []
        if isinstance(sounds.get("music"), dict):
            rows.append(sounds["music"])
        sfx = sounds.get("sfx") or {}
        if isinstance(sfx, dict):
            rows.extend(e for e in sfx.values() if isinstance(e, dict))
        for entry in rows:
            if entry.get("sha256") != sha:
                continue
            prov = entry.get("provenance")
            if isinstance(prov, dict) and prov.get("source") == EPIDEMIC and _str(prov, "id"):
                hits.append(prov)
    return hits


def from_lock(audio: Path, project_dir: Path | None) -> dict | None:
    """Epidemic provenance from a sound lock whose sha256 is the file. None when no lock claims it."""
    if project_dir is None or not Path(audio).is_file():
        return None
    sha = manifest.sha256(audio, fresh=True)
    hits = _lock_hits(project_dir, sha)
    if not hits:
        return None
    ids = {h.get("id") for h in hits}
    if len(ids) != 1:
        raise EngineError(f"{audio}: sound locks name more than one epidemic track id for these bytes ({sorted(ids)})")
    prov = hits[0]
    title = _str(prov, "title") or _str(prov, "id")
    artist = _str(prov, "artist") or "Epidemic Sound"
    credit = _str(prov, "credit") or credit_line(EPIDEMIC, _str(prov, "id"), title, artist)
    return {"source": EPIDEMIC, "id": _str(prov, "id"), "title": title, "artist": artist, "credit": credit}


def for_file(audio: Path, project_dir: Path | None = None) -> dict | None:
    """Epidemic provenance for a path-supplied file, or None when nothing claims it.

    The sidecar wins when both exist and agree. Disagreement on the track id is an error."""
    audio = Path(audio)
    side = from_sidecar(audio)
    lock = from_lock(audio, project_dir)
    if side and lock and side["id"] != lock["id"]:
        raise EngineError(f"{audio}: sidecar track id {side['id']!r} does not match the sound lock {lock['id']!r}")
    return side or lock
