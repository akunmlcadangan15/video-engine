"""Gate registry: the one place that describes every gate (id, class, layer, summary, switch).

The id lists that other modules need (`profiles.STYLE_GATE_IDS`, `TECHNICAL_GATE_IDS`, `NEVER_ADVISORY`) are derived
from `GATES` below, so a gate is added in one place. `engine gates` and the generated table in docs/GATES.md read the
same data; tests/test_gate_registry.py checks that every gate id a gate module emits is registered. The post-render
stage's all-clear record `QA` (qa.pixel_gates, docs/GATES.md) is a stage summary that only ever passes, not a gate.

Classes:
  technical  technical check (integrity, geometry, media, or asset rules)
  style      channel style / pacing check (warns unless the profile or timeline requires it)
Severity levels (kernel vs lint vs technical default; legacy tier exports are preserved for CLI/tooling compatibility):
  kernel     the 8-gate frozen integrity core (KERNEL_GATE_IDS, aliased as MANDATORY_GATE_IDS):
             no profile, project, or draft mode can lower its fail to warn.
  lint       the 50 non-blocking gates in LINT_GATE_IDS (style gates, layout L01-L10, taste/anti-slop,
             margin/camera/collision, and provenance/motion lint): warns and publishes with warnings
             by default unless explicitly listed in `[gates] require` or `gates.require`.
  error      technical gates outside KERNEL_GATE_IDS and LINT_GATE_IDS (`F01`, `AS04`, plus `AIQA`
             when `--ai-qa` is enabled): block a release build when they emit `fail` unless lowered
             via `[gates] advisory` or `--mode draft`.
Legacy exports PROFILE_GATE_IDS / FLEXIBLE_GATE_IDS, NEVER_ADVISORY (the 12 gates rejected in
`[gates] advisory`), and Gate.tier (mandatory/profile/flexible) are retained so `engine gates --format md`
and existing callers stay compatible; runtime severity (gates/severity.py) uses KERNEL_GATE_IDS vs LINT_GATE_IDS.
Layers (the order the pre-flight runs them, then the checks of the finished file):
  static, math, dom, place, layout, props, visual, profile, post, delivery, meta
This module imports nothing from the engine, so `profiles`, `timeline` and the CLI can all import it.
"""
from __future__ import annotations

from dataclasses import dataclass

LAYERS: dict[str, str] = {
    "static": "Layer 1: static checks of the timeline and project files",
    "math": "Layer 2: calculations on the timeline and the sources",
    "dom": "Layer 2: DOM probe of HyperFrames scenes (headless Chrome)",
    "place": "Layer 2: caption placement",
    "layout": "Layout planner checks (every frame of beats scenes)",
    "props": "Props (cut-out stickers and model sprites)",
    "visual": "Layer 3: visual snapshot QA",
    "profile": "Channel style gates (profile `[gates.*]` tables)",
    "post": "Post-render QA of the finished file",
    "delivery": "Gates on the final file",
    "meta": "Messages of the gate system itself",
}


@dataclass(frozen=True)
class Gate:
    id: str
    title: str
    summary: str
    layer: str
    kind: str                       # "technical" | "style"
    switch: str = ""                # profile / timeline key that turns it on or configures it ("" = always on)
    never_advisory: bool = False    # rejected in `[gates] advisory` (NEVER_ADVISORY; 8 kernel gates + R07, R14, R56px, A02)
    known: bool = True              # valid in `gates.waive` / `gates.advisory` / `gates.require`
    waivable: bool = False          # a project may lower it with `gates.waive_with_reason` (reason required)

    @property
    def tier(self) -> str:
        """Rigidity tier, derived from the class so a gate can never disagree with its own tier:
        "mandatory" (never advisory) | "profile" (channel style) | "flexible" (technical, lowerable)."""
        if self.never_advisory:
            return "mandatory"
        return "profile" if self.kind == "style" else "flexible"


def _t(id_, title, summary, layer, switch="", never=False, known=True, waivable=False) -> Gate:
    return Gate(id_, title, summary, layer, "technical", switch, never, known, waivable)


def _s(id_, title, summary, switch) -> Gate:
    return Gate(id_, title, summary, "profile", "style", switch)


GATES: tuple[Gate, ...] = (
    # --- layer 1: static -------------------------------------------------------------------------------------
    _t("T02", "Timeline integrity",
       "Segments are contiguous from frame 0, engine fields are valid, source files exist and presets are known.",
       "static", never=True),
    _t("R07", "Credits",
       "Every visual and BGM asset is credited: its source URL is http(s) and listed in credits.txt.",
       "static", never=True),
    _t("R14", "Licence",
       "The licence is on the allowlist (no NC or ND) and `watermark_free` is declared true.",
       "static", never=True),
    _t("R44", "Duplicate text",
       "No repeated adjacent caption word, no card text that repeats the spoken line, no duplicate overlay text.",
       "static"),
    _t("O01", "Drawn overlays",
       "Overlay text fits its box (at least 26 px at the reference canvas: 1920 high portrait, 1080 high landscape), the box is inside the safe area, and no two "
       "overlays overlap. Native segments only.",
       "static", waivable=True),
    _t("U01", "Frame aspect",
       "The frame is 9:16 (portrait reference 1080x1920) or 16:9 (landscape reference 1920x1080) within 1 %; other "
       "aspects scale profile lengths non-uniformly. Warning only.",
       "static"),
    _t("W01", "Pinned captions are current",
       "Pinned caption words match the voiceover bytes, `caption_fixes`, STT model and keyterms, language and fps.",
       "static", never=True),
    _t("L22", "Caption style and timing",
       "Caption style and slot are valid; words are ordered, do not overlap and lie inside the timeline.",
       "static", never=True),
    # --- layer 2: math ---------------------------------------------------------------------------------------
    _t("M01", "Source length",
       "The source video is long enough for `source_in_frame + n_frames` and is not HDR (PQ/HLG).",
       "math", never=True),
    _t("M02", "Map resolution",
       "Map texels per screen pixel at the deepest zoom (3D camera: the largest on-screen magnification of any frame): "
       "warn below 1.2, fail below 0.85.",
       "math"),
    _t("M03", "Tilt cover",
       "Every screen corner of every frame of a tilted or 3D camera shows map, not the map edge or the horizon.",
       "math", never=True),
    _t("M04", "Globe resolution",
       "Globe texels per degree at the view centre: warn below 1.0x, fail below 0.5x of the screen density.",
       "math"),
    _t("G01", "Grade LUTs",
       "Every grade LUT exists and parses as a 3D .cube file.",
       "math", never=True),
    _t("G02", "Geographic facts",
       "Region polygons are valid, beat pins lie inside their region, pin labels name their own region; with "
       "`require_land` a pin over water fails.",
       "math", "[gates.geo]"),
    _t("R56", "Camera motion",
       "Camera motion under 100 px at a 1080p-class diagonal (1080x1920 or 1920x1080) warns unless local intent expects motion; static intent is informational. "
       "Intensity changes require segment.creative_autofix or explicit camera_move.auto_intensity.",
       "math"),
    _t("R62", "Caption margins",
       "The caption font stays at least 0.6 of the configured size after fitting, and the caption is inside the "
       "safe area.",
       "math"),
    # --- layer 2: DOM probe ----------------------------------------------------------------------------------
    _t("R63", "Element margins and collisions",
       "HyperFrames elements stay inside the safe area (fail) and do not overlap (warn); captions keep their "
       "clearance or are moved automatically.",
       "dom"),
    _t("E01", "Entity colours",
       "Every region fill painted in the probe equals its entity colour; two entities sharing a colour warn.",
       "dom"),
    _t("R64", "Text legibility",
       "Adaptive map text reaches its APCA contrast target on the hard-case background; lines and pins reach "
       "their line target.",
       "dom", "[legibility]"),
    # --- layer 2: caption placement --------------------------------------------------------------------------
    _t("Z01", "Caption bottom limit",
       "No caption box ends below `caption_style.max_bottom_y` (after automatic placement and `y_overrides`).",
       "place", "caption_style.max_bottom_y"),
    _t("B01", "Watermark clearance",
       "The watermark box is inside the safe area and touches no element box or caption.",
       "place", "[watermark]"),
    # --- layout planner (every frame of beats scenes) ---------------------------------------------------------
    _t("T01", "Typeface count",
       "Distinct typefaces per probed frame stay within `print.max_typefaces` (captions and watermark excluded).",
       "layout", "[print]"),
    _t("L01", "Overlap",
       "No text box (halo included) comes closer than `text_clear` to another text, head or leader; rendered boxes "
       "match the plan within 3 px.",
       "layout", "[layout]", waivable=True),
    _t("L02", "Boundary",
       "Pin labels stay `edge_clear` away from region edges; area labels sit inside their region.",
       "layout", "[layout]"),
    _t("L03", "Budget",
       "Per frame at most `texts` texts, `labelled_pins` labelled pins, `routes` routes and `animating` tweens, one "
       "big number and one fully filled region.",
       "layout", "[layout]"),
    _t("L04", "Hero",
       "The largest text is at least `hero_ratio` times the next one (captions and hand notes excluded).",
       "layout", "[layout]"),
    _t("L05", "Grid",
       "Zone 1 ink starts on a column edge, tops sit on the unit, and a data card keeps year and time on one baseline.",
       "layout", "[layout]"),
    _t("L06", "Zones",
       "Texts stay inside the safe area, zone 1 texts inside zone 1, map labels, heads and routes inside zone 2.",
       "layout", "[layout]", waivable=True),
    _t("L07", "Route anchors",
       "A route starts on its map anchor, ends on its anchor or just short of its pin head, and keeps away from text.",
       "layout", "[layout]"),
    _t("L08", "Label stability",
       "At most `moves_per_shot` non-urgent label moves per shot, never two within `move_gap_s`.",
       "layout", "[layout]"),
    _t("L09", "Caption ground",
       "The highlighted region fill under the caption box covers at most `caption_ground` of its area.",
       "layout", "[layout]"),
    _t("L10", "Timing",
       "Holds are long enough to read, exits and entries keep their length, staggers and camera settling are respected.",
       "layout", "[layout]"),
    # --- props -----------------------------------------------------------------------------------------------
    _t("F01", "Prop files",
       "Every prop image exists, decodes as PNG or WebP and has transparent pixels; a model prop renders to a "
       "sprite that is not fully transparent.",
       "props"),
    _t("F02", "Prop resolution",
       "Source width over the largest on-screen width: fail below 1.0x, warn below 1.5x.",
       "props"),
    _t("F03", "Prop collisions",
       "A shown prop touches no pin head, label, title, figure, card, heading, note, area label or caption.",
       "props"),
    _t("F04", "Prop safe area",
       "A shown prop lies inside the safe area.",
       "props"),
    # --- layer 3: visual -------------------------------------------------------------------------------------
    _t("R38", "Snapshot quality",
       "Snapshots and sampled video frames must decode; no black frame (black fraction above 0.85 or mean luma below 12).",
       "visual", never=True),
    Gate("R38space", "Negative space",
         "Low-detail empty thirds warn by default; local intentional negative space with a reason is informational.",
         "visual", "style", "segments[].intent.negative_space"),
    _t("AIQA", "AI visual QA",
       "Optional (`--ai-qa`): AI models judge the snapshots; overlapping or cut-off text fails, an unavailable "
       "provider warns.",
       "visual", "--ai-qa", known=False),
    # --- channel style gates ---------------------------------------------------------------------------------
    _s("H01", "Hook",
       "Narration start, first caption and first pin/card/overlay are not later than the warn and fail times; "
       "optionally segment 0 must be a moving map.",
       "[gates.hook]"),
    _s("C01", "Cut rate",
       "Hard cuts and shot changes per 10 seconds, and the time of the first cut, stay inside the limits.",
       "[gates.cuts]"),
    _s("V01", "Visual type change",
       "A shot change between two segments of the same visual type warns; between two HyperFrames segments on the "
       "same map it fails.",
       "[gates.visual_type]"),
    _s("N01", "Novelty",
       "Something new appears on screen at least every `max_gap_s` seconds.",
       "[gates.novelty]"),
    _s("P01", "Camera motion",
       "The planned camera does not stay still longer than `hold_warn_s` (warn) or `hold_fail_s` (fail); the moving "
       "share stays above `moving_warn`.",
       "[gates.camera]"),
    _s("P02", "Camera drift",
       "The theme's `camera_drift_per_s` is at least `min_drift_per_s`, so waits between camera moves never freeze.",
       "[gates.camera] min_drift_per_s"),
    _s("P03", "Camera path",
       "Per segment: camera path length, peak screen speed and the largest one-frame speed change (info); warns on "
       "a speed jump above `step_warn` when that is set.",
       "[gates.camera] step_warn"),
    _s("K01", "Keyword return",
       "A word of the hook (first `hook_s` seconds) returns on screen in the last `end_s` seconds.",
       "[gates.keyword_return]"),
    _s("K02", "Ending keyword",
       "A hook keyword is still readable on the final frame.",
       "[gates.ending_keyword]"),
    _s("C02", "No call to action",
       "On-screen text and captions contain no call-to-action or end-card phrase.",
       "[gates.cta]"),
    _s("D01", "Duration range",
       "Total length lies between `min_s` and `max_s`.",
       "[gates.duration]"),
    _s("D02", "Exact duration",
       "Total length equals `target_s` within `tolerance_s`.",
       "[gates.duration_exact]"),
    _s("S01", "Silence",
       "The final file has no gap below `threshold_db` longer than `max_gap_s`.",
       "[gates.silence]"),
    _s("AS01", "Decorative side-strip panels",
       "A drawn lower third or text card with a left accent strip on a dark panel.",
       "[gates.anti_slop] panel"),
    _s("AS02", "Copy tics",
       "Em-dash saturation, buzzwords, repeated 'No ...' rebuttal endings or the word 'theater' in on-screen copy.",
       "[gates.anti_slop] copy"),
    _s("AS03", "Motion tics",
       "Ease-in entrances, scale-from-near-zero entrances or bouncing figures.",
       "[gates.anti_slop] motion"),
    _t("AS04", "Readable on-screen time",
       "A drawn overlay text stays on screen at least as long as it takes to read it.",
       "profile", "[gates.anti_slop] read_time"),
    _s("AS05", "Composition tells",
       "Gradient text, glass (`backdrop-filter`), side strips, repeating stripes, zero-offset glow, organic "
       "`clip-path` or `kicker`/`eyebrow` classes in project sources.",
       "[gates.anti_slop] source_tells"),
    _s("AS06", "Tell hues",
       "Literal colours in the forbidden hue ranges (violet, cyan) that are not declared brand colours.",
       "[gates.anti_slop] palette"),
    # --- finished file ---------------------------------------------------------------------------------------
    _t("R56px", "Measured motion",
       "Low sampled luma motion warns by default, is informational for static/read intent, and fails for explicit "
       "moving intent. Motion expectations never exempt black frames or decode failures.",
       "post", never=True),
    _t("A01", "Loudness",
       "The final file is at `target_lufs` within `tolerance_lu` and its true peak is at most `true_peak`.",
       "delivery", never=True),
    _t("A02", "Audio source",
       "Every music and SFX file of the final mix comes from the source named in `[audio] require_source`.",
       "delivery", "[audio] require_source", never=True),
)

BY_ID: dict[str, Gate] = {g.id: g for g in GATES}

# Derived id lists (the only definition; profiles.py and timeline.py import these).
STYLE_GATE_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.kind == "style" and g.known)
TECHNICAL_GATE_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.kind == "technical" and g.known)
KNOWN_GATE_IDS: tuple[str, ...] = STYLE_GATE_IDS + TECHNICAL_GATE_IDS
NEVER_ADVISORY: tuple[str, ...] = tuple(g.id for g in GATES if g.never_advisory)
# Tier views (the same data, grouped by rigidity). PROFILE_GATE_IDS are the channel style gates a profile
# may require; FLEXIBLE_GATE_IDS are the technical gates a profile may lower to warn. Both exports are
# retained for backward compat (gate_tools / older importers); severity now uses KERNEL vs LINT below.
# MANDATORY_GATE_IDS is defined after KERNEL_GATE_IDS below as a backward-compatible alias.
# NEVER_ADVISORY stays derived from Gate.never_advisory (historical flag, unchanged).
PROFILE_GATE_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.tier == "profile" and g.known)
FLEXIBLE_GATE_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.tier == "flexible" and g.known)
WAIVABLE_WITH_REASON: tuple[str, ...] = tuple(g.id for g in GATES if g.waivable)

# --- 3-level gate severity model (error / fix / lint) ---
# KERNEL_GATE_IDS: non-negotiable integrity checks that always block on failure.
KERNEL_GATE_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.id in ("T02", "M01", "W01", "L22", "R38", "A01", "G01", "M03"))
# LINT_GATE_IDS: non-blocking by default unless explicitly required by the channel profile or project timeline.
# Covers style gates, layout (L*), taste/anti-slop, and margin/camera/collision checks that auto-fix or warn.
LINT_GATE_IDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        STYLE_GATE_IDS
        + ("L01","L02","L03","L04","L05","L06","L07","L08","L09","L10",
           "R44","R64","T01","E01","O01","U01","M02","M04","F02","F03","F04","G02",
           "R38space","R62","Z01","B01","R63","R56","R56px","R07","R14","A02")
    )
)
# MANDATORY_GATE_IDS is exported as an alias of KERNEL_GATE_IDS for backward compatibility.
MANDATORY_GATE_IDS: tuple[str, ...] = KERNEL_GATE_IDS
ANTI_SLOP_IDS: tuple[str, ...] = tuple(g.id for g in GATES if g.id.startswith("AS"))

# profile `[gates.anti_slop]` switch of each anti-slop rule
ANTI_SLOP_SWITCH = {"AS01": "panel", "AS02": "copy", "AS03": "motion", "AS04": "read_time", "AS05": "source_tells",
                    "AS06": "palette"}


def get(gate_id: str) -> Gate | None:
    return BY_ID.get(gate_id)


def in_layer(layer: str) -> list[Gate]:
    return [g for g in GATES if g.layer == layer]
