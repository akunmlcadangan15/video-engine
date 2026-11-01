"""Profile watermark: a one-line text mark rendered to a PNG (Pillow) and overlaid by the final mux, top right
inside the safe area. Gate B01 checks it against every element box and caption before rendering."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, profiles
from .timeline import Timeline

FONT_FILES = {"inter": "Inter-Bold.otf", "title": "ArchivoBlack-Regular.ttf"}
SAFE_X, SAFE_TOP = 60, 100      # px at 1080 wide / 1920 high: same safe area as R63
SHADOW_BLUR = 0.08              # shadow blur radius, fraction of the font size
SHADOW_DY = 0.05                # shadow offset down, fraction of the font size
SHADOW_ALPHA = 0.6


@dataclass(frozen=True)
class Placement:
    x: int          # top-left of the PNG in output px
    y: int
    w: int
    h: int
    pad: int        # transparent margin around the text inside the PNG (room for the shadow)

    def text_rect(self) -> dict:
        """Box of the visible text in output px (shadow margin excluded), in R63 rect form."""
        return {"id": "watermark", "x": float(self.x + self.pad), "y": float(self.y + self.pad),
                "w": float(self.w - 2 * self.pad), "h": float(self.h - 2 * self.pad)}


def _font(wm: profiles.Watermark, H: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.FONTS_DIR / FONT_FILES[wm.font]), round(wm.px * H / 1920))


def placement(tl: Timeline) -> Placement | None:
    """Where the profile watermark goes (None = the profile has none). The text's right edge sits on the right edge
    of the safe area and its top on the top edge of the safe area."""
    wm = profiles.of(tl).watermark
    if wm is None:
        return None
    W, H = tl.settings.width, tl.settings.height
    font = _font(wm, H)
    l, t, r, b = font.getbbox(wm.text)
    pad = max(2, round(font.size * (SHADOW_BLUR * 3 + SHADOW_DY)))
    w, h = (r - l) + 2 * pad, (b - t) + 2 * pad
    x = round(W - SAFE_X * W / 1080) - (w - pad)
    y = round(SAFE_TOP * H / 1920) - pad
    return Placement(x, y, w, h, pad)


def render(tl: Timeline, path: Path) -> Placement | None:
    """Writes the watermark PNG (RGBA, straight alpha) to `path`; None and no file without a watermark."""
    pl = placement(tl)
    if pl is None:
        return None
    wm = profiles.of(tl).watermark
    font = _font(wm, tl.settings.height)
    l, t, _, _ = font.getbbox(wm.text)
    org = (pl.pad - l, pl.pad - t)
    a = round(255 * wm.opacity)
    img = Image.new("RGBA", (pl.w, pl.h), (0, 0, 0, 0))
    if wm.shadow:
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).text((org[0], org[1] + round(font.size * SHADOW_DY)), wm.text, font=font, fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(font.size * SHADOW_BLUR))
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow.putalpha(mask.point(lambda v: round(v * SHADOW_ALPHA * wm.opacity)))
        img = Image.alpha_composite(img, shadow)
    rgb = tuple(int(wm.colour[i:i + 2], 16) for i in (1, 3, 5))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(org, wm.text, font=font, fill=(*rgb, a))
    img = Image.alpha_composite(img, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return pl
