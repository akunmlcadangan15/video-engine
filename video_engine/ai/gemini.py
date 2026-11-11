"""Gemini provider (google-genai SDK).

  MODEL            the only Gemini model, used for planning/draft/director and visual QA
  PLANNING_MODELS  footage analysis / timeline drafting
  QA_MODELS        visual QA on gate snapshots
A configured fallback provider receives the same request when Gemini raises EngineError (after the normal retries,
on 404, or on a missing GEMINI_API_KEY). Retry/fallback rules: `video_engine.ai.base`. Every HTTP request has a
HTTP_TIMEOUT_S timeout. Request config: media_resolution=MEDIA_RESOLUTION_LOW, thinking_budget=1024,
response_mime_type=application/json. Media parts (audio, video) go through the File API once per provider instance;
`cleanup()` deletes every upload, verifies the deletion and delegates to the fallback provider.
Key: env GEMINI_API_KEY; never printed or logged.
"""
from __future__ import annotations

import os
import sys
import time

from . import base
from .base import AIResult, Image, Media, Part, Text, code_of, status_of
from ..util import EngineError

NAME = "gemini"
MODEL = "gemini-3.8-flash"
PLANNING_MODELS = [MODEL]
QA_MODELS = [MODEL]
THINKING_BUDGET = 1024
GONE_CODES = (403, 404)   # files.get status codes that mean a deleted file
HTTP_TIMEOUT_S = base.HTTP_TIMEOUT_S
FILE_ACTIVE_TIMEOUT_S = 120
CAPABILITIES = {"image", "audio", "video"}


def _client():
    if not os.environ.get("GEMINI_API_KEY"):
        raise EngineError("GEMINI_API_KEY is not set")
    from google import genai
    from google.genai import types
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                        http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_S * 1000))


def _gen_config(schema: dict | None = None):
    from google.genai import types
    kw = dict(response_mime_type="application/json",
              media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
              thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET))
    if schema:
        kw["response_json_schema"] = schema
    return types.GenerateContentConfig(**kw)


def _finish_reason(r) -> str | None:
    """Finish reason of the first candidate when it is not a normal stop (MAX_TOKENS, SAFETY, RECITATION, ...)."""
    cands = getattr(r, "candidates", None) or []
    fr = getattr(cands[0], "finish_reason", None) if cands else None
    name = getattr(fr, "name", None) or (str(fr) if fr else None)
    return name if name and name not in ("STOP", "FINISH_REASON_UNSPECIFIED") else None


def generate_json(models: list[str], contents: list, schema: dict | None = None, client=None) -> AIResult:
    """Calls models[0] (with same-model retries), then later models only as a fallback. Returns the first parsed
    JSON object. Raises EngineError on a non-retryable error or when every model is exhausted."""
    client = client or _client()

    def attempt(m: str):
        r = client.models.generate_content(model=m, contents=contents, config=_gen_config(schema))
        u = getattr(r, "usage_metadata", None)
        usage = {k: getattr(u, k, None) for k in ("prompt_token_count", "candidates_token_count",
                                                  "thoughts_token_count", "total_token_count")} if u else {}
        text = r.text or ""
        if not text.strip() and (reason := _finish_reason(r)):
            raise EngineError(f"Gemini model {m} returned no text (finish reason {reason}); the same request would "
                              f"end the same way")
        return text, usage
    return base.run_models(models, attempt, "Gemini", NAME)


def delete_and_verify(client, name: str) -> dict:
    """files.delete, then the file must be gone: files.get fails with 404 or 403 (the Gemini File API answers 403
    PERMISSION_DENIED for a deleted name) and the name is absent from files.list.
    Returns {name, deleted, verified_gone, get_status, listed}."""
    out = {"name": name, "deleted": False, "verified_gone": False, "get_status": None, "listed": None}
    try:
        client.files.delete(name=name)
        out["deleted"] = True
    except Exception as e:
        out["delete_error"] = status_of(e)
    try:
        client.files.get(name=name)
        out["get_status"] = "200 (file still exists)"
        return out
    except Exception as e:
        out["get_status"] = status_of(e)
        if code_of(e) not in GONE_CODES:
            return out
    try:
        out["listed"] = any(f.name == name for f in client.files.list())
    except Exception as e:
        out["list_error"] = status_of(e)
        return out
    out["verified_gone"] = not out["listed"]
    return out


def _wait_active(client, f):
    t0 = time.time()
    while getattr(f.state, "name", str(f.state)) == "PROCESSING":
        if time.time() - t0 > FILE_ACTIVE_TIMEOUT_S:
            raise EngineError("uploaded file stayed PROCESSING")
        time.sleep(1.0)
        f = client.files.get(name=f.name)
    if getattr(f.state, "name", str(f.state)) == "FAILED":
        raise EngineError("uploaded file processing FAILED")
    return f


class GeminiProvider:
    """Parts -> Gemini contents: Text -> str, Image -> inline Part, Media -> File API upload (once per path)."""
    name = NAME
    capabilities = CAPABILITIES

    def __init__(self, client=None, models: list[str] | None = None, qa_models: list[str] | None = None,
                 fallback=None):
        self._client = client
        self.models = {"planning": models or PLANNING_MODELS, "qa": qa_models or QA_MODELS}
        self.fallback = fallback
        self.uploads: list = []
        self._by_path: dict[str, object] = {}

    @property
    def client(self):
        if self._client is None:
            self._client = _client()
        return self._client

    def _contents(self, parts: list[Part]) -> list:
        out: list = []
        for p in parts:
            if isinstance(p, Text):
                out.append(p.text)
            elif isinstance(p, Image):
                from google.genai import types
                out.append(types.Part.from_bytes(data=p.data, mime_type=p.mime))
            elif isinstance(p, Media):
                key = str(p.path)
                if key not in self._by_path:
                    f = self.client.files.upload(file=key, config={"mime_type": p.mime})
                    self.uploads.append(f)
                    self._by_path[key] = _wait_active(self.client, f)
                out.append(self._by_path[key])
        return out

    def generate_json(self, task: str, parts: list[Part], schema: dict | None, request_id: str = "",
                      attachments: dict | None = None) -> AIResult:
        t0 = time.perf_counter()
        try:
            contents = self._contents(parts)
            return generate_json(self.models[task], contents, schema, client=self.client)
        except EngineError as e:
            if self.fallback is None:
                raise
            primary = self.models[task][0]
            gemini_error = e
            gemini_tried = list(getattr(e, "tried", None) or [(primary, f"error {e}")])
            gemini_seconds = time.perf_counter() - t0
        try:
            r = self.fallback.generate_json(task, parts, schema, request_id, attachments)
        except EngineError as fallback_error:
            raise EngineError(f"Gemini call failed: {gemini_error}; fallback {self.fallback.name} call failed: "
                              f"{fallback_error}") from None
        statuses = ", ".join(status for _, status in gemini_tried)
        warning = (f"FALLBACK: {primary} did not answer ({statuses}); "
                   f"answer is from {base.qualified_model(r.provider, r.model)}")
        print(f"warning: {warning}", file=sys.stderr)
        return AIResult(r.data, r.model, gemini_tried + list(r.tried), round(gemini_seconds + r.seconds, 2),
                        r.usage, r.retries, True, warning, r.provider)

    def cleanup(self) -> list[dict]:
        out = [delete_and_verify(self.client, f.name) for f in self.uploads] if self.uploads else []
        self.uploads, self._by_path = [], {}
        cleanup = getattr(self.fallback, "cleanup", None)
        if callable(cleanup):
            records = cleanup()
            if isinstance(records, list):
                out += records
        return out
