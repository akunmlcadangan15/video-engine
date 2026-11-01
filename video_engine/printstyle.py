"""Profile [print]: printed typography on map scenes.

The surface of a scene decides the style: a light map (median APCA Y of the map image >= print.paper_min_y) is paper,
where text is dark ink printed into the map (EB Garamond, Playfair, Cinzel, Kalam; multiply, no halo, no glow, no
box); anything darker is dark imagery, where text is light with a soft shadow (Inter Bold, Playfair Bold). `roles`
names the font of every text kind per surface; `TYPEFACE` maps the template CSS families to typefaces (gate T01)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# template CSS family -> typeface (T01 counts typefaces, not files or weights)
TYPEFACE = {"EngInter": "Inter", "EngInterS": "Inter", "EngSerif": "Playfair Display", "EngGaramond": "EB Garamond",
            "EngGaramondI": "EB Garamond", "EngCinzel": "Cinzel", "EngHand": "Kalam", "EngTitle": "Archivo Black",
            "EngScript": "Kaushan Script"}


def typeface(css_family: str) -> str:
    """Typeface of a computed CSS font-family list (its first family)."""
    first = css_family.split(",")[0].strip().strip("'\"")
    return TYPEFACE.get(first, first)


@lru_cache(maxsize=16)
def _map_y(path: str, mtime: float) -> float:
    import numpy as np
    from PIL import Image

    from . import legibility
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        # Draft first: a full decode of a 43200 px map plus its RGB copy is
        # ~5.6 GB and got preflights OOM-killed. JPEG draft decodes at 1/8.
        im.draft("RGB", (1024, 1024))
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        px = np.asarray(im, dtype=float).reshape(-1, 3)
    return float(np.median(legibility.apca_y(px)))


def map_y(map_path: Path) -> float:
    """Median APCA Y of the map image."""
    return _map_y(str(map_path), map_path.stat().st_mtime)


def surface(map_path: Path, pr) -> str:
    """"paper" or "dark"."""
    return "paper" if map_y(map_path) >= pr.paper_min_y else "dark"


def roles(surf: str, pr) -> dict:
    """Text kind -> (font role, weight, tracking em, caps) on a surface. Roles are hyperframes.THEME_FONTS / layout
    FONT_FILES keys ("inter" = Inter Bold)."""
    if surf == "paper":
        return {"title": ("garamond", pr.title_weight, pr.title_tracking, True),
                "area": ("garamond", pr.title_weight, pr.title_tracking, True),
                "label": ("garamond_italic", pr.label_weight, 0.0, False),
                "year": ("serif", pr.year_weight, 0.0, False),
                "time": ("cinzel", 600, pr.time_tracking, True),
                "heading": ("cinzel", pr.heading_weight, pr.heading_tracking, True),
                "note": ("hand", 700, 0.0, False)}
    return {"title": ("inter", 700, pr.dark_tracking, True),
            "area": ("inter", 700, pr.dark_tracking, True),
            "label": ("inter", 700, 0.0, False),
            "year": ("serif", pr.year_dark_weight, 0.0, False),
            "time": ("cinzel", 600, pr.time_tracking, True),
            "heading": ("cinzel", pr.heading_weight, pr.heading_tracking, True),
            "note": ("hand", 700, 0.0, False)}


def edge_ink(colour: str, pr, surf: str = "paper") -> str:
    """Solid region edge: the entity colour at OKLCH L = print.region_edge_l on paper, region_edge_l_dark on a dark
    surface (same chroma and hue, gamut-mapped)."""
    from . import legibility
    _, C, h = legibility.rgb_oklch(legibility.hex_rgb(colour))
    return legibility.oklch_hex(pr.region_edge_l if surf == "paper" else pr.region_edge_l_dark, C, h)


def scene_block(pr, surf: str) -> dict:
    """`print` block of the scene data: the profile values, the surface and the roles."""
    return {**pr.model_dump(mode="json"), "surface": surf,
            "roles": {k: list(v) for k, v in roles(surf, pr).items()}}
