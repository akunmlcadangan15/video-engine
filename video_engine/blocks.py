"""Deterministic infographic blocks: chart, timeline and stat-card PNGs with transparency.

They are ordinary image assets (usable as a native still, a prop or a layer), so a documentary is no longer map-only.
Data is declarative: {"title": "...", "bars": [{"label": "...", "value": 12}]}, {"events": [{"label": "...", "at": 0.3}]}
or {"items": [{"label": "...", "value": "42%", "detail": "..."}]}. No browser or network is used.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .util import EngineError

KINDS = ("chart", "timeline", "infographic")
FONTS = {"bold": "Inter-Bold.otf", "regular": "Inter-SemiBold.otf"}
INK = "#1d2433"
MUTED = "#657084"
PAPER = "#f7f3ea"


def _font(role: str, size: int):
    from PIL import ImageFont
    f = config.FONTS_DIR / FONTS[role]
    try:
        return ImageFont.truetype(str(f), size)
    except OSError as e:
        raise EngineError(f"font {f} not found; run `engine doctor --install`") from e


def _text(draw, xy, text, font, fill, anchor="la"):
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)


def _fit(text: str, font, width: int) -> str:
    text = str(text)
    while text and font.getlength(text) > width:
        text = text[:-1]
    return text.rstrip() + ("..." if text != str(text) and len(text) > 3 else "")


def _items(data, key: str) -> tuple[list, str | None]:
    """(rows, title) of a declarative block: either a list of rows or an object with `key` and an optional title."""
    if isinstance(data, list):
        return data, None
    return list(data.get(key) or []), data.get("title")


def _title(draw, title, w, h, scale):
    if title:
        _text(draw, (w * 0.06, h * 0.06), title, _font("bold", int(64 * scale)), INK)


def chart(data: dict, out: Path, width: int = 1080, height: int = 720, *, colour: str = "#4B4D8B",
          background: str | None = None) -> Path:
    from PIL import Image, ImageDraw
    bars, title = _items(data, "bars")
    if not bars:
        raise EngineError("chart needs at least one bar")
    if any(not isinstance(b, dict) or "label" not in b for b in bars):
        raise EngineError("each chart bar needs label and value")
    try:
        values = [float(b["value"]) for b in bars]
    except (KeyError, TypeError, ValueError):
        raise EngineError("each chart bar needs a numeric value") from None
    scale = min(width, height) / 720
    im = Image.new("RGBA", (width, height), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _title(d, title, width, height, scale)
    top, left = height * 0.20, width * 0.32
    right, bottom = width * 0.94, height * 0.90
    maxv = max([v for v in values if v > 0] or [1.0])
    row = (bottom - top) / max(len(bars), 1)
    f = _font("regular", int(30 * scale))
    fb = _font("bold", int(32 * scale))
    for i, (b, value) in enumerate(zip(bars, values)):
        y = top + i * row + row * 0.22
        bh = row * 0.42
        label = _fit(b["label"], f, int(left - width * 0.07))
        _text(d, (left - width * 0.02, y + bh / 2), label, f, INK, anchor="rm")
        bw = max(2, (right - left) * max(0.0, value) / maxv)
        d.rounded_rectangle((left, y, left + bw, y + bh), radius=int(bh * 0.25), fill=colour)
        _text(d, (left + bw + width * 0.012, y + bh / 2), b.get("display", b["value"]), fb, INK, anchor="lm")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, format="PNG", optimize=False, compress_level=6)
    return out


def timeline(data: dict, out: Path, width: int = 1080, height: int = 720, *, colour: str = "#9C443B",
             background: str | None = None) -> Path:
    from PIL import Image, ImageDraw
    events, title = _items(data, "events")
    if len(events) < 2:
        raise EngineError("timeline needs at least two events")
    if any(not isinstance(e, dict) or "label" not in e for e in events):
        raise EngineError("each timeline event needs label and at")
    try:
        for e in events:
            e["at"] = float(e["at"])
    except (KeyError, TypeError, ValueError) as e:
        raise EngineError("each timeline event needs a label and a numeric at") from None
    scale = min(width, height) / 720
    im = Image.new("RGBA", (width, height), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _title(d, title, width, height, scale)
    x0, x1 = width * 0.08, width * 0.92
    y = height * 0.55
    d.rounded_rectangle((x0, y - int(4 * scale), x1, y + int(4 * scale)), radius=int(4 * scale), fill=colour)
    f = _font("regular", int(26 * scale))
    fb = _font("bold", int(30 * scale))
    events = sorted(events, key=lambda e: float(e["at"]))
    lo, hi = min(float(e["at"]) for e in events), max(float(e["at"]) for e in events)
    span = hi - lo or 1.0
    for i, e in enumerate(events):
        x = x0 + (x1 - x0) * (float(e["at"]) - lo) / span
        up = i % 2 == 0
        ty = y - height * 0.20 if up else y + height * 0.20
        d.ellipse((x - 12 * scale, y - 12 * scale, x + 12 * scale, y + 12 * scale), fill=colour)
        d.line((x, y - 14 * scale, x, ty + (18 * scale if up else -18 * scale)), fill=colour,
               width=max(1, int(3 * scale)))
        label = _fit(e["label"], fb, int(width * 0.25))
        _text(d, (x, ty), label, fb, INK, anchor="ms" if up else "ma")
        if e.get("detail"):
            _text(d, (x, ty + (36 * scale if up else -36 * scale)), _fit(e["detail"], f, int(width * 0.25)),
                  f, MUTED, anchor="ms" if up else "ma")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, format="PNG", optimize=False, compress_level=6)
    return out


def infographic(data: dict, out: Path, width: int = 1080, height: int = 720, *, colour: str = "#136868",
                background: str | None = None) -> Path:
    from PIL import Image, ImageDraw
    items, title = _items(data, "items")
    if not items:
        raise EngineError("infographic needs at least one item")
    for it in items:
        if not isinstance(it, dict) or "label" not in it or "value" not in it:
            raise EngineError("each infographic item needs label and value")
    scale = min(width, height) / 720
    im = Image.new("RGBA", (width, height), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _title(d, title, width, height, scale)
    cols = 2 if len(items) > 1 else 1
    rows = (len(items) + cols - 1) // cols
    gap = width * 0.04
    left, top = width * 0.06, height * 0.20
    cw = (width * 0.88 - gap * (cols - 1)) / cols
    ch = (height * 0.74 - gap * (rows - 1)) / rows
    fv = _font("bold", int(72 * scale))
    fl = _font("regular", int(28 * scale))
    fd = _font("regular", int(24 * scale))
    for i, it in enumerate(items):
        x = left + (i % cols) * (cw + gap)
        y = top + (i // cols) * (ch + gap)
        d.rounded_rectangle((x, y, x + cw, y + ch), radius=int(24 * scale), fill=(255, 255, 255, 210),
                            outline=colour, width=max(1, int(4 * scale)))
        d.rounded_rectangle((x, y, x + int(10 * scale), y + ch), radius=int(5 * scale), fill=colour)
        _text(d, (x + cw * 0.08, y + ch * 0.20), _fit(it["value"], fv, int(cw * 0.84)), fv, colour)
        _text(d, (x + cw * 0.08, y + ch * 0.58), _fit(it["label"], fl, int(cw * 0.84)), fl, INK)
        if it.get("detail"):
            _text(d, (x + cw * 0.08, y + ch * 0.78), _fit(it["detail"], fd, int(cw * 0.84)), fd, MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, format="PNG", optimize=False, compress_level=6)
    return out


def render(kind: str, data, out: Path, **kw) -> Path:
    if kind not in KINDS:
        raise EngineError(f"unknown block {kind!r}; choose {', '.join(KINDS)}")
    if isinstance(data, (str, Path)):
        raw = str(data)
        p = Path(raw[1:] if raw.startswith("@") else raw)
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            raise EngineError(f"block data is not valid JSON: {e}") from None
    if not isinstance(data, (dict, list)):
        raise EngineError("block data must be a JSON object or array")
    return {"chart": chart, "timeline": timeline, "infographic": infographic}[kind](data, Path(out), **kw)


def render_svg(kind: str, data: dict | list, width: int = 1080, height: int = 720, *,
               colour: str | None = None, background: str | None = None) -> str:
    """Render a declarative block ('chart', 'timeline', 'infographic') as native scalable SVG markup for direct
    embedding inside HyperFrames HTML compositions (no Pillow rasterisation needed)."""
    import html as _h
    if kind not in KINDS:
        raise EngineError(f"unknown block kind {kind!r} (known: {', '.join(KINDS)})")
    bg_rect = f'<rect width="{width}" height="{height}" fill="{_h.escape(background)}"/>' if background else ""
    if kind == "chart":
        bars, title = _items(data, "bars")
        if not bars:
            raise EngineError("chart needs at least one bar")
        vals = [float(b["value"]) for b in bars]
        maxv = max([v for v in vals if v > 0] or [1.0])
        col = colour or "#4B4D8B"
        top, left, right, bottom = height * 0.20, width * 0.32, width * 0.94, height * 0.90
        row = (bottom - top) / max(len(bars), 1)
        els = [bg_rect]
        if title:
            els.append(f'<text x="{width*0.06:.1f}" y="{height*0.12:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="48">{_h.escape(str(title))}</text>')
        for i, (b, v) in enumerate(zip(bars, vals)):
            y = top + i * row + row * 0.22
            bh = row * 0.42
            bw = max(2.0, (right - left) * max(0.0, v) / maxv)
            els.append(f'<text x="{left - width*0.02:.1f}" y="{y + bh/2:.1f}" fill="{INK}" font-family="EngInterS,sans-serif" font-size="28" text-anchor="end" dominant-baseline="middle">{_h.escape(str(b["label"]))}</text>')
            els.append(f'<rect class="bar" x="{left:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{bh*0.25:.1f}" fill="{_h.escape(col)}"/>')
            els.append(f'<text x="{left + bw + width*0.012:.1f}" y="{y + bh/2:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="30" dominant-baseline="middle">{_h.escape(str(b.get("display", b["value"])))}</text>')
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">' + "".join(els) + "</svg>"
    if kind == "timeline":
        events, title = _items(data, "events")
        if len(events) < 2:
            raise EngineError("timeline needs at least two events")
        col = colour or "#9C443B"
        x0, x1, y = width * 0.08, width * 0.92, height * 0.55
        els = [bg_rect, f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" stroke="{_h.escape(col)}" stroke-width="8" stroke-linecap="round"/>']
        if title:
            els.append(f'<text x="{width*0.06:.1f}" y="{height*0.12:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="48">{_h.escape(str(title))}</text>')
        for i, e in enumerate(events):
            at = min(1.0, max(0.0, float(e["at"])))
            cx = x0 + (x1 - x0) * at
            ly = y - 36 if i % 2 == 0 else y + 52
            els.append(f'<circle cx="{cx:.1f}" cy="{y:.1f}" r="14" fill="{_h.escape(col)}"/>')
            els.append(f'<text x="{cx:.1f}" y="{ly:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="26" text-anchor="middle">{_h.escape(str(e["label"]))}</text>')
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">' + "".join(els) + "</svg>"
    items, title = _items(data, "items")
    if not items:
        raise EngineError("infographic needs at least one item")
    col = colour or "#136868"
    els = [bg_rect]
    if title:
        els.append(f'<text x="{width*0.06:.1f}" y="{height*0.12:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="48">{_h.escape(str(title))}</text>')
    top = height * (0.22 if title else 0.10)
    gap = width * 0.03
    cw = (width * 0.88 - gap * (len(items) - 1)) / max(len(items), 1)
    ch = height * 0.68
    for i, it in enumerate(items):
        cx = width * 0.06 + i * (cw + gap)
        els.append(f'<rect x="{cx:.1f}" y="{top:.1f}" width="{cw:.1f}" height="{ch:.1f}" rx="20" fill="{PAPER}"/>')
        els.append(f'<text x="{cx + 28:.1f}" y="{top + ch*0.38:.1f}" fill="{_h.escape(col)}" font-family="EngInter,sans-serif" font-weight="700" font-size="56">{_h.escape(str(it["value"]))}</text>')
        els.append(f'<text x="{cx + 28:.1f}" y="{top + ch*0.62:.1f}" fill="{INK}" font-family="EngInter,sans-serif" font-weight="700" font-size="30">{_h.escape(str(it["label"]))}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">' + "".join(els) + "</svg>"
