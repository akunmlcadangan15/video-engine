"""Agent provider: the model is an agent or a human editor working from files; no API.

Each request is a self-contained bundle directory `<root>/<request_id>/`:
  request.md         task, the exact prompt text in request order, image references, answer instructions
  schema.json        JSON schema the answer must satisfy ({} = free JSON object)
  images/NN_*.jpg    every image part (thumbnails, snapshots, sampled video frames), numbered in request order
  contact_sheet.jpg  all images on one labelled grid (when there are 2 or more)
  transcript.json    narration words with start/end frames (when the request has narration audio)
  <attachment>       request-specific files (draft: inventory.json, credits.json; repair rounds: gate_errors.txt)
  response.json      written by the agent: the answer, valid against schema.json
A request without response.json raises AwaitingAgent (CLI exit code 3). On resume the response is parsed and
validated against schema.json; invalid JSON or a schema mismatch is an EngineError naming the file and the errors.
"""
from __future__ import annotations

import io
import json
import re
import shutil
from pathlib import Path

from .base import AIResult, AwaitingAgent, Image, Media, Part, Text, sample_frames, schema_errors
from ..util import EngineError

NAME = "agent"
EXIT_AWAITING = 3
MODEL_LABEL = "grok-bot"
SHEET_TILE = 256
SHEET_COLS = 6


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")[:60] or "image"


def contact_sheet(images: list[tuple[str, bytes]]) -> bytes:
    from PIL import Image as PImage, ImageDraw
    n = len(images)
    cols = min(SHEET_COLS, n)
    rows = -(-n // cols)
    t = SHEET_TILE
    sheet = PImage.new("RGB", (cols * t, rows * (t + 18)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for k, (label, data) in enumerate(images):
        im = PImage.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((t, t))
        x, y = (k % cols) * t, (k // cols) * (t + 18)
        sheet.paste(im, (x + (t - im.width) // 2, y + 18 + (t - im.height) // 2))
        d.text((x + 3, y + 3), f"{k + 1:02d} {label}"[:40], fill=(255, 230, 90))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    return buf.getvalue()


class AgentProvider:
    name = NAME
    capabilities = {"image"}

    def __init__(self, root: Path, resume_cmd: str, resume: bool = False, model_label: str = MODEL_LABEL):
        self.root, self.resume_cmd, self.resume, self.model_label = Path(root), resume_cmd, resume, model_label

    def reset(self, sub: str) -> None:
        """Drop earlier bundles under <root>/<sub> unless resuming."""
        if not self.resume:
            shutil.rmtree(self.root / sub, ignore_errors=True)

    def generate_json(self, task: str, parts: list[Part], schema: dict | None, request_id: str,
                      attachments: dict | None = None) -> AIResult:
        d = self.root / request_id
        resp = d / "response.json"
        if resp.is_file():
            return self._read(resp, schema)
        if (d / "request.md").is_file() and self.resume:
            raise EngineError(f"no response.json in {d}: write the answer there (valid against schema.json), "
                              f"then run: {self.resume_cmd}")
        self.write_bundle(d, task, parts, schema, attachments or {})
        raise AwaitingAgent(d, self.resume_cmd)

    def _read(self, resp: Path, schema: dict | None) -> AIResult:
        try:
            data = json.loads(resp.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise EngineError(f"{resp}: invalid JSON ({e})") from None
        if not isinstance(data, dict):
            raise EngineError(f"{resp}: top level must be a JSON object")
        errs = schema_errors(data, schema or {})
        if errs:
            more = f"\n  ... {len(errs) - 20} more" if len(errs) > 20 else ""
            raise EngineError(f"{resp} does not match schema.json:\n  " + "\n  ".join(errs[:20]) + more)
        return AIResult(data, self.model_label, [(self.model_label, "ok")], 0.0, {}, provider=NAME)

    def write_bundle(self, d: Path, task: str, parts: list[Part], schema: dict | None, attachments: dict) -> None:
        shutil.rmtree(d, ignore_errors=True)
        (d / "images").mkdir(parents=True)
        (d / "schema.json").write_text(json.dumps(schema or {}, indent=1, ensure_ascii=False))
        body: list[str] = []
        images: list[tuple[str, bytes]] = []

        def add_image(data: bytes, mime: str, label: str) -> None:
            ext = "png" if mime == "image/png" else "jpg"
            fn = f"images/{len(images) + 1:02d}_{_slug(label)}.{ext}"
            (d / fn).write_bytes(data)
            images.append((label, data))
            body.append(f"![{label}]({fn})")

        for p in parts:
            if isinstance(p, Text):
                body.append(p.text)
            elif isinstance(p, Image):
                add_image(p.data, p.mime, p.label or "image")
            elif isinstance(p, Media) and p.kind == "audio":
                if p.words is not None:
                    (d / "transcript.json").write_text(json.dumps(p.words, indent=0, ensure_ascii=False))
                body.append(f"[{p.label or p.path.name}: audio; transcript with word frames in transcript.json]\n"
                            f"{p.transcript or ''}")
            elif isinstance(p, Media):
                for t, jpg in sample_frames(p.path, p.frames):
                    add_image(jpg, "image/jpeg", f"{p.label or p.path.stem} t={t:.2f}s")
        for name, obj in attachments.items():
            (d / name).write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1, ensure_ascii=False))
        if len(images) >= 2:
            (d / "contact_sheet.jpg").write_bytes(contact_sheet(images))
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and p.parent == d)
        head = (f"# AI request: {task} ({d.name})\n\n"
                f"Answer this request as the model. Write ONE JSON object to `response.json` in this directory, valid "
                f"against `schema.json`, then run:\n\n    {self.resume_cmd}\n\n"
                f"Files: {', '.join(files)}; images/ ({len(images)} images, numbered in request order).\n\n"
                f"---\n\n# Request (exact prompt, in order)\n\n")
        (d / "request.md").write_text(head + "\n\n".join(body) + "\n")

    def cleanup(self) -> list[dict]:
        return []
