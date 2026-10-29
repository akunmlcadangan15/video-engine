"""SFX cue model: one cue per render event, for profiles with a [sound] table.

Render events (absolute frames): pin (teardrop: when the drop first touches its shadow), region (fade-in start),
route (mid-draw), whip (mid-move), label (HyperFrames card, drawn native overlay), title (entrance), figure
(bounce figure entrance), counter_tick (each step of a counter's shown text). The profile maps events to SFX
kinds ([sound.events]) and gives each kind its gain, alignment and length cap ([sound.kinds.<kind>]).
"""
from __future__ import annotations

from typing import Callable

from . import hyperframes, profiles
from .timeline import HYPERFRAMES, NATIVE, SfxCue, Timeline

PIN_LAND_S = 0.22      # teardrop drop (0.6 s bounce.out) first touches its shadow


def events(tl: Timeline) -> list[dict]:
    """Render events {event, frame} in frame order."""
    th, fps, out = profiles.of(tl).render_values("hf"), tl.settings.fps, []

    def add(ev: str, s, rel: float) -> None:
        out.append({"event": ev, "frame": min(s.end_frame - 1, s.start_frame + max(0, round(rel)))})

    for s in tl.segments:
        n = s.n_frames
        if s.engine_type == HYPERFRAMES:
            sc = s.gsap_scene_config
            land = PIN_LAND_S * fps if th["pin_style"] == "teardrop" else 0
            for i, p in enumerate(sc.pins):
                add("pin", s, hyperframes.pin_frame(p, i, n, fps) + land)
            for c in sc.cards:
                add("label", s, hyperframes.card_frame(c, n, fps))
            for r in sc.regions or []:
                add("region", s, r.at_frame)
            for r in sc.routes or []:
                add("route", s, (r.at_frame + hyperframes.route_end_frame(r, fps, th["route_draw_s"])) / 2)
            f0 = 0
            for k in (sc.camera_path or [])[1:]:
                if k.move == "whip":   # same leg timing as hyperframes.camera_plan
                    fw = max(f0, k.at_frame - max(1, round(th["whip_s"] * fps)))
                    add("whip", s, (fw + k.at_frame) / 2)
                f0 = k.at_frame
            if sc.title:
                add("title", s, sc.title_at_frame or 0)
            for f in sc.figures or []:
                if f.motion == "bounce":
                    add("figure", s, f.at_frame)
        elif s.engine_type == NATIVE:
            for o in s.overlays:
                if o.kind != "reserve":
                    add("label", s, o.start_frame or 0)
    out += [{"event": "counter_tick", "frame": e["frame"]} for e in hyperframes.sfx_events(tl)]
    return sorted(out, key=lambda e: (e["frame"], e["event"]))


def kinds_used(tl: Timeline) -> list[str]:
    sp = profiles.of(tl).sound
    if sp is None:
        return []
    return sorted({sp.events[e["event"]] for e in events(tl) if e["event"] in sp.events})


def cues(tl: Timeline, asset: Callable[[str], tuple[str, float]]) -> list[SfxCue]:
    """One cue per mapped event. asset(kind) -> (source asset id, peak time s of the file); align "peak" puts
    that peak on the event (whoosh peak on the middle of the move), "onset" starts the file on it."""
    sp, fps = profiles.of(tl).sound, tl.settings.fps
    if sp is None:
        return []
    out = []
    for e in events(tl):
        kind = sp.events.get(e["event"])
        if not kind:
            continue
        k = sp.kinds[kind]
        aid, peak = asset(kind)
        t = e["frame"] / fps - (peak if k.align == "peak" else 0.0)
        out.append(SfxCue(time_s=round(max(0.0, t), 4), kind=kind, gain_db=k.gain_db, asset_id=aid,
                          event=e["event"], frame=e["frame"], trim_s=round(max(0.0, -t), 4), max_s=k.max_s))
    return out
