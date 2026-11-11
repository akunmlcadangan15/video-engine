"""Provider-agnostic AI layer: request parts, result record, retry/fallback loop, JSON-schema check, media degradation.

Parts (in request order):
  Text(text)                         prompt text or a label
  Image(data, mime, label)           inline image bytes
  Media(path, kind, mime, label, transcript, words)
                                     audio or video file. A provider without the matching input capability receives
                                     the transcript (audio) or sampled JPEG frames (video) instead (`degrade`).
Retry semantics (every provider): HTTP 429 / 503, a transport drop without status (treated as 503) and invalid JSON
-> retry the same model up to SAME_MODEL_RETRIES times after BACKOFF_S[i] + jitter; then the next model.
HTTP 404 -> next model at once. Any other status -> EngineError, no retry. A fallback answer carries a warning that is
printed to stderr.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .. import config
from ..util import EngineError

SAME_MODEL_RETRY_CODES = {429, 503}
NEXT_MODEL_CODES = {404}
RETRY_STATUSES = {"RESOURCE_EXHAUSTED": 429, "UNAVAILABLE": 503, "NOT_FOUND": 404}
TRANSPORT_ERRORS = {"RemoteProtocolError", "ReadTimeout", "ReadError", "WriteError", "ConnectError", "ConnectTimeout",
                    "PoolTimeout", "WriteTimeout", "ServerDisconnectedError"}
SAME_MODEL_RETRIES = 3
BACKOFF_S = (2.0, 5.0, 12.0)
JITTER_FRAC = 0.25
HTTP_TIMEOUT_S = 300
FRAME_PX = 384
_sleep = time.sleep   # patched in tests



# ------------------------------------------------------------------------------------------------------------ parts
@dataclass
class Text:
    text: str


@dataclass
class Image:
    data: bytes
    mime: str = "image/jpeg"
    label: str = ""


@dataclass
class Media:
    path: Path
    kind: str                      # audio | video
    mime: str
    label: str = ""
    transcript: str | None = None  # audio: text sent to a provider without audio input
    words: list[dict] | None = None   # audio: [{word, start_frame, end_frame}] (agent bundle transcript.json)
    frames: int = 6                # video: frames sampled for a provider without video input


Part = Text | Image | Media


class AwaitingAgent(Exception):
    """A request bundle was written for the agent provider; the command exits with code 3."""

    def __init__(self, bundle: Path, resume: str):
        super().__init__(f"waiting for agent response: write {bundle / 'response.json'}, then run: {resume}")
        self.bundle, self.resume = bundle, resume


@dataclass
class AIResult:
    data: dict
    model: str                       # model that answered
    tried: list[tuple[str, str]]     # (model, outcome) per attempt, in order
    seconds: float
    usage: dict
    retries: int = 0                 # failed attempts on the answering model before its answer
    fallback: bool = False           # answered by a model other than models[0]
    warning: str | None = None
    provider: str = "gemini"

    def meta(self) -> dict:
        return {"provider": self.provider, "model": self.model, "retries": self.retries, "fallback": self.fallback,
                "warning": self.warning, "tried": [list(t) for t in self.tried], "seconds": self.seconds,
                "usage": self.usage}


class Provider(Protocol):
    name: str

    def generate_json(self, task: str, parts: list[Part], schema: dict | None, request_id: str,
                      attachments: dict | None = None) -> AIResult: ...

    def cleanup(self) -> list[dict]: ...


# ---------------------------------------------------------------------------------------------------- error mapping
def is_transport(e: BaseException) -> bool:
    return type(e).__name__ in TRANSPORT_ERRORS or isinstance(e, (ConnectionError, TimeoutError))


def code_of(e: BaseException) -> int | None:
    c = getattr(e, "code", None)
    if isinstance(c, int):
        return c
    st = getattr(e, "status", None)
    if isinstance(st, str) and st in RETRY_STATUSES:
        return RETRY_STATUSES[st]
    return 503 if is_transport(e) else None


def status_of(e: BaseException) -> str:
    if is_transport(e) and not isinstance(getattr(e, "code", None), int):
        return f"transport {type(e).__name__} (no HTTP status; retried like 503)"
    c, st = code_of(e), getattr(e, "status", None)
    return " ".join(str(x) for x in (c, st if isinstance(st, str) else None) if x) or type(e).__name__


def backoff_delay(retry: int) -> float:
    """Delay before same-model retry number `retry` (1-based): BACKOFF_S[retry-1] + jitter."""
    base = BACKOFF_S[min(retry, len(BACKOFF_S)) - 1]
    return base + random.uniform(0.0, JITTER_FRAC * base)


def qualified_model(provider: str, model: str) -> str:
    """`provider/model` label; a model id that already starts with `provider/` is returned unchanged."""
    prefix = f"{provider}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n(.*?)```", re.DOTALL)


def _no_constant(name: str):
    raise json.JSONDecodeError(f"{name} is not valid JSON", name, 0)


def _loads(t: str):
    """json.loads without the NaN / Infinity extension: a number the timeline cannot hold is an invalid reply."""
    return json.loads(t, parse_constant=_no_constant)


def parse_json_text(text):
    """JSON value from a model reply. Accepted, in this order: a bare JSON document; a ```json fenced block at the
    start; the first fenced block after leading prose; one JSON document that starts at the first `{` or `[` and is
    followed by prose. Chat-completions content given as a list of parts is read as its joined text parts. Anything
    else raises json.JSONDecodeError, which `run_models` retries. A cut-off document is never repaired: only a
    complete document at the first bracket counts, so a fragment inside a truncated reply is not taken for it."""
    if isinstance(text, list):
        parts = [p.get("text") if isinstance(p, dict) else p for p in text]
        text = "".join(p for p in parts if isinstance(p, str))
    if not isinstance(text, str):
        text = ""
    t = text.strip()
    body = t
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    try:
        return _loads(body)
    except json.JSONDecodeError as first:
        m = _FENCE.search(t)
        if m and not t.startswith("```"):
            return _loads(m.group(1))
        start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        try:
            data, _ = json.JSONDecoder(parse_constant=_no_constant).raw_decode(t, start)
        except json.JSONDecodeError:
            raise first from None
        return data


def run_models(models: list[str], attempt: Callable[[str], tuple[str, dict]], label: str, provider: str
               ) -> AIResult:
    """attempt(model) -> (reply text, usage) or raises. Applies the retry/fallback rules of this module."""
    tried: list[tuple[str, str]] = []
    t0 = time.perf_counter()
    for m in models:
        retries = 0
        while True:
            try:
                text, usage = attempt(m)
            except EngineError as e:
                if not hasattr(e, "tried"):
                    e.tried = list(tried)
                raise
            except Exception as e:
                code = code_of(e)
                tried.append((m, f"error {status_of(e)}"))
                if code in NEXT_MODEL_CODES:
                    break
                if code not in SAME_MODEL_RETRY_CODES:
                    err = EngineError(f"{label} call to {m} failed ({status_of(e)}); not retried; tried {tried}")
                    err.tried = list(tried)
                    raise err from None
                if retries >= SAME_MODEL_RETRIES:
                    break
                retries += 1
                _sleep(backoff_delay(retries))
                continue
            try:
                data = parse_json_text(text)
            except json.JSONDecodeError:
                tried.append((m, "invalid json"))
                if retries >= SAME_MODEL_RETRIES:
                    break
                retries += 1
                _sleep(backoff_delay(1))
                continue
            tried.append((m, "ok"))
            fallback = m != models[0]
            warning = None
            if fallback:
                first = [o for mm, o in tried if mm == models[0]]
                warning = f"FALLBACK: {models[0]} did not answer ({', '.join(first)}); answer is from {m}"
                print(f"warning: {warning}", file=sys.stderr)
            return AIResult(data if isinstance(data, dict) else {"items": data}, m, tried,
                            round(time.perf_counter() - t0, 2), usage, retries, fallback, warning, provider)
    err = EngineError(f"all {label} models failed: {tried}")
    err.tried = list(tried)
    raise err


# ------------------------------------------------------------------------------------------------------ JSON schema
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def schema_errors(data, schema: dict, path: str = "$") -> list[str]:
    """Validation of the schema subset the engine emits: type, properties, required, items, enum, minItems,
    maxItems. Returns error strings (empty = valid)."""
    errs: list[str] = []
    t = schema.get("type")
    if t in ("integer", "number"):
        ok = isinstance(data, (int, float)) and not isinstance(data, bool) and (t == "number" or float(data).is_integer())
        if not ok:
            return [f"{path}: expected {t}, got {type(data).__name__}"]
    elif t in _TYPES and not (isinstance(data, _TYPES[t]) and not (t != "boolean" and isinstance(data, bool))):
        return [f"{path}: expected {t}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: {data!r} not in {schema['enum']}")
    if isinstance(data, dict):
        for k in schema.get("required", []):
            if k not in data:
                errs.append(f"{path}: missing required '{k}'")
        for k, sub in schema.get("properties", {}).items():
            if k in data:
                errs += schema_errors(data[k], sub, f"{path}.{k}")
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, x in enumerate(data):
                errs += schema_errors(x, schema["items"], f"{path}[{i}]")
    return errs


# ----------------------------------------------------------------------------------------------------------- media
def sample_frames(path: Path, n: int, px: int = FRAME_PX) -> list[tuple[float, bytes]]:
    """n JPEG frames at k/n of the duration (k = 0..n-1): [(time_s, jpeg)]."""
    from ..util import probe
    dur = float(probe(path)["format"].get("duration") or 1.0)
    out = []
    for k in range(max(1, n)):
        t = dur * k / max(1, n)
        r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                            f"scale='min({px},iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            out.append((round(t, 2), r.stdout))
    return out


def degrade(parts: list[Part], caps: set[str]) -> list[Part]:
    """Replace Media parts the target cannot take: audio -> Text(transcript), video -> labelled Image frames;
    drop images when `image` is not a capability (a Text note marks each)."""
    out: list[Part] = []
    for p in parts:
        if isinstance(p, Media) and p.kind not in caps:
            if p.kind == "audio":
                out.append(Text(f"[{p.label or p.path.name}: audio replaced by its transcript]\n{p.transcript or '(no transcript)'}"))
            else:
                frames = sample_frames(p.path, p.frames)
                for t, jpg in frames:
                    lab = f"{p.label or p.path.name} frame at {t:.2f} s"
                    out += ([Text(lab), Image(jpg, "image/jpeg", lab)] if "image" in caps
                            else [Text(f"[{lab}: image omitted, model has no image input]")])
        elif isinstance(p, Image) and "image" not in caps:
            out.append(Text(f"[{p.label or 'image'}: image omitted, model has no image input]"))
        else:
            out.append(p)
    return out
