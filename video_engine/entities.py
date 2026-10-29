"""Entity colours: one colour per entity for the whole video. Profile theme.entities first, then the next unused
palette colour in order of first appearance in the timeline (segment order; in a segment regions, title, figures)."""
from __future__ import annotations

from . import profiles
from .timeline import HYPERFRAMES, Timeline


def names(tl: Timeline) -> list[str]:
    """Entities in order of first appearance."""
    out: list[str] = []
    for s in tl.segments:
        if s.engine_type == HYPERFRAMES:
            sc = s.gsap_scene_config
            for e in [*(r.entity for r in sc.regions or []), sc.title_entity, *(f.entity for f in sc.figures or [])]:
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
