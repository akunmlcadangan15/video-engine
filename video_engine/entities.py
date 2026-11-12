"""Entity colours: one colour per entity for the whole video. Profile theme.entities first, then the next unused
palette colour in order of first appearance in the timeline (segment order; in a segment regions, title, figures)."""
from __future__ import annotations

from pathlib import Path

from . import profiles
from .timeline import HYPERFRAMES, Timeline

VIVID_SOURCE_L = (0.38, 0.84)   # OKLCH L band of a muted palette (tests/test_geoverum_profile.py), mapped onto vivid_l


def names(tl: Timeline) -> list[str]:
    """Entities in order of first appearance."""
    out: list[str] = []
    for s in tl.segments:
        if s.engine_type == HYPERFRAMES:
            sc = s.gsap_scene_config
            assert sc is not None
            for e in [*(e for r in sc.regions or [] for e in (r.entity, *(c.entity for c in r.changes))),
                      sc.title_entity, *(f.entity for f in sc.figures or []), *(c.entity for c in sc.cards)]:
                if e and e not in out:
                    out.append(e)
    return out


def colours(tl: Timeline) -> dict[str, str]:
    """entity -> lower-case "#rrggbb". When the palette runs out it repeats from the start (E01 warns)."""
    th = profiles.of(tl).theme
    fixed = {k: v.lower() for k, v in th.entities.items()}
    out: dict[str, str] = {}
    free = [c.lower() for c in th.palette if c.lower() not in fixed.values()]
    k = 0
    for e in names(tl):
        if e in fixed:
            out[e] = fixed[e]
            continue
        taken = set(out.values())
        pick = next((c for c in free if c not in taken), None)
        if pick is None:
            pick = th.palette[k % len(th.palette)].lower()
            k += 1
        out[e] = pick
    return out


def vivid_colour(colour: str, th) -> str:
    """An entity colour on a dark satellite map (theme vivid_chroma_scale set): same hue, chroma x vivid_chroma_scale
    (gamut-mapped by legibility.oklch_hex), OKLCH L mapped linearly from VIVID_SOURCE_L onto th.vivid_l and clamped.
    Scaling keeps the palette's chroma and lightness differences, so distinct palette colours stay distinct."""
    from . import legibility
    L, C, h = legibility.rgb_oklch(legibility.hex_rgb(colour))
    (s0, s1), (lo, hi) = VIVID_SOURCE_L, th.vivid_l
    return legibility.oklch_hex(min(max(lo + (L - s0) * (hi - lo) / (s1 - s0), lo), hi), C * th.vivid_chroma_scale, h)


def dark_map(seg, tl: Timeline, project_dir) -> bool:
    """Whether seg's map is a dark satellite surface: the profile has no [print], or [print] classifies it "dark"."""
    prof = profiles.of(tl)
    if prof.print is None:
        return True
    from . import printstyle
    return printstyle.surface(Path(project_dir) / seg.gsap_scene_config.map_asset, prof.print) == "dark"


def scene_colours(seg, tl: Timeline, project_dir) -> dict[str, str]:
    """entity -> colour as seg's HyperFrames scene paints it: `colours`, made vivid on a dark map when the theme sets
    vivid_chroma_scale (paper maps keep the palette colours). Gate E01 compares painted fills with these."""
    col, th = colours(tl), profiles.of(tl).theme
    if th.vivid_chroma_scale and dark_map(seg, tl, project_dir):
        return {e: vivid_colour(c, th) for e, c in col.items()}
    return col


def block_colour(colour: str, theme: dict) -> str:
    """Face colour of a "block" region: the entity colour with OKLCH L clamped to theme block_l_min..block_l_max and
    chroma raised to at least block_chroma (same hue, gamut-mapped)."""
    from . import legibility
    L, C, h = legibility.rgb_oklch(legibility.hex_rgb(colour))
    return legibility.oklch_hex(min(max(L, theme["block_l_min"]), theme["block_l_max"]), max(C, theme["block_chroma"]), h)


def box_colour(colour: str, theme: dict) -> str:
    """Box of a label in an entity's colour: its block colour, darkened in OKLCH L steps of 0.01 until white text
    reaches APCA |Lc| >= theme label_box_lc."""
    from . import legibility
    L, C, h = legibility.rgb_oklch(legibility.hex_rgb(block_colour(colour, theme)))
    yw = float(legibility.apca_y(legibility.hex_rgb("#ffffff")))
    while True:
        c = legibility.oklch_hex(L, C, h)
        if abs(float(legibility.apca_lc(yw, float(legibility.apca_y(legibility.hex_rgb(c)))))) >= theme["label_box_lc"] or L <= 0.05:
            return c
        L -= 0.01
