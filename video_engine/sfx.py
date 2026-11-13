"""SFX cue model: one cue per render event, for profiles with a [sound] table.

Render events (absolute frames): pin (teardrop: when the drop first touches its shadow), region (fade-in start),
route (mid-draw), whip (mid-move of a camera_path whip or of a camera3d_preset crash_zoom push), label (HyperFrames card, drawn native overlay), title (entrance), figure
(bounce figure entrance), counter_tick (each step of a counter's shown text). The profile maps events to SFX
kinds ([sound.events]) and gives each kind its gain, alignment and length cap ([sound.kinds.<kind>]).
"""
from __future__ import annotations

from typing import Callable

from . import camera_full3d, hyperframes, profiles
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
            assert sc is not None
            land = PIN_LAND_S * fps if th["pin_style"] == "teardrop" else 0
            for i, p in enumerate(sc.pins):
                add("pin", s, hyperframes.pin_frame(p, i, n, fps) + land)
            for c in sc.cards:
                add("label", s, hyperframes.card_frame(c, n, fps))
            for r in sc.regions or []:
                add("region", s, r.at_frame)
                for c in r.changes:
                    add("region", s, c.at_frame)
            for r in sc.routes or []:
                add("route", s, (r.at_frame + hyperframes.route_end_frame(r, fps, th["route_draw_s"])) / 2)
            keys = sc.camera_path or []
            for prev, k in zip(keys, keys[1:]):
                if k.move == "whip":   # same leg timing as hyperframes.camera_plan: the leg starts after the previous
                    f0 = min(n, prev.at_frame + prev.hold)   # key's hold and lasts move_s (else theme whip_s)
                    fw = max(f0, k.at_frame - max(1, round((k.move_s or th["whip_s"]) * fps)))
                    add("whip", s, (fw + k.at_frame) / 2)
            if sc.camera3d_preset is not None:   # the one sharp move of a 3D preset
                push = camera_full3d.preset_sfx_frame(sc.camera3d_preset.name, fps, sc.camera3d_preset.options)
                if push is not None:
                    add("whip", s, push)
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
    """SFX kinds the mix will resolve: mapped render events, plus manual cues that name a kind."""
    sp = profiles.of(tl).sound
    kinds: set[str] = set()
    kinds.update(c.kind for c in tl.audio_chain.sfx if c.kind)
    if sp is None:
        return sorted(kinds)
    kinds.update(sp.events[e["event"]] for e in events(tl) if e["event"] in sp.events)
    return sorted(kinds)


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
