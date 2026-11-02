# Channel profiles

The format of a channel profile and the profiles that ship with the engine.

**What this document answers**

- Which tables and keys a profile has, and the precedence default < channel profile < project.
- How to split a profile into several files with `include`.
- What the `default` and `geoverum` profiles set.
- Which safety limits a profile cannot loosen.

- Files: `profiles/default.toml` holds the engine values; `profiles/<channel>.toml` lists only the keys a channel changes. Tables merge key by key.
- Precedence: `default.toml` < channel profile < project values (`caption_style` keys, `audio_chain.target_lufs` / `true_peak` / `lra`).
- Tables:

- `description`: free text
- `[caption_style]`: Python caption keys (`auto_place`, `clearance`, `fixed_y`, `max_bottom_y`) and any Rust `Style` field (see [Captions](TIMELINE.md#captions))
- `[audio]`: `target_lufs`, `true_peak`, `lra`, `tolerance_lu` (A01 tolerance), `require_source` (`"epidemic"` or absent: source every music and SFX file must have, gate A02; docs/SOUND.md)
- `[draft]`: `min_seg_s`, `max_seg_s` (pause segmenter and draft prompt segment length), `rules` (prompt rules appended after the engine rules, numbered from 6), `map_path` (true = the draft schema offers `map_path` camera keys for map segments), `scene` (opt-in draft map scene keys: `globe`, `props`, `morph`, `beats`; beats also need `[layout]`; regions, routes, borders, figures, flag characters and pin flags are always offered; docs/AI.md)
- `[theme]`: Renderer values written into the HyperFrames scene (`theme`): `camera_drift_per_s`, `camera_start_speed`, `camera_end_speed`, `whip_s`, `whip_blur_px`, `reveal_ease` (camera move `reveal`, CSS cubic-bezier, default `[0.28, 0.37, 0.29, 0.91]`), `region_fill_opacity`, `region_edge`, `region_edge_px`, `region_glow_px`, `pin_style`, `pin_colour`, `label_font`, `pin_label_px`, `route_colour`, `route_width_px`, `route_dash_px`, `route_gap_px`, `route_arrow_px`, `route_draw_s`, `region_glow_alpha`, `title_font`, `title_px`, `title_glow_px`, `title_tint`, `text_outline_px`, `text_outline_colour`, `text_outline_alpha`, `text_shadow_dy_px`, `text_shadow_blur_px`, `text_shadow_alpha`, `figure_px`, `counter_s`, `number_group`, `number_decimal`.
  A theme font role (`label_font = "script"`, `title_font = "title"`; a year figure always uses the serif) adds its font file to the HyperFrames build and cache key.
  `palette` and `[theme.entities]` resolve entity colours into the scene elements and are not in `theme`.
- `[motion]`: Named motion tokens: `ease_out`, `ease_in_out`, `ease_drawer` (CSS cubic-bezier, Emil Kowalski), `micro_frames` (4), `overlay_frames` (7), `panel_frames` (12), `stagger_frames` (2) at 30 fps, role eases `entrance` (`power2.out`), `move` (`power2.inOut`), `exit` (`power2.in`), `entrance_scale` (0.96) and `overlay_fade_s` (0.20).
  The role names keep the engine look; set one to a token curve to adopt it.
  The HyperFrames scene carries `theme.motion`; native drawn overlay fades read `overlay_fade_s`.
  A default `[motion]` table is left out of the profile hash.
- `[camera]`: HyperFrames camera pulses inside every drift (the camera waiting on a view): `pulse_zoom` (ln zoom of a pulse, 0-1; 0 = no pulses), `pulse_s` (length), `pulse_gap_s` (minimum drift before, between and after pulses), `pulse_pan` (frame-centre shift of a pulse away, frame widths, sides alternate), `pulse_ease` (CSS cubic-bezier), `pulse_anchor` (screen point, frame fractions, that stays put), `pulse_direction` (`in` = push in then back, `out` = pull out then back), `max_zoom` (deepest camera zoom: camera keys, layout fit and draft `map_path` stop there; 1-64).
  Defaults: 0, 0.8, 0.6, 0, [0.3, 0, 0.15, 1], [0.5, 0.5], `out`, 4.0.
  A default `[camera]` table is left out of the profile hash.
  Pulse values are part of the HyperFrames scene theme (`camera_pulse`) only when `pulse_zoom` > 0.
- `[grade]`: Default full-frame grade of every segment and beat without its own (same keys as segment `grade`; `lut` relative to the profiles directory).
  Absent = no grade; absent leaves the profile hash unchanged.
- `[transitions]`: Segment-boundary transitions drawn from the segment files and laid over them by the final mux: `kind` (`cut`, `crossfade`, `state`, `zoom_whip`, `zoom_through`, `page_turn`; default `cut`), `by_type` (visual type -> kind; the incoming segment's type wins, then the outgoing one's), `whip_out_s` (0.1), `whip_blur_in_s` (0.1, <= `whip_in_s`), `whip_in_s` (0.4), `whip_scale` (1.3), `whip_blur` (0.12), `page_s` (0.4), and for `zoom_through` `through_out_s` (0.4), `through_scale` (4.0), `through_blur` (0.25), `through_in_s` (0.5), `through_in_scale` (1.0), `through_blur_in_s` (0.3, <= `through_in_s`), `through_flash` (0, white flash strength 0-1). zoom_whip: the outgoing shot pushes in to `whip_scale` over `whip_out_s` with a radial blur rising to `whip_blur`, the incoming shot settles from `whip_scale` to 1 over `whip_in_s` with the blur fading over `whip_blur_in_s`. zoom_through: the outgoing shot accelerates (power2.in) into a push-in to `through_scale` with a radial blur rising to `through_blur` and the flash peaking on its last frame; the incoming shot settles from `through_in_scale` to 1 (power4.out) while blur and flash fade over `through_blur_in_s` (`through_in_scale` 1 leaves the landing to the incoming scene's camera, e.g. a `reveal` key). page_turn: the outgoing last frame folds away from the bottom-right corner over the incoming shot for `page_s`.
  No transition = the concat of the segments unchanged.
- `[watermark]`: Optional channel mark burned into the final video by the composite pass, above the captions, in every segment: `text` (one line, 1-40 chars), `font` (`inter` = Inter Bold, `title` = Archivo Black), `px` (16-120, px at 1920 height, default 36), `colour` (`#rrggbb`, default white), `opacity` (0-1, default 0.85), `shadow` (soft dark shadow, default true).
  The text's right edge sits on the right edge and its top on the top edge of the safe area (60 px sides, 100 px top).
  Absent table = no watermark and the mux graph without overlay.
  Not in any segment cache key.
- `[sound]`: Optional SFX cues, one per render event (`video_engine/sfx.py`), and the mixer (`video_engine/mixer.py`).
  Mixer keys: `duck_db` (music gain while the voiceover speaks, -40 to <0, default -12), `duck_attack_s` (ramp down before a phrase, default 0.15), `duck_release_s` (ramp up after it, default 0.4), `duck_merge_s` (word gaps up to this long stay ducked, default 0.5), `fade_in_s` / `fade_out_s` (music, default 0.3 / 1.0).
  Speech spans come from the voiceover word timestamps; ducking applies when `audio_chain.auto_ducking` is on and a voiceover is set.
  `[sound.events]`: render event -> SFX kind;
    events `pin` (teardrop: when the drop first touches its shadow, 0.22 s after it appears; dot: when it appears), `region` (fade-in start), `route` (mid-draw), `whip` (mid-move), `label` (HyperFrames card, drawn native overlay), `title` (entrance), `figure` (bounce figure entrance), `counter_tick` (each frame on which a counter's shown text changes).
  `[sound.kinds.<kind>]` for every mapped kind: `gain_db` (-60 to 0), `align` (`onset`: the file starts on the event; `peak`: its loudest sample lands on the event, default `onset`), `max_s` (cue length cap, default 2.0).
  Kinds: `pop`, `tick`, `whoosh`, `click`, `beep`, `counter_tick`, `impact`, `riser`.
  Unknown events or kinds and events mapped to a kind without a table are errors.
- `[gates]`: `require`: style gate ids (H01, C01, C02, V01, N01, P01, P02, K01, K02, D01, D02, S01, AS01, AS02, AS03, AS05, AS06) whose fail findings block a build.
  Every other style gate reports at most warn.
  Technical gates block on fail unless listed in `advisory`.
  `advisory`: technical gate ids this channel lowers from fail to warn (never T02, R07, R14, A01, A02, W01, R38, R56px, M01, M03, G01, L22).
  AS04 is technical-class and a profile raises it with `[gates.anti_slop.levels] AS04 = "fail"`.
- `[gates.<name>]`: Channel style gates (see [Style gate configuration](GATES.md#style-gate-configuration)). Absent table = gate off; the default profile has none.
- `[gates.anti_slop]`: Anti-AI-slop rules `AS01`-`AS06` (see [Style gate configuration](GATES.md#style-gate-configuration)).
  `level` (default `warn`) and per-rule `[gates.anti_slop.levels]`;
    `panel`, `copy`, `motion`, `read_time`, `source_tells`, `palette` switch rules on, plus `em_dash_max`, `em_dash_per_500`, `aphoristic_min`, `buzzwords`, `chars_per_s`, `min_read_s`, `forbidden_hues`, `side_strip_px`, `clip_vertices`.
  Taste rules stay warn unless a profile raises them.
  Geoverum has no `[gates.anti_slop]` table: AS01-AS06 stay warn, including technical-class AS04.
- `[resources]`: `workers` (0-64, 0 = auto): shared worker budget of a build (see [Resources](BUILD.md#resources)). `VIDEO_ENGINE_WORKERS` overrides it.

- `[legibility]` (optional): adaptive map text for teardrop labels, title, years, counters and area labels (water names blue italic): polarity, sepia ink, background-tinted halo that keeps counters open, escalation, stability schedule, overlay muting, cased pins, routes, region outlines and leaders, and gate R64.
  Keys and rules: docs/SPEC.md, section F. Absent = theme text paint and R64 off.
- `[print]` (optional): printed typography and map grade: the map surface (paper or dark imagery) fixes the text polarity;
    paper text is ink multiplied into the map (no halo, glow or box), EB Garamond titles inside their region, a centred Playfair year, Cinzel headings with rules, EB Garamond Italic pin labels above the pin on a leader, Kalam hand notes;
    regions with a multiplied fill and solid edge;
    paper texture and grain;
    gates T01 and B01 clearance.
  Keys and rules: docs/SPEC.md, section F2.
  Absent = theme typography.
## Shipped profiles

- `default`: the engine values; every anti-slop rule warns and no channel gate is required.
- `geoverum`: map-led geography shorts.
  - Captions: white Inter Bold 84 px (the approved size; 92 and 104 px were rejected in the 2026-11-01 A/B), no outline, never a backing box, soft shadow 0 3 px with blur 4 at 55 %, no per-word highlight, at most 2 words.
    `fixed_y` 1382 (about 0.72 H), `max_bottom_y` 1480, `auto_place` inside `place_band` 0.60-0.76 H, `clearance` 0.
  - Audio: -14 LUFS and -1.5 dBTP (the default), music and SFX from Epidemic Sound only (A02), music ducked -12 dB under the voiceover with keyframed ramps.
    SFX cues: pin and figure `pop`, label and region `tick`, route and whip `whoosh` (peak on the move), `counter_tick` per counter step, title `impact`.
  - Pacing: segments of 0.8-30 s with free cuts; hard cuts, with a 0.2 s state dissolve between segments on one map; no page turns and no map morphs.
  - Camera: zoom up to 16x; drift of 0.06 per second between moves (counts as P01 motion).
  - Render: HyperFrames quality `high` at CRF 8; intermediates CRF 8 `medium`; final CRF 12 `veryslow`, 10-bit (High 10), capped at 40 Mb/s; map JPEG quality 95.
  - Gates: every style gate is on except C01 (cut rate), V01 (visual type) and D02 (exact duration).
    Only S01 is required.
    R64, E01, R56, T01, R44, AS04, F03 and L02-L10 (except L06) are advisory.
    Anti-slop AS01-AS06 warn.
  - Look: muted OKLCH entity palette of 17 colours with six unpinned (teal, umber, wine, mauve, jade, olive), adaptive legibility (`[legibility]`, gate R64), printed typography (`[print]`), watermark "Geoverum" in Inter Bold 40 px, white at 85 % with a shadow.
  - Files: `profiles/geoverum.toml` holds the channel settings and `include`s `profiles/geoverum/entities.toml`, `legibility.toml`, `print.toml`, `sound.toml` and `layout.toml`.
  - Taste notes: `profiles/taste/geoverum.json`; `engine taste geoverum --lint` checks them against this profile.

## Splitting a profile with `include`

A channel profile may list other `.toml` files of the profiles directory on a single top-level line, before its first table:

```toml
include = ["geoverum/entities.toml", "geoverum/layout.toml"]
```

- Paths are relative to the profiles directory, end in `.toml` and must stay inside it.
  An included file cannot include another file, and `default.toml` cannot include.
- Merge order: `default.toml` < included files (in list order) < the channel file itself.
  A key set in the channel file wins over the same key in an included file; later includes win over earlier ones.
- The merged profile and its hash do not depend on how the keys are split between files, so moving a table into an included file never changes a segment cache key.
  Check with `profiles.load(name).blake3` before and after.
- Included files are validated like any profile text: unknown keys are errors, and a TOML error names the included file.
- `profiles.flatten(name)` returns a channel profile as one text (its own text without the `include` line, then each included file) for tools and tests; the `include` line must be a single-line list for this.


- Global safety limits: a profile may set `audio.true_peak` and `audio.tolerance_lu` only at or below the default profile value; a looser value is an error.
  Gates T02, R07, R14, A01, A02, W01, R38, R56px, M01, M03, G01 and L22 (`profiles.NEVER_ADVISORY`) and post-render QA are identical for every profile;
    no `[gates] advisory` list can lower them.
- Unknown keys, wrong types, `clearance` < 0, `fixed_y` or `max_bottom_y` outside 0-1920 or `lra` <= 0 are errors naming the profile.
- Segment cache keys contain the profile values that reach the segment's renderer: the HyperFrames key contains the scene, which holds `theme`, `print`, `layout` and `legibility`, plus the map derivative key and the toolchain identity (docs/RUST.md, Cache-key rules);
    native segment keys contain no profile value (no profile key reaches the native renderer).
  `build_report.json` -> `profile` records the name and the BLAKE3 hash of the merged profile.
