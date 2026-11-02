"""Layer 1 static gates: T02, R07, R14, R44, L22, W01, O01. Input: timeline, project dir, caption words. No media decoding."""
from __future__ import annotations

import re
import shlex
from contextvars import ContextVar
from itertools import pairwise
from pathlib import Path

import hypoverum_core_rs as core

from .. import hyperframes, profiles, props
from ..assets import flag_for
from ..licence import OWN_LICENCES, licence_allowed, norm_licence
from ..timeline import HYPERFRAMES, NATIVE, CaptionWord, Timeline
from ..util import EngineError
from .core import Finding

URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
REPEAT_GAP_S = 1.0
EXTRA_CREDIT_URLS: ContextVar[tuple[str, ...]] = ContextVar("extra_credit_urls", default=())


def norm_text(s: str) -> list[str]:
    return [w for w in re.sub(r"[^\w\s]", " ", s.lower()).split() if w]


def visual_refs(tl: Timeline) -> list[tuple[int | None, str | None, str | None, str]]:
    """(seg, asset_id, path, role) for every asset that appears on screen or in the mix."""
    refs = []
    for s in tl.segments:
        if s.engine_type == NATIVE:
            refs.append((s.seg_index, s.asset_id, s.source, "source"))
        elif s.gsap_scene_config:
            for sc in s.gsap_scene_config.panels():   # a split segment: both panels' maps, props and flags
                ps = s if sc is s.gsap_scene_config else s.model_copy(update={"gsap_scene_config": sc})
                refs.append((s.seg_index, sc.map_asset_id, sc.map_asset, "map"))
                for c in sc.cards:
                    if c.asset_ref:
                        refs.append((s.seg_index, c.asset_ref, None, "card image"))
                for _, p, _, _ in props.flat(ps):
                    refs.append((s.seg_index, p.asset_id, p.asset, "prop"))
                for L in sc.layers or []:
                    refs.append((s.seg_index, L.asset_id, L.asset, "layer"))
                for c in sc.flag_characters or []:
                    refs.append((s.seg_index, f"flag_{c.flag.code}", flag_for(c.flag.code, c.flag.variant),
                                 "flag character"))
                for ins in sc.insets or []:
                    refs.append((s.seg_index, None, ins.asset, "inset image"))
    a = tl.audio_chain
    if a.bgm_path:
        refs.append((None, a.bgm_asset_id, a.bgm_path, "bgm"))
    return refs


def t02_integrity(tl: Timeline, project_dir: Path) -> list[Finding]:
    out = []
    segs = tl.segments
    for i, s in enumerate(segs):
        if s.seg_index != i:
            out.append(Finding("T02", "fail", f"seg_index {s.seg_index} at position {i}; seg_index must equal list position", s.seg_index))
        if i == 0 and s.start_frame != 0:
            out.append(Finding("T02", "fail", f"first segment starts at frame {s.start_frame}; must start at 0", s.seg_index))
        if i > 0 and s.start_frame != segs[i - 1].end_frame:
            kind = "gap" if s.start_frame > segs[i - 1].end_frame else "overlap"
            out.append(Finding("T02", "fail", f"{kind}: start_frame {s.start_frame} != previous end_frame {segs[i - 1].end_frame}", s.seg_index))
        if (s.engine_type == HYPERFRAMES and s.gsap_scene_config and s.gsap_scene_config.morph
                and not profiles.of(tl).transitions.map_morph):
            out.append(Finding("T02", "fail", "map morph is off in this profile ([transitions] map_morph = false): "
                               "keep the segment on its base map", s.seg_index))
        if s.engine_type == NATIVE:
            if not (project_dir / s.source).is_file():
                out.append(Finding("T02", "fail", f"source file not found: {s.source}", s.seg_index))
            if s.camera_move and s.camera_move.preset not in core.camera_presets():
                out.append(Finding("T02", "fail", f"unknown camera preset '{s.camera_move.preset}' (known: {', '.join(core.camera_presets())})", s.seg_index))
            if s.is_still and s.camera_move is None:
                out.append(Finding("T02", "fail", "still image segment needs camera_move", s.seg_index))
        elif s.engine_type == HYPERFRAMES:
            from ..hyperframes import HF_PRESETS
            sc = s.gsap_scene_config
            if not (project_dir / sc.map_asset).is_file():
                out.append(Finding("T02", "fail", f"map_asset not found: {sc.map_asset}", s.seg_index))
            if sc.globe and sc.globe.detail_tiles:
                from ..globe import tile_files
                try:
                    tile_files(project_dir / sc.globe.detail_tiles)
                except EngineError as e:
                    out.append(Finding("T02", "fail", str(e), s.seg_index))
            for L in sc.layers or []:
                if not (project_dir / L.asset).is_file():
                    out.append(Finding("T02", "fail", f"layer asset not found: {L.asset}", s.seg_index))
            if sc.camera_preset not in HF_PRESETS:
                out.append(Finding("T02", "fail", f"unknown HyperFrames camera_preset '{sc.camera_preset}' (known: {', '.join(HF_PRESETS)})", s.seg_index))
            for p in sc.pins:
                if p.x is None and not sc.map_bounds and not sc.globe:
                    out.append(Finding("T02", "fail", f"pin '{p.label}' uses lat/lng; map_bounds is required", s.seg_index))
                if p.at_frame is not None and p.at_frame >= s.n_frames:
                    out.append(Finding("T02", "fail", f"pin '{p.label}' at_frame {p.at_frame} >= segment length {s.n_frames}", s.seg_index))
            for r in sc.regions or []:
                for what, f in [("at_frame", r.at_frame), ("label_at_frame", r.label_at_frame),
                                *((f"change to '{c.entity}' at_frame", c.at_frame) for c in r.changes)]:
                    if f is not None and f >= s.n_frames:
                        out.append(Finding("T02", "fail", f"region '{r.entity}' {what} {f} >= segment length {s.n_frames}", s.seg_index))
            for i, b in enumerate(sc.borders or []):
                if b.at_frame >= s.n_frames:
                    out.append(Finding("T02", "fail", f"border {i} at_frame {b.at_frame} >= segment length {s.n_frames}", s.seg_index))
            if sc.morph:
                for what, f in (("at_frame", sc.morph.at_frame), ("back_at_frame", sc.morph.back_at_frame)):
                    if f is not None and f >= s.n_frames:
                        out.append(Finding("T02", "fail", f"morph {what} {f} >= segment length {s.n_frames}", s.seg_index))
                if not (project_dir / sc.morph.asset).is_file():
                    out.append(Finding("T02", "fail", f"morph asset not found: {sc.morph.asset}", s.seg_index))
            if sc.title and (sc.title_at_frame or 0) >= s.n_frames:
                out.append(Finding("T02", "fail", f"title at_frame {sc.title_at_frame} >= segment length {s.n_frames}", s.seg_index))
            if sc.title_entity and not sc.title:
                out.append(Finding("T02", "fail", f"title_entity '{sc.title_entity}' without a title", s.seg_index))
            for f in sc.figures or []:
                end = hyperframes.figure_end_frame(f, tl.settings.fps, profiles.of(tl).theme.counter_s)
                if end > s.n_frames - 1:
                    out.append(Finding("T02", "fail", f"figure {f.value:g} at_frame {f.at_frame}: entrance ends at frame "
                                       f"{end}, after the segment's last frame {s.n_frames - 1}", s.seg_index))
            for r in sc.routes or []:
                end = hyperframes.route_end_frame(r, tl.settings.fps, profiles.of(tl).theme.route_draw_s)
                if end > s.n_frames - 1:
                    out.append(Finding("T02", "fail", f"route at_frame {r.at_frame}: drawing ends at frame {end}, after the "
                                       f"segment's last frame {s.n_frames - 1}", s.seg_index))
            if sc.camera_path and sc.camera_path[-1].at_frame >= s.n_frames:
                out.append(Finding("T02", "fail", f"camera_path last key at_frame {sc.camera_path[-1].at_frame} >= "
                                   f"segment length {s.n_frames}", s.seg_index))
            for c in sc.cards:
                if c.at_frame is not None and c.at_frame >= s.n_frames:
                    out.append(Finding("T02", "fail", f"card '{c.title}' at_frame {c.at_frame} >= segment length {s.n_frames}", s.seg_index))
            out += _prop_timing(s, tl.settings.fps)
    a = tl.audio_chain
    for name in ("voiceover_path", "bgm_path"):
        p = getattr(a, name)
        if p and not (project_dir / p).is_file():
            out.append(Finding("T02", "fail", f"audio_chain.{name} not found: {p}"))
    return out or [Finding("T02", "pass", f"{len(segs)} segments contiguous 0..{tl.total_frames}")]


def _prop_timing(s, fps: int) -> list[Finding]:
    """T02 for props: shown frames inside the segment, enter and exit inside the shown frames."""
    out = []
    for where, p, f_in, f_out in props.flat(s):
        if f_in >= s.n_frames or f_out > s.n_frames:
            out.append(Finding("T02", "fail", f"{where} '{p.asset}': frames {f_in}-{f_out} leave the segment "
                               f"(0-{s.n_frames})", s.seg_index))
        elif f_out <= f_in:
            out.append(Finding("T02", "fail", f"{where} '{p.asset}': t_out falls at or before t_in", s.seg_index))
        else:
            need = sum(max(1, core.sec_to_frame(m.s, fps)) for m in (p.enter, p.exit) if m)
            if need > f_out - f_in:
                out.append(Finding("T02", "fail", f"{where} '{p.asset}': enter + exit take {need} frames, the prop shows "
                                   f"{f_out - f_in}", s.seg_index))
    return out


def r07_credits(tl: Timeline, project_dir: Path) -> list[Finding]:
    out = []
    ctxt_path = project_dir / "credits.txt"
    ctxt = ctxt_path.read_text(errors="replace") if ctxt_path.is_file() else None
    if ctxt is None:
        out.append(Finding("R07", "fail", "credits.txt missing in project dir"))
    extra = set(EXTRA_CREDIT_URLS.get())
    for seg, aid, path, role in visual_refs(tl):
        c = tl.credit_for(ref=aid, path=path)
        label = f"{role} '{aid or path}'"
        if c is None:
            out.append(Finding("R07", "fail", f"{label} has no credits_manifest entry", seg))
            continue
        if norm_licence(c.license) in OWN_LICENCES:
            continue
        if not URL_RE.match(c.source_url.strip()):
            out.append(Finding("R07", "fail", f"{label} source_url '{c.source_url}' is not an http(s) URL", seg))
        elif ctxt is not None and c.source_url.strip() not in ctxt and c.source_url.strip() not in extra:
            out.append(Finding("R07", "fail", f"{label} source_url '{c.source_url}' missing from credits.txt", seg))
    return out or [Finding("R07", "pass", "every visual/audio asset credited with URL in credits.txt")]


def r14_licence(tl: Timeline) -> list[Finding]:
    out = []
    used = {c.asset_id for _, aid, path, _ in visual_refs(tl) if (c := tl.credit_for(ref=aid, path=path))}
    for c in tl.credits_manifest:
        if c.asset_id not in used:
            continue
        if not licence_allowed(c.license):
            out.append(Finding("R14", "fail", f"credit '{c.asset_id}' license '{c.license}' not in allowlist (PD, CC0, "
                                              "CC BY, CC BY-SA, Pexels, Pixabay, Unsplash, Mixkit, Own; never NC/ND)"))
        if c.watermark_free is not True:
            state = "not declared" if c.watermark_free is None else "false"
            out.append(Finding("R14", "fail", f"credit '{c.asset_id}' watermark_free is {state}; must be true"))
    return out or [Finding("R14", "pass", "all used licences allowed and watermark-free")]


def r44_duplicates(tl: Timeline, words: list[CaptionWord]) -> list[Finding]:
    out = []
    fps = tl.settings.fps
    nw = [(norm_text(w.word), w) for w in words]
    for (a, wa), (b, wb) in pairwise(nw):
        if a and a == b and wb.start_frame - wa.end_frame <= REPEAT_GAP_S * fps \
                and not wa.word.rstrip().endswith((".", "!", "?", "…")):
            out.append(Finding("R44", "fail", f"caption word repeated: '{wa.word.strip()}' at frames {wa.start_frame} and {wb.start_frame}"))
    for s in tl.segments:
        sc = s.gsap_scene_config
        if not sc:
            continue
        texts = [("card title", c.title) for c in sc.cards] + [("card subtitle", c.subtitle) for c in sc.cards if c.subtitle] \
            + [("pin", p.label) for p in sc.pins] + ([("title", sc.title)] if sc.title else [])
        seen = {}
        for kind, t in texts:
            k = " ".join(norm_text(t))
            if k in seen:
                out.append(Finding("R44", "fail", f"{kind} '{t}' duplicates {seen[k]} in the same segment", s.seg_index))
            seen[k] = f"{kind} '{t}'"
        spoken = [x for x, w in nw if w.end_frame > s.start_frame and w.start_frame < s.end_frame]
        flat = [t for x in spoken for t in x]
        for kind, t in texts:
            toks = norm_text(t)
            if len(toks) < 2:
                continue
            for i in range(len(flat) - len(toks) + 1):
                if flat[i:i + len(toks)] == toks:
                    out.append(Finding("R44", "fail", f"{kind} '{t}' repeats the caption spoken during this segment; keep one", s.seg_index))
                    break
    return out or [Finding("R44", "pass", "no duplicated text")]


def l22_captions(tl: Timeline, words: list[CaptionWord]) -> list[Finding]:
    out = []
    slots = dict(core.caption_slots())
    slot = profiles.caption_style(tl).get("slot", "lower")
    if slot not in slots:
        out.append(Finding("L22", "fail", f"caption_style.slot '{slot}' invalid (valid: {', '.join(slots)})"))
    total = tl.total_frames
    prev_end = 0
    for i, w in enumerate(words):
        if not w.word.strip():
            out.append(Finding("L22", "fail", f"caption word {i} is empty"))
        if w.end_frame <= w.start_frame:
            out.append(Finding("L22", "fail", f"caption word {i} '{w.word.strip()}' end_frame {w.end_frame} <= start_frame {w.start_frame}"))
        if w.end_frame > total:
            out.append(Finding("L22", "fail", f"caption word {i} '{w.word.strip()}' ends at {w.end_frame} after timeline end {total}"))
        if w.start_frame < prev_end:
            out.append(Finding("L22", "fail", f"caption word {i} '{w.word.strip()}' starts at {w.start_frame} before previous word ends ({prev_end})"))
        prev_end = max(prev_end, w.end_frame)
    return out or [Finding("L22", "pass", f"slot '{slot}', {len(words)} words monotone inside 0..{total}")]


def repin_hint(project_dir: Path, *, cached: bool) -> str:
    """How to refresh stale pinned captions. `cached`: only the caption fixes or the fps changed, so the speech-to-text
    cache is reused and nothing is transcribed again."""
    cmd = f"engine words {shlex.quote(str(project_dir))}"
    reuse = " (the transcription cache is reused; nothing is transcribed again)" if cached else ""
    return f"fix: run `{cmd}`{reuse}, or add --repin to `engine build`; never edit captions_meta by hand"


def w01_pinned_words(tl: Timeline, project_dir: Path) -> list[Finding]:
    """Pinned captions must match the narration they were transcribed from (no silent drift)."""
    m, ac = tl.captions_meta, tl.audio_chain
    if not tl.captions:
        if ac.auto_captions and ac.voiceover_path:
            return [Finding("W01", "skip", "captions not pinned yet; the build (or `engine words`) writes them once")]
        return [Finding("W01", "pass", "no captions requested")]
    if m is None:
        return [Finding("W01", "pass", f"{len(tl.captions)} hand-written captions (no captions_meta)")]
    if not ac.voiceover_path or not (project_dir / ac.voiceover_path).is_file():
        return [Finding("W01", "fail", "captions_meta is present but audio_chain.voiceover_path is missing or the file "
                                       "does not exist; restore the voiceover, then run "
                                       f"`engine words {shlex.quote(str(project_dir))}`; never edit captions_meta by hand")]
    from ..audio import expected_meta
    exp = expected_meta(tl, project_dir)
    names = {"voiceover_blake3": "voiceover audio", "stt_model": "stt.model", "language": "language",
             "keyterms_blake3": "stt.keyterms", "fixes_blake3": "caption_fixes", "fps": "settings.fps"}
    out = [Finding("W01", "fail", f"pinned captions are stale: {names[k]} changed since the words were pinned; "
                                  + repin_hint(project_dir, cached=k in ("fixes_blake3", "fps")))
           for k, v in exp.items() if getattr(m, k) != v]
    if m.clipped_words and tl.total_frames > m.total_frames:
        out.append(Finding("W01", "fail", f"timeline grew from {m.total_frames} to {tl.total_frames} frames and "
                                          f"{m.clipped_words} transcribed words were cut at the old end; "
                                          + repin_hint(project_dir, cached=True)))
    return out or [Finding("W01", "pass", f"{len(tl.captions)} pinned words match voiceover, fixes and fps")]


def pins_stale(tl: Timeline, project_dir: Path) -> bool:
    """True when gate W01 would fail on the pinned captions of `tl` (a rebuild with --repin pins them again)."""
    return any(f.level == "fail" for f in w01_pinned_words(tl, project_dir))


def o01_overlays(tl: Timeline) -> list[Finding]:
    """O01: every drawn overlay fits its text in its box, sits in the safe area, has a valid frame range and does not
    overlap another drawn overlay of the same segment while both are visible."""
    from .. import overlays
    out, n = [], 0
    for s in tl.segments:
        n += len(overlays.drawn(s))
        out += [Finding("O01", "fail", e, s.seg_index) for e in overlays.check(s, tl)]
    out = out or [Finding("O01", "pass" if n else "skip", f"{n} drawn overlays fit their boxes" if n else "no drawn overlays")]
    pr = profiles.of(tl).print
    if pr is not None and n:      # T01 on native segments (map scenes: layout_gates.typefaces on the DOM)
        t01 = [Finding("T01", "fail", e, s.seg_index) for s in tl.segments for e in overlays.typefaces(s, pr.max_typefaces)]
        out += t01 or [Finding("T01", "pass", f"native overlays: <= {pr.max_typefaces} typefaces at a time")]
    return out


def run_layer1(tl: Timeline, project_dir: Path, words: list[CaptionWord] | None) -> list[Finding]:
    fs = t02_integrity(tl, project_dir) + r07_credits(tl, project_dir) + r14_licence(tl) + w01_pinned_words(tl, project_dir)
    fs += o01_overlays(tl)
    if words is None:
        fs.append(Finding("R44", "skip", "no captions yet (run `engine audio`); overlay-only checks ran"))
        fs += [f for f in r44_duplicates(tl, []) if f.level != "pass"]
        fs += [f for f in l22_captions(tl, []) if f.level != "pass"]
        fs.append(Finding("L22", "skip", "no captions yet (run `engine audio`); slot check ran"))
    else:
        fs += r44_duplicates(tl, words) + l22_captions(tl, words)
    return fs
