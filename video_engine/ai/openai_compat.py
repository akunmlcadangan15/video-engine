"""OpenAI-compatible chat-completions provider for models registered in the AI config file.

Request: POST {base_url}/chat/completions, `Authorization: Bearer $<api_key_env>`, one user message whose content is
a list of `text` / `image_url` (base64 data URL) items; `input_audio` (base64 mp3/wav) when the entry has the
`audio` capability; `video_url` (base64 data URL) when it has `video`. Parts the model cannot take are degraded
(`base.degrade`): audio -> transcript text, video -> sampled JPEG frames, images dropped with a text note.
Output: `response_format` `json_schema` (schema attached, strict false) or `json_object` (`json_mode` entry).
Retry/fallback rules: `video_engine.ai.base`. The key value is read from the environment at call time and never
written to logs, reports or error messages.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field

from . import base
from .base import AIResult, Image, Media, Part, Text
from ..util import EngineError


@dataclass
class Entry:
    name: str
    base_url: str
    models: list[str]
    api_key_env: str
    qa_models: list[str] = field(default_factory=list)
    capabilities: set[str] = field(default_factory=lambda: {"image"})
    json_mode: str = "json_schema"        # json_schema | json_object
    timeout_s: float = base.HTTP_TIMEOUT_S

    @classmethod
    def from_config(cls, name: str, d: dict) -> "Entry":
        missing = [k for k in ("base_url", "models", "api_key_env") if not d.get(k)]
        if missing:
            raise EngineError(f"AI provider '{name}': missing {', '.join(missing)} in the AI config file")
        caps = set(d.get("capabilities", ["image"]))
        bad = caps - {"image", "audio", "video"}
        if bad:
            raise EngineError(f"AI provider '{name}': unknown capabilities {sorted(bad)} (image, audio, video)")
        mode = d.get("json_mode", "json_schema")
        if mode not in ("json_schema", "json_object"):
            raise EngineError(f"AI provider '{name}': json_mode must be json_schema or json_object")
        return cls(name, d["base_url"].rstrip("/"), list(d["models"]), d["api_key_env"], list(d.get("qa_models") or []),
                   caps, mode, float(d.get("timeout_s", base.HTTP_TIMEOUT_S)))


class HTTPError(Exception):
    def __init__(self, code: int, status: str | None = None):
        super().__init__(f"HTTP {code}" + (f" {status}" if status else ""))
        self.code, self.status = code, status


def _post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, dict]:
    import httpx
    r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class OpenAICompatProvider:
    def __init__(self, entry: Entry, post=None):
        self.entry = entry
        self.name = entry.name
        self.capabilities = entry.capabilities
        self.post = post or _post
        self.models = {"planning": entry.models, "qa": entry.qa_models or entry.models}

    def _key(self) -> str:
        k = os.environ.get(self.entry.api_key_env)
        if not k:
            raise EngineError(f"AI provider '{self.name}': environment variable {self.entry.api_key_env} is not set")
        return k

    def content(self, parts: list[Part]) -> list[dict]:
        out = []
        for p in base.degrade(parts, self.capabilities):
            if isinstance(p, Text):
                out.append({"type": "text", "text": p.text})
            elif isinstance(p, Image):
                out.append({"type": "image_url", "image_url": {"url": f"data:{p.mime};base64,{_b64(p.data)}"}})
            elif isinstance(p, Media) and p.kind == "audio":
                fmt = "wav" if p.path.suffix.lower() == ".wav" else "mp3"
                out.append({"type": "input_audio", "input_audio": {"data": _b64(p.path.read_bytes()), "format": fmt}})
            elif isinstance(p, Media):
                out.append({"type": "video_url", "video_url": {"url": f"data:{p.mime};base64,{_b64(p.path.read_bytes())}"}})
        return out

    def payload(self, model: str, content: list[dict], schema: dict | None) -> dict:
        body = {"model": model, "messages": [{"role": "user", "content": content}]}
        if schema and self.entry.json_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "response", "schema": schema, "strict": False}}
        else:
            body["response_format"] = {"type": "json_object"}
            if schema:
                content.append({"type": "text", "text": "Return JSON matching this schema:\n" + json.dumps(schema)})
        return body

    def generate_json(self, task: str, parts: list[Part], schema: dict | None, request_id: str = "",
                      attachments: dict | None = None) -> AIResult:
        key = self._key()
        content = self.content(parts)
        url = f"{self.entry.base_url}/chat/completions"

        def attempt(m: str):
            status, body = self.post(url, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     self.payload(m, list(content), schema), self.entry.timeout_s)
            if status != 200:
                err = body.get("error") if isinstance(body, dict) else None
                st = (err.get("status") or err.get("code")) if isinstance(err, dict) else None
                raise HTTPError(status, st if isinstance(st, str) else None)
            msg = ((body.get("choices") or [{}])[0].get("message") or {})
            return msg.get("content") or "", body.get("usage") or {}
        return base.run_models(self.models[task], attempt, self.name, self.name)

    def cleanup(self) -> list[dict]:
        return []
