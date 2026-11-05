"""Caption stage: caption words -> .ass via the Rust compiler. Text/style edits only rerun this (milliseconds).

The effective caption style is the profile caption_style with the project's caption_style on top (profiles.py).
Keys handled in Python (not passed to Rust):
  y_overrides  [[start_frame, end_frame, y_px], ...]: captions starting in [start, end) are centred at y_px
  auto_place   bool: pre-flight moves captions that violate the clearance from element boxes
  clearance    px at the reference canvas height (1920 portrait, 1080 landscape) kept between a caption box and an element box (0 = must not intersect)
  forbid_box   bool: caption_style.box_bg must not be true (captions never use a backing box)
  ab_sizes     [px, ...]: A/B size candidates (validated, Python only; the effective `size` is what renders)
  fixed_y      px at the reference canvas height (scaled to the output height): centre y of every caption that no y_override or
               placement override moves; absent = the Rust slot y
  place_band   [lo, hi] fractions of the output height: automatic placement (auto_place) keeps every moved caption
               centre inside this band (the approved caption band); absent = anywhere in the safe area
  max_bottom_y px at the reference canvas height (scaled to the output height): lowest allowed caption box bottom (Z01; automatic
               placement never moves a caption below it); absent = no limit
All other keys are Rust Style fields (unknown keys raise ValueError).

Landscape frames (units.py): the Rust length keys (LENGTH_KEYS) are px at the 1920x1080 reference canvas and are
scaled by the uniform k before they reach Rust and the R62 gate (effective_style); a landscape style without `size` /
`margin_x` gets LANDSCAPE_DEFAULTS instead of the portrait Rust defaults (84 / 90). Portrait styles pass unchanged."""
from __future__ import annotations

from pathlib import Path

import hypoverum_core_rs as core

from . import profiles
from .units import units
from .timeline import MAX_FRAME, CaptionWord, Timeline

PY_KEYS = ("y_overrides", "auto_place", "place_band", "clearance", "fixed_y", "max_bottom_y", "forbid_box", "ab_sizes")


LENGTH_KEYS = ("size", "margin_x", "outline", "shadow", "shadow_blur", "shadow_dx", "shadow_dy")
# px at 1920x1080. size/margin_x replace the portrait Rust defaults (84, 90 at 1080x1920); outline/shadow/shadow_blur
# repeat the Rust defaults so they scale with k like explicit values (caption_pad assumes the same numbers).
LANDSCAPE_DEFAULTS = {"size": 58, "margin_x": 160, "outline": 5.0, "shadow": 3.0, "shadow_blur": 6.0}


def effective_style(tl: Timeline) -> dict:
    """profiles.caption_style with the Rust length keys in output px. Portrait: unchanged (the Rust compiler and the
    gates use the values as given, as before). Landscape: LANDSCAPE_DEFAULTS for absent size/margin_x, then every
    length key (and ab_sizes) times units.k."""
    st = profiles.caption_style(tl)
    u = units(tl)
    if not u.landscape:
        return st
    st = {**LANDSCAPE_DEFAULTS, **st}
    k = u.k
    for key in LENGTH_KEYS:
        if st.get(key) is not None and not isinstance(st[key], bool):
            st[key] = float(st[key]) * k
    if st.get("ab_sizes"):
        st["ab_sizes"] = [float(v) * k for v in st["ab_sizes"]]
    return st


def rust_style(tl: Timeline) -> dict | None:
    st = {k: v for k, v in effective_style(tl).items() if k not in PY_KEYS}
    return st or None


def overrides(tl: Timeline, extra: list | None = None) -> list[tuple[int, int, float]]:
    st = profiles.caption_style(tl)
    ov = [tuple(x) for x in st.get("y_overrides", [])] + [tuple(x) for x in (extra or [])]
    if st.get("fixed_y") is not None:   # last: the first matching override wins in the Rust compiler
        ov.append((0, MAX_FRAME - 1, units(tl).y(float(st["fixed_y"]))))
    return [(int(a), int(b), float(y)) for a, b, y in ov]


def compile_ass(words: list[CaptionWord], tl: Timeline, y_overrides: list | None = None) -> tuple[str, list[tuple]]:
    """Returns (ass_text, events). Event: (start, end, text, font_size, est_width, x, y)."""
    S = tl.settings
    ws = [(w.word, w.start_frame, w.end_frame, w.highlight) for w in words]
    return core.compile_ass(ws, S.width, S.height, S.fps, rust_style(tl), overrides(tl, y_overrides))


def write_ass(words: list[CaptionWord], tl: Timeline, out: Path, y_overrides: list | None = None) -> tuple[Path, list[tuple]]:
    txt, events = compile_ass(words, tl, y_overrides)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(txt, encoding="utf-8")
    return out, events


def metrics(events: list, tl: Timeline) -> dict:
    """Report-only caption numbers: event count, events per 10 s, mean on-screen seconds. Timing comes from the words."""
    fps, n = tl.settings.fps, len(events)
    return {"events": n, "per_10s": round(n / (tl.total_frames / fps / 10), 2),
            "mean_s": round(sum(e[1] - e[0] for e in events) / n / fps, 2) if n else None}
