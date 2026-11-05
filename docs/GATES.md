# Gates

A gate is an automatic check that passes, warns or fails the build.
Most gates run before any segment is rendered, because a failed render costs several times more than the checks.

**What this document answers**

- What each gate checks, which layer it belongs to and whether it can block a build.
- What the levels mean, and how a profile or a project lowers a gate (advisory, waive, draft build).
- Which profile table switches a style gate on, and which keys it takes.
- Where the list of gates comes from, and how to look at it (`engine gates`).

## Levels, layers and classes

- Pre-flight target: < 5 s in total (stage times: `--timing-tsv`).
- Levels: `pass`, `fix` (applied automatically), `info`, `warn`, `skip` (the gate had no input) and `fail`.
  Any `fail` stops `build`.
- Layers, in the order the pre-flight runs them: 1 static checks of the timeline and project files, 2 calculations and the DOM probe of HyperFrames scenes, caption placement, layout and props, then 3 visual snapshots.
  Checks of the finished file (post-render QA, loudness, silence, audio source) come after the render.
- Classes:
  - **Technical** gates (assets, output integrity, decode, loudness, licence and credits, caption bounds and legibility, factual pins) keep their level: a fail blocks the build.
  - **Style** gates (H01, C01, C02, V01, N01, P01, P02, K01, K02, D01, D02, S01, AS01, AS02, AS03, AS05, AS06) block only when required (profile `[gates] require` plus timeline `gates.require`).
    A fail of a style gate that is not required is reported as a warning.
  - A technical gate in the profile's `[gates] advisory` list is treated like a non-required style gate: its fail is reported as a warning.
    `T02`, `R07`, `R14`, `A01`, `A02`, `W01`, `R38`, `R56px`, `M01`, `M03`, `G01` and `L22` can never be advisory.
- Geoverum has `[gates] require = []`: no style gate is required by the channel. Its advisory technical gates are listed in `profiles/geoverum.toml`; use `engine gates --profile geoverum` for the effective policy. A project may still add required gates.
- `build_report.json` -> `gates`: `required`, `fails`, `warns`, `waived` (each row: `gate`, `class`, `level`, `message`, `seg`, `frames`, and the original level in `waived`), and `waivers` when the timeline lowers gates with a reason.

## Lowering a gate

| Way | Who | What it can lower | Where it is recorded |
|---|---|---|---|
| `[gates] advisory` | the channel profile | technical gates except the never-advisory list | `waived` rows: "style gate not required by the channel" |
| `gates.waive` | the project (timeline) | non-required style gates and advisory gates, to info | `waived` rows |
| `hold: "read"` | the project (segment or beat) | P01 and N01 findings that start inside it, to info | `waived` rows |
| `gates.waive_with_reason` | the project (timeline) | `L01`, `L06`, `O01`, to info, with a written reason | `waived` rows and `gates.waivers` |
| `--mode draft` | the person running the build | technical gates that are not never-advisory, from fail to warn | `waived` rows: "lowered in a draft build" |

A waive of a required gate, or a plain `gates.waive` of a technical gate, changes nothing and adds one warning (gate `GW01`) that says it was ignored.
Every lowered finding keeps its original level in `waived`.

### Draft builds

`engine build P --mode draft` (and `engine preflight P --mode draft`) is for trying a timeline before it is clean.

- A fail of any technical gate that a profile may make advisory becomes a warning.
  Gates in the never-advisory list (`T02`, `R07`, `R14`, `A01`, `A02`, `W01`, `R38`, `R56px`, `M01`, `M03`, `G01`, `L22`) and any style gates explicitly required by the profile or project still block.
- The build writes `out/draft/final.draft.mp4`, `out/draft/build_report.json` (marked `"mode": "draft"`) and `out/draft/build_manifest.json`.
  `out/final.mp4` and its report are never touched.
- `engine deliver` refuses a draft.
  Build again without `--mode draft` to get a deliverable file.
- Segment, composite and audio cache keys do not depend on policy mode. The same render settings can share cached renders; changing render quality changes the relevant visual cache keys.
- `--quality proxy` is a separate, opt-in speed setting and requires `--mode draft`; see [Proxy render quality](BUILD.md#proxy-render-quality). Draft mode by itself retains delivery render settings.

### Waiving a gate with a reason

```json
"gates": {"waive_with_reason": [{"gate": "L01", "reason": "the title card overlaps the pin on purpose for this reveal"}]}
```

Only `L01`, `L06` and `O01` can be waived this way.
The reason must be at least 10 characters; each gate may be listed once.
The findings of that gate become info, the reason is stored in `build_report.json` under `gates.waivers` and in the waived rows, and `engine gates --stats` counts the waived findings.

## Where the list of gates comes from

`video_engine/gate_registry.py` describes every gate once: id, class, layer, name, rule, switch, and whether it is never-advisory or waivable.
`profiles.STYLE_GATE_IDS`, `TECHNICAL_GATE_IDS`, `KNOWN_GATE_IDS` and `NEVER_ADVISORY` are derived from it.
`engine gates` prints it; `engine gates --profile NAME` shows what each gate does under a profile; `engine gates --stats PATH ...` counts findings in build reports.
The index below is generated by `engine gates --format md`; do not edit it by hand.

## Gate index

<!-- BEGIN GENERATED: engine gates --format md -->

### Layer 1: static checks of the timeline and project files

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| T02 | technical (never advisory) | Timeline integrity | Segments are contiguous from frame 0, engine fields are valid, source files exist and presets are known. | always |
| R07 | technical (never advisory) | Credits | Every visual and BGM asset is credited: its source URL is http(s) and listed in credits.txt. | always |
| R14 | technical (never advisory) | Licence | The licence is on the allowlist (no NC or ND) and `watermark_free` is declared true. | always |
| R44 | technical | Duplicate text | No repeated adjacent caption word, no card text that repeats the spoken line, no duplicate overlay text. | always |
| O01 | technical | Drawn overlays | Overlay text fits its box (at least 26 px at the reference canvas: 1920 high portrait, 1080 high landscape), the box is inside the safe area, and no two overlays overlap. Native segments only. | always |
| U01 | technical | Frame aspect | The frame is 9:16 (portrait reference 1080x1920) or 16:9 (landscape reference 1920x1080) within 1 %; other aspects scale profile lengths non-uniformly. Warning only. | always |
| W01 | technical (never advisory) | Pinned captions are current | Pinned caption words match the voiceover bytes, `caption_fixes`, STT model and keyterms, language and fps. | always |
| L22 | technical (never advisory) | Caption style and timing | Caption style and slot are valid; words are ordered, do not overlap and lie inside the timeline. | always |

### Layer 2: calculations on the timeline and the sources

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| M01 | technical (never advisory) | Source length | The source video is long enough for `source_in_frame + n_frames` and is not HDR (PQ/HLG). | always |
| M02 | technical | Map resolution | Map texels per screen pixel at the deepest zoom (3D camera: the largest on-screen magnification of any frame): warn below 1.2, fail below 0.85. | always |
| M03 | technical (never advisory) | Tilt cover | Every screen corner of every frame of a tilted or 3D camera shows map, not the map edge or the horizon. | always |
| M04 | technical | Globe resolution | Globe texels per degree at the view centre: warn below 1.0x, fail below 0.5x of the screen density. | always |
| G01 | technical (never advisory) | Grade LUTs | Every grade LUT exists and parses as a 3D .cube file. | always |
| G02 | technical | Geographic facts | Region polygons are valid, beat pins lie inside their region, pin labels name their own region; with `require_land` a pin over water fails. | `[gates.geo]` |
| R56 | technical | Camera motion | Camera motion under 100 px at a 1080p-class diagonal (1080x1920 or 1920x1080) warns unless local intent expects motion; static intent is informational. Intensity changes require segment.creative_autofix or explicit camera_move.auto_intensity. | always |
| R62 | technical | Caption margins | The caption font stays at least 0.6 of the configured size after fitting, and the caption is inside the safe area. | always |

### Layer 2: DOM probe of HyperFrames scenes (headless Chrome)

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| R63 | technical | Element margins and collisions | HyperFrames elements stay inside the safe area (fail) and do not overlap (warn); captions keep their clearance or are moved automatically. | always |
| E01 | technical | Entity colours | Every region fill painted in the probe equals its entity colour; two entities sharing a colour warn. | always |
| R64 | technical | Text legibility | Adaptive map text reaches its APCA contrast target on the hard-case background; lines and pins reach their line target. | `[legibility]` |

### Layer 2: caption placement

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| Z01 | technical | Caption bottom limit | No caption box ends below `caption_style.max_bottom_y` (after automatic placement and `y_overrides`). | `caption_style.max_bottom_y` |
| B01 | technical | Watermark clearance | The watermark box is inside the safe area and touches no element box or caption. | `[watermark]` |

### Layout planner checks (every frame of beats scenes)

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| T01 | technical | Typeface count | Distinct typefaces per probed frame stay within `print.max_typefaces` (captions and watermark excluded). | `[print]` |
| L01 | technical | Overlap | No text box (halo included) comes closer than `text_clear` to another text, head or leader; rendered boxes match the plan within 3 px. | `[layout]` |
| L02 | technical | Boundary | Pin labels stay `edge_clear` away from region edges; area labels sit inside their region. | `[layout]` |
| L03 | technical | Budget | Per frame at most `texts` texts, `labelled_pins` labelled pins, `routes` routes and `animating` tweens, one big number and one fully filled region. | `[layout]` |
| L04 | technical | Hero | The largest text is at least `hero_ratio` times the next one (captions and hand notes excluded). | `[layout]` |
| L05 | technical | Grid | Zone 1 ink starts on a column edge, tops sit on the unit, and a data card keeps year and time on one baseline. | `[layout]` |
| L06 | technical | Zones | Texts stay inside the safe area, zone 1 texts inside zone 1, map labels, heads and routes inside zone 2. | `[layout]` |
| L07 | technical | Route anchors | A route starts on its map anchor, ends on its anchor or just short of its pin head, and keeps away from text. | `[layout]` |
| L08 | technical | Label stability | At most `moves_per_shot` non-urgent label moves per shot, never two within `move_gap_s`. | `[layout]` |
| L09 | technical | Caption ground | The highlighted region fill under the caption box covers at most `caption_ground` of its area. | `[layout]` |
| L10 | technical | Timing | Holds are long enough to read, exits and entries keep their length, staggers and camera settling are respected. | `[layout]` |

### Props (cut-out stickers and model sprites)

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| F01 | technical | Prop files | Every prop image exists, decodes as PNG or WebP and has transparent pixels; a model prop renders to a sprite that is not fully transparent. | always |
| F02 | technical | Prop resolution | Source width over the largest on-screen width: fail below 1.0x, warn below 1.5x. | always |
| F03 | technical | Prop collisions | A shown prop touches no pin head, label, title, figure, card, heading, note, area label or caption. | always |
| F04 | technical | Prop safe area | A shown prop lies inside the safe area. | always |

### Layer 3: visual snapshot QA

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| R38 | technical (never advisory) | Snapshot quality | Snapshots and sampled video frames must decode; no black frame (black fraction above 0.85 or mean luma below 12). | always |
| R38space | style | Negative space | Low-detail empty thirds warn by default; local intentional negative space with a reason is informational. | `segments[].intent.negative_space` |
| AIQA | technical | AI visual QA | Optional (`--ai-qa`): AI models judge the snapshots; overlapping or cut-off text fails, an unavailable provider warns. | `--ai-qa` |

### Channel style gates (profile `[gates.*]` tables)

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| H01 | style | Hook | Narration start, first caption and first pin/card/overlay are not later than the warn and fail times; optionally segment 0 must be a moving map. | `[gates.hook]` |
| C01 | style | Cut rate | Hard cuts and shot changes per 10 seconds, and the time of the first cut, stay inside the limits. | `[gates.cuts]` |
| V01 | style | Visual type change | A shot change between two segments of the same visual type warns; between two HyperFrames segments on the same map it fails. | `[gates.visual_type]` |
| N01 | style | Novelty | Something new appears on screen at least every `max_gap_s` seconds. | `[gates.novelty]` |
| P01 | style | Camera motion | The planned camera does not stay still longer than `hold_warn_s` (warn) or `hold_fail_s` (fail); the moving share stays above `moving_warn`. | `[gates.camera]` |
| P02 | style | Camera drift | The theme's `camera_drift_per_s` is at least `min_drift_per_s`, so waits between camera moves never freeze. | `[gates.camera] min_drift_per_s` |
| P03 | style | Camera path | Per segment: camera path length, peak screen speed and the largest one-frame speed change (info); warns on a speed jump above `step_warn` when that is set. | `[gates.camera] step_warn` |
| K01 | style | Keyword return | A word of the hook (first `hook_s` seconds) returns on screen in the last `end_s` seconds. | `[gates.keyword_return]` |
| K02 | style | Ending keyword | A hook keyword is still readable on the final frame. | `[gates.ending_keyword]` |
| C02 | style | No call to action | On-screen text and captions contain no call-to-action or end-card phrase. | `[gates.cta]` |
| D01 | style | Duration range | Total length lies between `min_s` and `max_s`. | `[gates.duration]` |
| D02 | style | Exact duration | Total length equals `target_s` within `tolerance_s`. | `[gates.duration_exact]` |
| S01 | style | Silence | The final file has no gap below `threshold_db` longer than `max_gap_s`. | `[gates.silence]` |
| AS01 | style | Decorative side-strip panels | A drawn lower third or text card with a left accent strip on a dark panel. | `[gates.anti_slop] panel` |
| AS02 | style | Copy tics | Em-dash saturation, buzzwords, repeated 'No ...' rebuttal endings or the word 'theater' in on-screen copy. | `[gates.anti_slop] copy` |
| AS03 | style | Motion tics | Ease-in entrances, scale-from-near-zero entrances or bouncing figures. | `[gates.anti_slop] motion` |
| AS04 | technical | Readable on-screen time | A drawn overlay text stays on screen at least as long as it takes to read it. | `[gates.anti_slop] read_time` |
| AS05 | style | Composition tells | Gradient text, glass (`backdrop-filter`), side strips, repeating stripes, zero-offset glow, organic `clip-path` or `kicker`/`eyebrow` classes in project sources. | `[gates.anti_slop] source_tells` |
| AS06 | style | Tell hues | Literal colours in the forbidden hue ranges (violet, cyan) that are not declared brand colours. | `[gates.anti_slop] palette` |

### Post-render QA of the finished file

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| R56px | technical (never advisory) | Measured motion | Low sampled luma motion warns by default, is informational for static/read intent, and fails for explicit moving intent. Motion expectations never exempt black frames or decode failures. | always |

### Gates on the final file

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| A01 | technical (never advisory) | Loudness | The final file is at `target_lufs` within `tolerance_lu` and its true peak is at most `true_peak`. | always |
| A02 | technical (never advisory) | Audio source | Every music and SFX file of the final mix comes from the source named in `[audio] require_source`. | `[audio] require_source` |

### Messages of the gate system itself

| ID | Class | Name | Rule | Switch |
|---|---|---|---|---|
| GW01 | technical | Ignored waive | A project `gates.waive` named a gate that is required or technical; the request was ignored. | always |

<!-- END GENERATED: engine gates --format md -->

## Gate rules

The full rule of each gate, with its thresholds. Where this list and the index differ, this list is the rule.

- **T02** (layer 1): Segments contiguous from 0, valid engine fields, sources exist, known presets
- **R07** (layer 1): Every visual / BGM asset credited; source URL is http(s) and listed in `credits.txt`
- **R14** (layer 1): Licence in allowlist (no NC/ND); `watermark_free` declared true
- **R44** (layer 1): No repeated adjacent caption word; card text does not repeat the spoken line in its segment; no duplicate overlay text
- **O01** (layer 1): Drawn overlays: text fits the box at >= 26 px (1920 height), box inside the safe area (60 px sides, 100 px top/bottom), frame range inside the segment, no two drawn overlays overlap on screen while both are visible, native segments only.
- **W01** (layer 1): Pinned captions match the current voiceover bytes, `caption_fixes`, `stt.model`, `stt.keyterms`, `language` and `fps`;
    a timeline that grew past words cut at the old end fails.
  Unpinned auto captions = `skip` (the build pins them).
- **L22** (layer 1): Valid caption style and slot; caption words ordered, non-overlapping, inside the timeline
- **M01** (layer 2 math): Source video long enough for `source_in_frame + n_frames`
- **M02** (layer 2 math): HyperFrames map texels per screen px at the segment's deepest zoom (tilt magnification included): real source detail (pixel width; half of it for a raster pre-upscaled 2x, measured once per source and cached) / map width on screen: warn < 1.2, fail < 0.85.
  The derivative and deep tiles are sized to the on-screen width on purpose, so they lower the ratio only when cut below it (a deep raster cap)
- **M03** (layer 2 math): Tilted HyperFrames cameras: every screen corner of every frame shows map (not past the map edge or the horizon); fail otherwise
- **M04** (layer 2 math): Globe scenes: texels per degree drawn at the view centre (finest detail patch there, else the globe texture) / screen px per degree, lowest over frames: fail < 0.5x, warn < 1.0x
- **G01** (layer 2 math): Every grade LUT (segment, beat, profile) exists and parses as a 3D .cube (LUT_3D_SIZE 2-256, N^3 entries, DOMAIN_MAX > DOMAIN_MIN); fail otherwise
- **R56** (layer 2 math): Camera motion under 100 px at 1080x1920 warns by default, is informational for declared static/read intent and produces a fail when local intent explicitly expects motion (subject to the normal preflight severity policy). Path legs are checked separately.
  Camera intensity is not changed by default. `segments[].creative_autofix: true` (native or HyperFrames), or explicit native `camera_move.auto_intensity: true`, opts into intensity adjustment in 0.05 steps up to 6.0. Static/read intent prevents this creative mutation.
- **R62** (layer 2 math): Caption font >= 0.6 of the configured size after width fitting; caption width inside the safe area
- **R63** (layer 2 DOM): HyperFrames elements (headless Chrome probe at 30 %, 60 %, last frame, every `camera_path` key frame and per prop its first settled, middle and last pre-exit frame) inside the safe area (60 px sides, 100 px top/bottom; fail) and without pairwise overlap (warn; repeated seeks of one element are reported as one time span);
    caption clearance and automatic placement (see [Captions](TIMELINE.md#captions))
- **E01** (layer 2 DOM): Every region fill as painted in the probe equals its entity colour; fail otherwise; two entities sharing a colour (palette exhausted) = warn.
  Text colour is not checked.
  No finding when no region is painted.
- **R64** (layer 2 DOM): Profile `[legibility]` only: every adaptive text element (teardrop label, title, year, counter, area label) at each of its `samples` times has APCA Lc >= target - `lc_fail_margin` on the hard-case percentile of its background under the dilated glyph mask (painted ink, halo and vignette composited), and Lc >= `lc_floor` against its adjacent pixels;
    its painted halo within the width, blur and alpha bands and its counters open;
    fail otherwise.
  Pins, routes, region outlines and leaders: Lc >= `lc_line_fail` against the map around them, warn below `lc_line`.
  WCAG below `wcag_label` / `wcag_title` or a flagged element = warn.
  Rules: docs/SPEC.md, F.
- **Z01** (layer 2 place): `caption_style.max_bottom_y` only: every caption box bottom (after automatic placement and `y_overrides`) <= `max_bottom_y` = pass;
    any caption below it = fail.
  No finding without the key.
- **B01** (layer 2 place): Profile watermark only: its text box inside the safe area and intersecting no element box (DOM element rects at every probed seek, declared overlays, camera focus boxes) and no caption = pass;
    otherwise fail.
  No finding without a watermark.
- **T01** (layer layout): Profile `[print]` only: distinct typefaces per probed frame (DOM font families; native overlays) <= `print.max_typefaces`, caption and watermark excluded;
    fail otherwise.
- **L01** (layer layout): Profile `[layout]`, beats scenes, every frame: no text box (halo included) within `text_clear` of another text, head or leader;
    area labels `group_gap` from heads;
    when DOM is on, rendered boxes within 3 px of the plan at each beat's settled frame and mid-leg;
    fail otherwise.
  Rules: docs/SPEC.md, G.
- **L02** (layer layout): Pin label box + `edge_clear` crosses no region edge;
    area labels inset `area_inset` inside their region (or outside with a leader);
    fail otherwise.
  A label hidden by the planner = warn.
- **L03** (layer layout): Per frame <= `texts` texts, <= `labelled_pins` labelled pins, <= `routes` routes, <= `animating` tweens, at most one number >= 100 px, at most one region at full fill;
    fail otherwise.
- **L04** (layer layout): Largest text >= `hero_ratio` x the next (caption and hand notes excluded); fail otherwise.
- **L05** (layer layout): Z1 ink starts on a column edge, tops on `unit`, data card year and time on one baseline with the time ink `card_gap` right of the year ink;
    fail otherwise.
- **L06** (layer layout): Texts inside `safe` (caption excluded), Z1 texts inside Z1, pin labels, area labels, heads and routes inside Z2;
    a beats scene under a profile without `[layout]` fails;
    a beat whose content cannot fit Z2 = warn.
- **L07** (layer layout): Route starts on its map anchor and ends on its anchor or `route_pin_gap` (-2/+4 px) short of its `to_pin` head;
    never starts or ends inside text;
    >= `route_clear` from text;
    fail otherwise.
- **L08** (layer layout): Label moves: at most `moves_per_shot` non-urgent moves per shot, never two within `move_gap_s`; fail otherwise.
- **L09** (layer layout): Highlighted (full) region fill under the caption box <= `caption_ground` of its area; fail otherwise.
- **L10** (layer layout): Hold >= max(`min_hold_s`, chars / `chars_per_s`) (to the segment end for the last span), exits `exit_min_s`-`exit_max_s`, entries after the camera settles and after the exits, stagger >= `stagger_min_s`;
    fail otherwise.
- **F01** (layer props): Every prop image exists, decodes as PNG or WebP and has transparent pixels;
    a model prop renders to a sprite that does and is not fully transparent (the message names referenced files that are missing);
    fail otherwise.
- **F02** (layer props): Source width (model props: sprite size) / largest on-screen width over the shown frames (zoom, depth and pop included): fail < 1.0x, warn < 1.5x.
- **F03** (layer props): On every shown frame the prop box (rotated ink box plus shadow) intersects no pin head, label, title, figure, card, heading, note or area label (beats: layout items of every frame; DOM rects at the probe seeks) and no caption shown on that frame;
    fail otherwise.
  Props join the caption avoid-list (R63) for their frames.
- **F04** (layer props): On every shown frame the prop box lies inside the safe area (60 px sides, 100 px top/bottom); fail otherwise.
- **H01, C01, C02, V01, N01, P01, K01, K02, D01, D02** (layer profile): Channel style gates, after layer 2 (see [Style gate configuration](#style-gate-configuration))
- **AS01-AS06** (layer profile): Anti-AI-slop gates: decorative side-strip panels, copy tics, motion tics, minimum readable on-screen time, composition source tells in project sources and built HTML, and tell-hue literals (see [Style gate configuration](#style-gate-configuration)).
  AS01, AS02, AS03, AS05, AS06 are style-class and warn by default; AS04 is technical-class, warn by default.
  Geoverum has no `[gates.anti_slop]` table, so all six stay at warn (AS01 is not raised to fail).
- **G02** (layer profile): Geo/fact gate on map regions and geographic pins (see [Style gate configuration](#style-gate-configuration)); technical class, always blocks on fail
- **R38** (layer 3): Snapshot per segment at 1/4 size and post-render samples: black frame (black fraction > 0.85 or mean < 12) fails. Decode failures remain hard errors. Creative intent never waives this integrity check.
- **R38space** (visual advice): a third with std < 6 and detail < 1.5 warns; `intent.negative_space: true` with a reason makes that segment's finding informational. This heuristic does not prove the renderer is broken. Explicitly requiring `R38space` in the profile or project restores a blocking result, even when local negative-space intent is present; required policy takes precedence over the local waiver.
- **AIQA** (layer 3): Optional (`--ai-qa`): snapshots to the AI provider's QA models;
    overlap or cut-off text = fail;
    API unavailable = warn;
    invalid agent response = error.
  `build_report.json` -> `ai_qa` holds `provider`, `model`, `retries`, `fallback`
- **QA** (layer post-render): Every 5th frame at 1/4 size: R38/R38space per sample; R56px mean luma difference below 0.5 per segment warns by default, is info for static/read intent, and fails for explicit moving intent even in draft mode. Too few samples to estimate motion are informational for static/read/unspecified shots; explicit moving intent fails with "cannot verify expected motion" instead of a false freeze claim. No decoded samples is an integrity failure. Exact frame counts and stream timing are independently verified by the build and remain hard errors regardless of intent.
- **S01** (layer delivery): Profile silence gate on the final file (see [Style gate configuration](#style-gate-configuration))
- **A02** (layer delivery): Profile `[audio] require_source` only: every music and SFX file of the final mix (without `[sound]`: `audio_chain.bgm_path`) has provenance `source` equal to `require_source` = pass;
    any other source (`local-test`, `project-file`) = fail.
  No finding without the key.
  Gate code: `video_engine/gates/audio_source.py`.
- **A01** (layer delivery): Final file at `target_lufs` +/- `tolerance_lu` and true peak <= `true_peak` (default profile: -14.0 LUFS +/- 0.2 LU, -1.5 dBTP).
  Two-pass loudnorm (measure, then `linear=true` with measured values + limiter at TP - 0.5 dB) in the master audio pass;
    re-measure;
    up to 2 corrective gain passes on the audio file (volume + limiter), then the stream-copy mux;
    still outside = build fails.
  Values recorded in `build_report.json` -> `loudness`, with the loudness range `measured_lra` (information only).

## Style gate configuration

Channel style gates are switched on by their `[gates.<name>]` table in the channel profile; an absent table means the gate is off, and the default profile has none.
Profile format and precedence: PROFILES.md.

- **H01** (`[gates.hook]`)
  - Keys: `vo_warn_s`, `vo_fail_s`, `caption_warn_s`, `caption_fail_s`, `graphic_warn_s`, `graphic_fail_s`, `open_on_moving_map`
  - Rule: Narration start (end of a leading pause in the voiceover), first caption start and first pin / card / drawn overlay: later than the warn value = warn, later than the fail value = fail.
    `open_on_moving_map`: segment 0 must be HyperFrames with a `camera_path` or a camera preset other than `static`, else fail.
- **C01** (`[gates.cuts]`)
  - Keys: `warn_per_10s`, `fail_per_10s`, `first_warn_s`, `first_fail_s`
  - Rule: Shot changes = segment boundaries;
      a boundary is a hard cut unless profile `[transitions]` draws a transition there (camera moves, pans, zooms, pulses and whips inside a segment are not shot changes).
    Hard-cut rate above `warn_per_10s` = warn;
      shot-change rate above `fail_per_10s` = fail;
      first hard cut earlier than `first_warn_s` = warn;
      first shot change earlier than `first_fail_s` = fail.
    A transition window that leaves its two segments or overlaps the next window = fail.
- **V01** (`[gates.visual_type]`)
  - Keys: none
  - Rule: A shot change (hard cut, zoom whip or page turn, named in the message) between two segments of the same visual type = warn;
      between two HyperFrames segments on the same map asset = fail.
- **N01** (`[gates.novelty]`)
  - Keys: `max_gap_s`
  - Rule: Gap between new on-screen events (segment start, pin, card, drawn overlay start, `camera_path` key) longer than `max_gap_s` = warn.
- **P01** (`[gates.camera]`)
  - Keys: `hold_warn_s`, `hold_fail_s`, `moving_warn`, `still_zoom_ln_s`, `still_pan_w_s`
  - Rule: Planned camera per frame (HyperFrames camera legs, native `camera_move` windows; native footage without a camera move is not counted; transition frames count as moving): a frame is still when its zoom speed is below `still_zoom_ln_s` (ln scale / s) and its frame-centre speed (2D) below `still_pan_w_s` (frame widths / s).
    A run of still frames longer than `hold_warn_s` / `hold_fail_s` = warn / fail; moving fraction below `moving_warn` = warn.
- **P02** (`[gates.camera]`)
  - Keys: `min_drift_per_s`
  - Rule: Theme `camera_drift_per_s` below `max(min_drift_per_s, still_zoom_ln_s)` = fail (waits between camera moves would be still).
    Absent key = no P02.
    Geoverum: 0.06 ln/s.
- **K01** (`[gates.keyword_return]`)
  - Keys: `hook_s`, `end_s`, `min_chars`
  - Rule: No word (>= `min_chars` letters) of the on-screen text or captions of the first `hook_s` on screen in the last `end_s` = warn.
- **K02** (`[gates.ending_keyword]`)
  - Keys: `hook_s`, `end_s`, `min_chars`
  - Rule: No hook keyword of the first `hook_s` is still on screen on the final frame = fail (absent hook keyword = warn).
    `end_s` is reserved for the last-seconds variant.
- **C02** (`[gates.cta]`)
  - Keys: `phrases`
  - Rule: A call-to-action or end-card phrase in on-screen text or captions (whole words, case-insensitive) = fail.
- **D01** (`[gates.duration]`)
  - Keys: `min_s`, `max_s`
  - Rule: Total length outside `min_s`-`max_s` = warn.
- **D02** (`[gates.duration_exact]`)
  - Keys: `target_s`, `tolerance_s`
  - Rule: Total length outside `target_s` +/- `tolerance_s` = fail.
- **G02** (`[gates.geo]`)
  - Keys: `require_land`
  - Rule: Region polygons must have area and not cross themselves;
      a beat pin must lie inside its beat region;
      a pin label naming a region must sit inside it;
      with `require_land`, a geographic pin over water = fail (warn without it).
    Uses `/workspace/geo-library` Natural Earth land when present; absent library skips the land part.
- **S01** (`[gates.silence]`)
  - Keys: `max_gap_s`, `threshold_db`
  - Rule: Final file: a gap below `threshold_db` (ffmpeg `silencedetect`) longer than `max_gap_s` = fail.
- **AS01** (`[gates.anti_slop]`)
  - Keys: `panel`
  - Rule: A drawn `lower_third` or `text_card` uses a left accent strip on a dark panel: the confirmed decorative side-strip panel.
    Warn by default (Geoverum included); a profile makes it blocking with `[gates.anti_slop.levels] AS01 = "fail"` plus `[gates] require = ["AS01"]`.
    Use a plain panel or a `label`.
- **AS02** (`[gates.anti_slop]`)
  - Keys: `copy`, `em_dash_max`, `em_dash_per_500`, `aphoristic_min`, `buzzwords`
  - Rule: On-screen and caption copy: em-dash saturation (>= `em_dash_max`, or >= 2 above `em_dash_per_500` per 500 characters), a listed buzzword, >= `aphoristic_min` "No ..." rebuttal endings, or "theater" = warn.
- **AS03** (`[gates.anti_slop]`)
  - Keys: `motion`
  - Rule: `[motion] entrance` is an ease-in, `entrance_scale` < 0.9, `[layout.fly] ease_in` is an ease-in, or a figure uses a bounce entrance = warn.
- **AS04** (`[gates.anti_slop]`)
  - Keys: `read_time`, `chars_per_s`, `min_read_s`
  - Rule: A drawn overlay text is on screen shorter than max(`min_read_s`, characters / `chars_per_s`) = warn; technical-class, a profile may set `levels.AS04 = "fail"`.
- **AS05** (`[gates.anti_slop]`)
  - Keys: `source_tells`, `side_strip_px`, `clip_vertices`
  - Rule: Composition sources under the project and the built HyperFrames HTML: gradient text, `backdrop-filter` glass, a `border-left/right` wider than `side_strip_px`, `repeating-linear-gradient`, zero-offset glow, an organic/high-vertex `clip-path`, or a `kicker`/`eyebrow` class = warn per finding.
    Engine-generated template effects and markup (`MO.*` motion tokens, whip blur, built-in `lower_third` markup) are allowlisted; only project content is flagged.
- **AS06** (`[gates.anti_slop]`)
  - Keys: `palette`, `forbidden_hues`
  - Rule: A literal colour in a composition source or the built HyperFrames HTML whose hue is in a forbidden AI-tell range (default violet 260-310, cyan 170-197) and is not a declared brand colour = warn.
    Engine-generated template content is allowlisted.

- Warn thresholds must not be beyond fail thresholds; unknown gate tables or keys are errors.
