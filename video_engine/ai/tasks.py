"""Provider-agnostic AI tasks: footage analysis (`engine director`) and Layer 3 visual QA (`--ai-qa`).

Footage goes through a PROXY_HEIGHT p, PROXY_FPS fps H.264 proxy (no audio) in the scratch root; a provider without
video input receives DIRECTOR_FRAMES frames sampled from that proxy. QA snapshots are inline PNGs.
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..gates.core import Finding
from ..util import EngineError
from .base import AIResult, Image, Media, Provider, Text

PROXY_HEIGHT = 360
PROXY_FPS = 5
DIRECTOR_FRAMES = 8


def make_proxy(src: Path, out: Path, start_s: float = 0.0, seconds: float | None = 5.0) -> Path:
    """360p 5 fps H.264 proxy, no audio. ffmpeg is spawned by the Rust core (`core.make_proxy`)."""
    import hypoverum_core_rs as core
    try:
        return Path(core.make_proxy(src, out, height=PROXY_HEIGHT, fps=PROXY_FPS, start_s=start_s, seconds=seconds,
                                    crf=30, threads=2, ffmpeg=config.FFMPEG))
    except (ValueError, OSError) as e:
        raise EngineError(f"proxy: {e}") from e


SHOTS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "start_s": {"type": "number"}, "end_s": {"type": "number"}, "description": {"type": "string"},
            "focus_box": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
            "suggested_camera": {"type": "string", "enum": ["static", "ken_burns_zoom_in", "ken_burns_zoom_out",
                                                             "pan_left", "pan_right", "pan_up", "pan_down"]},
            "has_watermark_or_text": {"type": "boolean"}},
            "required": ["start_s", "end_s", "description", "focus_box", "suggested_camera", "has_watermark_or_text"]}},
    },
    "required": ["summary", "shots"],
}

ANALYZE_PROMPT = (
    "Analyse this footage proxy for a 1080x1920 vertical documentary edit. Return JSON: summary (one sentence) and shots "
    "(one per visually distinct shot) with start_s, end_s, description, focus_box [ymin,xmin,ymax,xmax] on a 0-1000 grid "
    "for the main subject, suggested_camera, has_watermark_or_text.")


QA_SCHEMA = {
    "type": "object",
    "properties": {"segments": {"type": "array", "items": {"type": "object", "properties": {
        "seg": {"type": "integer"}, "overlap": {"type": "boolean"}, "cut_off_text": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}}},
        "required": ["seg", "overlap", "cut_off_text", "issues"]}}},
    "required": ["segments"],
}

QA_PROMPT = (
    "Each image is one frame of a vertical video segment; its segment number is given before it. For every image report "
    "overlap=true if any text label, card or pin overlaps another text element, cut_off_text=true if any text is cut by "
    "the frame edge, and list concrete issues (empty list if none). Return JSON.")


def director_request_id(src: Path, start_s: float) -> str:
    return f"director/{src.stem}_{start_s:g}s"


def analyze_footage(src: Path, workdir: Path, provider: Provider, start_s: float = 0.0, seconds: float = 5.0
                    ) -> AIResult:
    """Proxy -> planning model JSON (SHOTS_SCHEMA). Provider uploads are deleted and verified afterwards; the
    deletion records are in `result.usage["file_deletions"]`."""
    proxy = make_proxy(src, workdir / "proxy" / (src.stem + "_360p.mp4"), start_s, seconds)
    deletions: list[dict] = []
    try:
        parts = [Media(proxy, "video", "video/mp4", f"proxy {src.name} from {start_s:g}s", frames=DIRECTOR_FRAMES),
                 Text(ANALYZE_PROMPT)]
        r = provider.generate_json("planning", parts, SHOTS_SCHEMA, director_request_id(src, start_s))
    finally:
        deletions = provider.cleanup()
        proxy.unlink(missing_ok=True)
    r.usage["file_deletions"] = deletions
    return r


def qa_snapshots(paths: dict[int, str], provider: Provider, request_id: str = "aiqa/0", meta: dict | None = None
                 ) -> list[Finding]:
    """Layer 3 AI QA: snapshots inline to the provider's QA models. A provider error becomes an AIQA warn; an
    invalid agent response is an EngineError. `meta` receives provider, model, retries, fallback."""
    parts: list = [Text(QA_PROMPT)]
    for seg, p in sorted(paths.items()):
        parts += [Text(f"segment {seg}:"), Image(Path(p).read_bytes(), "image/png", f"segment {seg}")]
    try:
        r = provider.generate_json("qa", parts, QA_SCHEMA, request_id)
    except EngineError as e:
        if provider.name == "agent":
            raise
        return [Finding("AIQA", "warn", f"AI QA unavailable: {e}")]
    finally:
        provider.cleanup()
    if meta is not None:
        meta.update(provider=r.provider, model=r.model, retries=r.retries, fallback=r.fallback)
    out = []
    if r.warning:
        out.append(Finding("AIQA", "warn", r.warning))
    for item in r.data.get("segments", []):
        if item.get("overlap") or item.get("cut_off_text"):
            out.append(Finding("AIQA", "fail", f"{r.model}: {'; '.join(item.get('issues') or ['overlap/cut-off text'])}",
                               item.get("seg")))
    if not any(f.level == "fail" for f in out):
        out.append(Finding("AIQA", "pass", f"{r.model} (retries {r.retries}): no overlap or cut-off text in {len(paths)} snapshots"))
    return out
