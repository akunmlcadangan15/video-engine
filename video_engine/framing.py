"""Directing in two layers: one shot intent for every aspect, a format adapter per orientation.

Layer A (story, aspect-agnostic): `segments[].intent.shot` names what the shot communicates (SHOTS). Beats, rhythm,
emphasis and the second-based story gates (hook, novelty, camera holds, silence, keyword, CTA) are the same for
9:16 and 16:9.

Layer B (format adapter, per orientation; units.py): the intent becomes concrete framing for the frame it lands on.

| Aspect          | 9:16 (portrait)             | 16:9 (landscape)                   |
|-----------------|-----------------------------|------------------------------------|
| subject         | centre-dominant             | rule of thirds                     |
| field of view   | tight                       | wide, shows the surroundings       |
| reading order   | top to bottom               | left to right                      |
| text slots      | title top, caption bottom   | title top left, caption lower third|
| camera          | push (dolly / z) dominant   | lateral pan, crane                 |
| watermark       | top right                   | bottom right                       |

`apply(tl)` (run when a timeline is validated) fills what a segment leaves open from its shot's framing: the native
`camera_move` of a still without one, and the HyperFrames `camera_preset` / `camera_intensity` of a map scene that
sets neither a preset nor a `camera_path`. `subject`, `read` and the text slots are guidance for the draft and the
director (`describe`), not positions the engine moves. Anything the timeline sets explicitly wins. A segment without `intent.shot` is never
touched, so existing timelines (and their cache keys) do not change.

A profile may override single values per orientation and shot (profile `[framing.portrait.<shot>]`,
`[framing.landscape.<shot>]`, keys of Framing).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .units import LANDSCAPE, PORTRAIT, orientation_of

SHOTS = ("establish", "track", "reveal", "compare", "journey", "hold", "reaction", "contrast")
SHOT_HELP = {
    "establish": "open on the place: where are we",
    "track": "follow a subject or a line across the frame",
    "reveal": "show something that was not visible: a fast start and a long settle",
    "compare": "two things side by side (landscape) or stacked (portrait)",
    "journey": "travel from one place to the next along the story",
    "hold": "let the viewer read: a near-still frame with a slow drift",
    "reaction": "push in on the detail the narration reacts to",
    "contrast": "pull back to set one thing against its surroundings",
}


@dataclass(frozen=True)
class Framing:
    native: str                  # native camera preset (Rust camera_presets) for a still without camera_move
    hf: str                      # HyperFrames camera_preset (hyperframes.HF_PRESETS) for a map scene without one
    intensity: float             # camera intensity of both (field of view: lower = wider)
    subject: tuple[float, float]  # where the subject sits, frame fractions (x, y)
    subjects: tuple[tuple[float, float], ...] = ()   # compare: the two subject positions
    read: str = "top_down"       # reading order: top_down | left_right
    title_slot: str = "top"      # draft overlay slot of a title / data card
    caption_slot: str = "bottom"


_P_CENTRE, _L_THIRD = (0.5, 0.45), (1 / 3, 0.5)
_P = dict(subject=_P_CENTRE, read="top_down", title_slot="top", caption_slot="bottom")
_L = dict(subject=_L_THIRD, read="left_right", title_slot="top_left", caption_slot="lower_third")

DEFAULTS: dict[str, dict[str, Framing]] = {
    PORTRAIT: {   # push (z) dominant, tight
        "establish": Framing("ken_burns_zoom_out", "zoom_out", 1.0, **_P),
        "track": Framing("pan_up", "pan_north", 1.0, **_P),
        "reveal": Framing("ken_burns_zoom_out", "zoom_out", 1.2, **_P),
        "compare": Framing("pan_down", "pan_south", 0.8, **{**_P, "subjects": ((0.5, 0.3), (0.5, 0.62))}),
        "journey": Framing("pan_up", "pan_north", 1.0, **_P),
        "hold": Framing("ken_burns_zoom_in", "static", 0.4, **_P),
        "reaction": Framing("ken_burns_zoom_in", "zoom_in", 1.2, **_P),
        "contrast": Framing("ken_burns_zoom_out", "zoom_out", 1.0, **_P),
    },
    LANDSCAPE: {   # lateral pan and crane, wide (lower intensity = less zoom)
        "establish": Framing("pan_right", "pan_east", 0.75, **_L),
        "track": Framing("pan_right", "pan_east", 0.8, **_L),
        "reveal": Framing("pan_up", "pan_north", 0.9, **_L),       # crane up onto the subject
        "compare": Framing("pan_right", "pan_east", 0.6, **{**_L, "subject": (0.5, 0.5),
                                                              "subjects": ((1 / 3, 0.5), (2 / 3, 0.5))}),
        "journey": Framing("pan_right", "pan_east", 0.8, **_L),
        "hold": Framing("ken_burns_zoom_in", "static", 0.3, **_L),
        "reaction": Framing("ken_burns_zoom_in", "zoom_in", 0.9, **_L),
        "contrast": Framing("pan_left", "pan_west", 0.75, **_L),
    },
}
OVERRIDE_KEYS = ("native", "hf", "intensity", "subject", "read", "title_slot", "caption_slot")


def framing(shot: str, orientation: str, overrides: dict | None = None) -> Framing:
    """Framing of `shot` in `orientation`, with a profile's [framing] values on top."""
    if shot not in SHOTS:
        raise ValueError(f"unknown shot intent {shot!r} (known: {', '.join(SHOTS)})")
    f = DEFAULTS[orientation][shot]
    over = ((overrides or {}).get(orientation) or {}).get(shot) or {}
    if over:
        f = replace(f, **{k: (tuple(v) if k == "subject" else v) for k, v in over.items()})
    return f


def check_overrides(over: dict) -> None:
    """ValueError for a malformed profile [framing] table."""
    for orient, shots in over.items():
        if orient not in (PORTRAIT, LANDSCAPE):
            raise ValueError(f"framing.{orient}: orientation must be portrait or landscape")
        if not isinstance(shots, dict):
            raise ValueError(f"framing.{orient} must be a table of shots")
        for shot, vals in shots.items():
            if shot not in SHOTS:
                raise ValueError(f"framing.{orient}.{shot}: unknown shot (known: {', '.join(SHOTS)})")
            bad = sorted(set(vals) - set(OVERRIDE_KEYS))
            if bad:
                raise ValueError(f"framing.{orient}.{shot}: unknown keys {bad} (known: {', '.join(OVERRIDE_KEYS)})")
            if "intensity" in vals and not (isinstance(vals["intensity"], (int, float)) and 0 <= vals["intensity"] <= 6):
                raise ValueError(f"framing.{orient}.{shot}.intensity must be a number in 0..6")
            if "subject" in vals and not (isinstance(vals["subject"], list) and len(vals["subject"]) == 2
                                          and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in vals["subject"])):
                raise ValueError(f"framing.{orient}.{shot}.subject must be [x, y] frame fractions")


def apply(tl) -> list[str]:
    """Fill the open camera fields of every segment with an `intent.shot` from its framing (in place; called by the
    timeline validator after the profile is known). Returns one line per filled field (for reports and tests)."""
    from . import profiles
    from .timeline import HYPERFRAMES, CameraMove
    shots = [s for s in tl.segments if s.intent is not None and s.intent.shot is not None]
    if not shots:
        return []
    orient = orientation_of(tl.settings.width, tl.settings.height)
    over = profiles.load(tl.profile, tl.profile_overrides).data.framing
    notes = []
    for s in shots:
        f = framing(s.intent.shot, orient, over)
        if s.engine_type == HYPERFRAMES:
            sc = s.gsap_scene_config
            if sc is None or sc.camera_path or getattr(sc, "globe", None):
                continue
            set_ = sc.model_fields_set
            if "camera_preset" not in set_:
                sc.camera_preset = f.hf
                notes.append(f"segment {s.seg_index}: camera_preset {f.hf} ({s.intent.shot}, {orient})")
            if "camera_intensity" not in set_:
                sc.camera_intensity = f.intensity
        elif s.is_still and s.camera_move is None:
            s.camera_move = CameraMove(preset=f.native, intensity=f.intensity)
            notes.append(f"segment {s.seg_index}: camera_move {f.native} x{f.intensity:g} ({s.intent.shot}, {orient})")
    return notes


def describe(orientation: str, overrides: dict | None = None) -> str:
    """Prompt text: the shot vocabulary and how each shot is framed in `orientation` (draft prompts, AI director)."""
    lines = [f"Shot intents (segments[].intent.shot; framing for this {orientation} frame is filled in by the engine):"]
    for shot in SHOTS:
        f = framing(shot, orientation, overrides)
        lines.append(f"- {shot}: {SHOT_HELP[shot]}; camera {f.hf} (native {f.native}), subject at "
                     f"x={f.subject[0]:.2f} y={f.subject[1]:.2f}, title slot {f.title_slot}")
    return "\n".join(lines)


