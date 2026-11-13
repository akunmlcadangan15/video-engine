# Channel profiles

The format of a channel profile and the profiles that ship with the engine.

**What this document answers**

- Which tables and keys a profile has, and the precedence default < channel profile < project.
- How to split a profile into several files with `include`, and how to build one profile on another with `extends`.
- How to see the resolved profile and where each key comes from (`engine profile NAME --origin`).
- What the `default` and `geoverum` profiles set.
- Which safety limits a profile cannot loosen.

- Files: `profiles/default.toml` holds the engine values; `profiles/<channel>.toml` lists only the keys a channel changes. Tables merge key by key.
- Precedence, lowest first: model field defaults (`video_engine/profile_schema.py`) < `default.toml` < the profile's `extends` chain (for each profile its `include` files, then its own file) < project `profile_overrides` < project values (`caption_style` keys, `audio_chain.target_lufs` / `true_peak` / `lra`).
  Fallback is per key: a key a profile does not write takes the value of the layer below. A profile that cannot be read or validated is an error; the engine never substitutes the default profile for it.
- `engine profile NAME` prints the resolved profile; `--origin` adds the file that set each key (or `(engine default)`); `engine schema profile` prints the JSON Schema of the tables.
- Tables:

- `description`: free text
- `[caption_style]`: Python caption keys (`auto_place`, `clearance`, `fixed_y`, `max_bottom_y`, `place_band`, `forbid_box`, `ab_sizes`) and any Rust `Style` field (see [Captions](TIMELINE.md#captions)).
  A key in neither set, a value of the wrong type or an unknown `slot` is an error when the profile (or a timeline that sets `caption_style`) loads, with the closest known key named (`profiles.check_caption_style`).
- `[audio]`: `target_lufs`, `true_peak`, `lra`, `tolerance_lu` (A01 tolerance), `require_source` (`"epidemic"` or absent: source every music and SFX file must have, gate A02; docs/SOUND.md), `aac_bitrate` (AAC kbps of the master audio and of the corrective gain passes; integer 64-320; default 192, and 192 is left out of the profile hash). `[render] aac_bitrate` overrides it, including a project `profile_overrides.render.aac_bitrate`.
- `[type]`: base of the type scale (`title_px`, `title_px_paper`, `pin_label_px`). A size a channel sets here is written to every surface the channel leaves open: `title_px` -> `[theme] title_px` and `[layout.sizes] title`; `title_px_paper` -> `[print] title_px`; `pin_label_px` -> `[theme] pin_label_px` and `[layout.sizes] pin_label`.
  A surface key a channel file writes wins, and a surface table the profile does not have (`[layout]`, `[print]`) stays absent. See [STRUCTURE](STRUCTURE.md).
- `[draft]`: `min_seg_s`, `max_seg_s` (pause segmenter and draft prompt segment length), `rules` (prompt rules appended after the engine rules, numbered from 6) or `rules_file` (single-source markdown `profiles/<channel>/creative.md`; mutually exclusive with `rules`), `map_path` (true = the draft schema offers `map_path` camera keys for map segments), `scene` (opt-in draft map scene keys: `globe`, `props`, `morph`, `beats`, `camera3d` (draft key `map_camera3d`: a named 3D camera move, timeline `camera3d_preset`); beats also need `[layout]`; regions, routes, borders, figures, flag characters and pin flags are always offered; docs/AI.md)
- `[theme]`: Renderer values written into the HyperFrames scene (`theme`): `camera_drift_per_s`, `camera_start_speed`, `camera_end_speed`, `whip_s`, `whip_blur_px`, `reveal_ease` (camera move `reveal`, CSS cubic-bezier, default `[0.28, 0.37, 0.29, 0.91]`), `region_fill_opacity`, `region_edge`, `region_edge_px`, `region_glow_px`, `pin_style`, `pin_colour`, `label_font`, `pin_label_px`, `route_colour`, `route_width_px`, `route_dash_px`, `route_gap_px`, `route_arrow_px`, `route_draw_s`, `region_glow_alpha`, `title_font`, `title_px`, `title_glow_px`, `title_tint`, `text_outline_px`, `text_outline_colour`, `text_outline_alpha`, `text_shadow_dy_px`, `text_shadow_blur_px`, `text_shadow_alpha`, `figure_px`, `counter_s`, `number_group`, `number_decimal`.
  Style options, left out of the profile hash at their defaults (the engine look): `vivid_chroma_scale` and `vivid_l` (on a dark satellite map, i.e. no `[print]` or a map `[print]` classifies "dark", entity colours keep their hue at `vivid_chroma_scale` x chroma with OKLCH L mapped from 0.38-0.84 onto `vivid_l`, `entities.vivid_colour`; paper maps keep the palette; gate E01 compares fills with these scene colours),
  `title_entrance` (`fade` | `track`: letters close in from 0.35 em at the settled width, and track out on a layout exit), `title_anchor` (`top` | `region`: a scene title without beats enters above its `title_entity` region, below it near the top, inside the safe area),
  `label_box_entrance` (`pop` | `type`: a box label grows left to right with its text at `label_type_cps`, 0.25-1 s), `label_box_case` (`upper` | `none`), `label_box_tilt_deg`, `figure_pop` (`elastic` 0.9 s from scale 0 | `back` 0.45 s from 0.6) and `figure_accent` (colour of number figures without an entity; years keep theirs).
  A theme font role (`label_font = "script"`, `title_font = "title"`; a year figure always uses the serif) adds its font file to the HyperFrames build and cache key.
  `palette` and `[theme.entities]` resolve entity colours into the scene elements and are not in `theme`.
- `[motion]`: Named motion tokens: `ease_out`, `ease_in_out`, `ease_drawer` (CSS cubic-bezier, Emil Kowalski), `micro_frames` (4), `overlay_frames` (7), `panel_frames` (12), `stagger_frames` (2) at 30 fps, role eases `entrance` (`power2.out`), `move` (`power2.inOut`), `exit` (`power2.in`), `entrance_scale` (0.96) and `overlay_fade_s` (0.20).
  The role names keep the engine look; set one to a token curve to adopt it.
  The HyperFrames scene carries `theme.motion` (`entrance`, `move`, `exit`, `entrance_scale`, `overlay_s`, `panel_s`
  read by the template; `micro_s` and `stagger_s` validated and exported); native drawn overlay fades read
  `overlay_fade_s`. Element layouts and paths keep their timing in `[layout.timing]`, `[theme]` and `[transitions]`.
  A default `[motion]` table is left out of the profile hash.
- `[camera]`: HyperFrames camera pulses inside every drift (the camera waiting on a view): `pulse_zoom` (ln zoom of a pulse, 0-1; 0 = no pulses), `pulse_s` (length), `pulse_gap_s` (minimum drift before, between and after pulses), `pulse_pan` (frame-centre shift of a pulse away, frame widths, sides alternate), `pulse_ease` (CSS cubic-bezier), `pulse_anchor` (screen point, frame fractions, that stays put), `pulse_direction` (`in` = push in then back, `out` = pull out then back), `max_zoom` (deepest camera zoom: camera keys, layout fit and draft `map_path` stop there; 1-64), `mode` (`planar`, the default, or `3d`: every map scene uses the full 3D camera of `gsap_scene_config.camera3d` unless its `camera_mode` says otherwise; the planar default adds nothing to the hash).
  Defaults: 0, 0.8, 0.6, 0, [0.3, 0, 0.15, 1], [0.5, 0.5], `out`, 4.0.
  A default `[camera]` table is left out of the profile hash.
  Pulse values are part of the HyperFrames scene theme (`camera_pulse`) only when `pulse_zoom` > 0.
- `[grade]`: Default full-frame grade of every segment and beat without its own (same keys as segment `grade`; `lut` relative to the profiles directory).
  Absent = no grade; absent leaves the profile hash unchanged.
- `[transitions]`: Segment-boundary transitions drawn from the segment files and laid over them by the final mux: `kind` (`cut`, `crossfade`, `state`, `zoom_whip`, `zoom_through`, `page_turn`; default `cut`), `same_type` (visual type -> kind when both neighbouring segments share that type, checked before `by_type`), `by_type` (visual type -> kind; the incoming segment's type wins, then the outgoing one's), `whip_out_s` (0.1), `whip_blur_in_s` (0.1, <= `whip_in_s`), `whip_in_s` (0.4), `whip_scale` (1.3), `whip_blur` (0.12), `page_s` (0.4), and for `zoom_through` `through_out_s` (0.4), `through_scale` (4.0), `through_blur` (0.25), `through_in_s` (0.5), `through_in_scale` (1.0), `through_blur_in_s` (0.3, <= `through_in_s`), `through_flash` (0, white flash strength 0-1). zoom_whip: the outgoing shot pushes in to `whip_scale` over `whip_out_s` with a radial blur rising to `whip_blur`, the incoming shot settles from `whip_scale` to 1 over `whip_in_s` with the blur fading over `whip_blur_in_s`. zoom_through: the outgoing shot accelerates (power2.in) into a push-in to `through_scale` with a radial blur rising to `through_blur` and the flash peaking on its last frame; the incoming shot settles from `through_in_scale` to 1 (power4.out) while blur and flash fade over `through_blur_in_s` (`through_in_scale` 1 leaves the landing to the incoming scene's camera, e.g. a `reveal` key). page_turn: the outgoing last frame folds away from the bottom-right corner over the incoming shot for `page_s`.
  No transition = the concat of the segments unchanged.
- `[watermark]`: Optional channel mark burned into the final video by the composite pass, above the captions, in every segment: `text` (one line, 1-40 chars), `font` (`inter` = Inter Bold, `title` = Archivo Black), `px` (16-120, px at 1920 height, default 36), `colour` (`#rrggbb`, default white), `opacity` (0-1, default 0.85), `shadow` (soft dark shadow, default true).
  The text's right edge sits on the right edge and its top on the top edge of the safe area (60 px sides, 100 px top).
  Absent table = no watermark and the mux graph without overlay.
  Not in any segment cache key.
- `[sound]`: Optional SFX cues, one per render event (`video_engine/sfx.py`), and the mixer (`video_engine/mixer.py`). Every key has a default: a table with only mixer keys ducks the music and plays no cues (`events` and `kinds` default to empty).
  Mixer keys: `duck_db` (music gain while the voiceover speaks, -40 to <0, default -12), `duck_attack_s` (ramp down before a phrase, default 0.15), `duck_release_s` (ramp up after it, default 0.4), `duck_merge_s` (word gaps up to this long stay ducked, default 0.5), `fade_in_s` / `fade_out_s` (music, default 0.3 / 1.0).
  Speech spans come from the voiceover word timestamps; ducking applies when `audio_chain.auto_ducking` is on and a voiceover is set.
  `[sound.events]`: render event -> SFX kind;
    events `pin` (teardrop: when the drop first touches its shadow, 0.22 s after it appears; dot: when it appears), `region` (fade-in start), `route` (mid-draw), `whip` (mid-move of a `camera_path` whip, or of the push of a `camera3d_preset` `crash_zoom`), `label` (HyperFrames card, drawn native overlay), `title` (entrance), `figure` (bounce figure entrance), `counter_tick` (each frame on which a counter's shown text changes).
  `[sound.kinds.<kind>]` for every mapped kind: `gain_db` (-60 to 0), `align` (`onset`: the file starts on the event; `peak`: its loudest sample lands on the event, default `onset`), `max_s` (cue length cap, default 2.0).
  Kinds: `pop`, `tick`, `whoosh`, `click`, `beep`, `counter_tick`, `impact`, `riser`, `rumble` (`rumble` is a low bed for manual cues; it is not mapped to a render event unless a profile says so).
  Timeline cues in `audio_chain.sfx` use the same kinds, or a project file (docs/TIMELINE.md, docs/SOUND.md).
  Unknown events or kinds and events mapped to a kind without a table are errors.
- `[gates]`: `require`: lint and style gate ids (`LINT_GATE_IDS`, including R38space; see `engine gates`) whose findings block a build. R38space is an explicit opt-in strict composition check; when required it overrides local negative-space intent.
  Every other gate in `LINT_GATE_IDS` reports at most warn.
  Technical gates outside `LINT_GATE_IDS` (`F01`, and `AS04` when raised to `fail`) block a release build on fail unless listed in `advisory`.
  `advisory`: technical or lint gate ids this channel lowers from fail to warn (never T02, R07, R14, A01, A02, W01, R38, R56px, M01, M03, G01, L22 in `NEVER_ADVISORY`).
  AS04 is technical-class and a profile raises it with `[gates.anti_slop.levels] AS04 = "fail"`.
- `[gates.<name>]`: Channel style gates (see [Style gate configuration](GATES.md#style-gate-configuration)). Absent table = gate off; the default profile has none.
- `[gates.anti_slop]`: Anti-AI-slop rules `AS01`-`AS06` (see [Style gate configuration](GATES.md#style-gate-configuration)).
  `level` (default `warn`) and per-rule `[gates.anti_slop.levels]`;
    `panel`, `copy`, `motion`, `read_time`, `source_tells`, `palette` switch rules on, plus `em_dash_max`, `em_dash_per_500`, `aphoristic_min`, `buzzwords`, `chars_per_s`, `min_read_s`, `forbidden_hues`, `side_strip_px`, `clip_vertices`.
  Taste rules stay warn unless a profile raises them.
  Geoverum sets `[gates.anti_slop] level = "warn"` in `profiles/geoverum/core.toml`: AS01-AS06 stay warn, including technical-class AS04.
- `[resources]`: `workers` (0-64, 0 = auto): shared worker budget of a build (see [Resources](BUILD.md#resources)). `VIDEO_ENGINE_WORKERS` overrides it.

- `[legibility]` (optional): adaptive map text for teardrop labels, title, years, counters and area labels (water names blue italic): polarity, sepia ink, background-tinted halo that keeps counters open, escalation, stability schedule, overlay muting, cased pins, routes, region outlines and leaders, and gate R64.
  Keys and rules: docs/SPEC.md, section F. Absent = theme text paint and R64 off.
  Every key has a default (the engine baseline, calibrated on reference map videos), so a channel table lists only the keys it changes; `profiles/geoverum/legibility.toml` writes the full table on purpose, so the channel does not move when the baseline does.
- `[reference_style.*]`: the channel's catalogue of creator options (docs/REFERENCE-STYLE.md). The group names are fixed (`transitions`, `fill`, `label`, `data_viz`, `overlay`, `scene_variants`, `style`, `overlap`); `[reference_style.overlap]` (`allowed`, `rule`, `forbid`) is the one group gates read and is typed, so a misspelt key there is an error.
- `[print]` (optional): printed typography and map grade: the map surface (paper or dark imagery) fixes the text polarity;
    paper text is ink multiplied into the map (no halo, glow or box), EB Garamond titles inside their region, a centred Playfair year, Cinzel headings (plain by default; `heading_ornaments = true` adds the rules and diamonds), EB Garamond Italic pin labels above the pin on a leader, Kalam hand notes;
    regions with a multiplied fill and solid edge;
    paper texture and grain;
    gates T01 and B01 clearance.
  Keys and rules: docs/SPEC.md, section F2.
  Absent = theme typography.
## Shipped profiles

- `default`: the engine values; every anti-slop rule warns and no channel gate is required.
- `geoverum`: map-led geography shorts.
  - Captions: white Inter Bold 48 px, no outline, optional backing box, soft shadow 0 3 px with blur 4 at 55 %, active-word emphasis and at most 4 words.
    `fixed_y` 1366, `max_bottom_y` 1480, `auto_place` inside `place_band` 0.58-0.76 H, `clearance` 0. The effective timeline/profile, not dated taste notes, determines placement.
  - Audio: -14 LUFS and -1.5 dBTP (the default), optional `require_source` (unset by default so local or Epidemic Sound assets can be mixed; set `require_source = "epidemic"` to enforce A02), music ducked -12 dB under the voiceover with keyframed ramps.
    SFX cues: pin and figure `pop`, label and region `tick`, route and whip `whoosh` (peak on the move), `counter_tick` per counter step, title `impact`.
  - Pacing: suggested draft segments of 3-30 s with free cuts; hard cuts, with a 0.35 s state dissolve between segments on one map; page turns and map morphs disabled (`page_turn = false`, `map_morph = false`). Channel creative direction separates identity, defaults and alternatives; these are not universal shot quotas.
  - Camera: zoom up to 16x; drift of 0.06 per second between moves (counts as P01 motion).
  - Render: HyperFrames quality `high` at CRF 8; intermediates CRF 8 `medium`; final CRF 16 `medium`, 10-bit (High 10), capped at 40 Mb/s; map JPEG quality 95.
  - Gates: every style gate is on except C01 (cut rate), V01 (visual type) and D02 (exact duration).
    No style gate is required (`require = []`), and `advisory = []` because lint gates in `LINT_GATE_IDS` warn by default unless required; `engine gates --profile geoverum` shows their effective behavior.
    Anti-slop AS01-AS06 warn.
  - Look: muted OKLCH entity palette of 17 colours with six unpinned (teal, umber, wine, mauve, jade, olive), painted vivid on dark satellite maps (`vivid_chroma_scale` 1.8); titles Archivo Black 104 px white without outline that track in above their region; box labels in a darker shade of the entity colour (`label_box_lc` 80) that grow with their typed text, written case, tilted -3 deg; numbers 96 px in signal yellow `#FFD417` with a back pop; pins `#E61129`; adaptive legibility (`[legibility]`, gate R64), printed typography (`[print]`), watermark "Geoverum" in Inter Bold 40 px, white at 85 % with a shadow.
  - Landscape variant: `geoverum_landscape` (see Orientation).
  - Files: `profiles/geoverum.toml` holds only what 9:16 changes (caption geometry, runtime window, watermark size) and `include`s `profiles/geoverum/core.toml` (the shared look, caption style, watermark, gates and draft rules), `entities.toml`, `legibility.toml`, `print.toml`, `sound.toml` and `layout.toml`. `core.toml` leaves out every key that only repeats `default.toml`.
  - Taste notes: `profiles/taste/geoverum.json`; `engine taste geoverum --lint` exposes historical differences from the active profile without rewriting dated feedback. The director labels them historical preferences, not hard constraints.

## Orientation (portrait and landscape)

The engine renders portrait 9:16 (Shorts: 1080x1920, 2160x3840) and landscape 16:9 (1920x1080, 2560x1440,
3840x2160). The orientation comes from the frame: `settings.width > settings.height` is landscape, anything else
(square included) portrait. A timeline needs no new field.

- Reference canvas (`video_engine/units.py`): profile lengths and positions are px at 1080x1920 for portrait and at
  1920x1080 for landscape. Positions scale by `W / ref_w` and `H / ref_h`, lengths by the uniform `k = W / ref_w`
  (1440p: 1.333, 4K: 2). For an exact 9:16 or 16:9 frame the three are equal. Portrait numbers are evaluated with the
  same operands as before, so portrait renders, profile hashes and cache keys are unchanged.
- Safe area (R63, O01, F04, B01, R62): portrait 60 px sides and 100 px top/bottom; landscape 96 px sides and 54 px
  top/bottom (5 % title safe), at the reference canvas.
- Gate U01 warns when the frame aspect differs from its reference by more than 1 % (for example 1080x1350): layout
  values are then scaled non-uniformly as before.
- `[format] orientation = "portrait" | "landscape"` declares what a profile is written for. Undeclared: a profile with
  portrait geometry (`caption_style.fixed_y`, `max_bottom_y`, `place_band`, `[layout]` or `[print]`) counts as
  portrait, any other profile (the default) fits both. A timeline whose frame disagrees with its profile is rejected at
  load (`engine validate`). An undeclared `[format]` adds nothing to the profile hash.
- Captions in landscape: the Rust length keys (`size`, `margin_x`, `outline`, `shadow`, `shadow_blur`, `shadow_dx`,
  `shadow_dy`, `ab_sizes`) are px at 1920x1080 and are scaled by `k` (`captions.effective_style`, read by the
  compiler and by R62). Without a `size` / `margin_x` a landscape caption is 58 / 160 px (portrait Rust defaults 84 /
  90). `fixed_y`, `max_bottom_y` and `clearance` are px at 1080 height. Portrait caption styles pass unchanged.
- `[print]` positions have an approved range per orientation (`profiles.PRINT_RANGES`): portrait `year_cy` 520-600,
  `heading_baseline_y` 360-400, `year_cx` 0-1080; landscape `year_cy` 300-460, `heading_baseline_y` 120-200,
  `year_cx` 0-1920. `fixed_y` and `max_bottom_y` must lie in 0..1920 (portrait) or 0..1080 (landscape).
- `[watermark] corner`: `top_right`, `bottom_right`, `top_left` or `bottom_left`; absent = top right in portrait,
  bottom right in landscape. The text sits in that corner of the safe area. `px` is px at the reference height.
- HyperFrames scenes of a landscape frame carry `units` (`k`, `ref`, `safe`) for the JS twins, and theme `*_px`
  lengths are multiplied by `k` (1 at 1920x1080); cards and the big title sit at 0.10 / 0.70 and 0.10 of the height.
  Portrait scenes are unchanged.
- Native overlays (O01): fonts and paddings scale from the reference height; the heading baseline band is 120-200 px
  in landscape (portrait 360-400).
- Worker budget: one worker needs 1.8 GB at 1080p-class frames, scaled by pixel count above (4K: 7.2 GB).
- The final file's H.264 level must be 5.2 or lower (`streams.verify_level`; x264 picks 4.0/4.1 at 1080p, 5.1 at
  2160p30, 5.2 at 2160p60).

Landscape profiles:

- `profiles/landscape/format.toml`: an include for any landscape channel: `[format] orientation = "landscape"`,
  caption geometry (58 px, `margin_x` 160, `max_words` 4, `fixed_y` 900, `place_band` 0.74-0.88, `clearance` 90).
- `geoverum_landscape`: the Geoverum brand on 16:9. It includes the Geoverum tables plus `landscape/format.toml`,
  `geoverum/landscape/layout.toml` (12 columns of 104 px from x0 116; safe `[96,54,1824,1026]`, Z1 title band at the
  top left `[116,72,1100,200]`, Z2 map `[116,216,1804,880]`, title 88, year 112) and `geoverum/landscape/print.toml`
  (year 960 / 380, heading baseline 150). Watermark 28 px bottom right; duration 60-3600 s. Both `geoverum.toml` and
  `geoverum_landscape.toml` share their channel tables through `geoverum/core.toml` and the modular `geoverum/*.toml` includes, overriding only orientation-specific settings.
- Values not yet specified in a shipped profile: rhythm gates and duration for long-form, the landscape grid and title placement,
  the O01 minimum font per orientation, the grid backdrop cell basis (`fx.grid.cell_pct` is
  a share of H in both orientations), source quality policy at 4K, delivery metadata and thumbnail rule,
  and whether portrait captions at 2160x3840 should also scale (today they do not).

## Directing for two aspects (`[framing]`)

One story, two frames: the shot intent (`segments[].intent.shot`, docs/TIMELINE.md) and the second-based story gates
(hook, novelty, camera holds, silence, keyword, CTA) are the same for 9:16 and 16:9; the geometry gates read the
orientation's values (Orientation, above). `video_engine/framing.py` maps each shot to framing per orientation:

| Aspect | 9:16 | 16:9 |
|---|---|---|
| Subject | centre (0.5, 0.45) | rule of thirds (1/3, 0.5); compare side by side |
| Field of view | tight (intensity 1.0-1.2) | wide (intensity 0.6-0.9) |
| Reading order | top to bottom | left to right |
| Text slots | title top, caption bottom | title top left, caption lower third |
| Camera | push (`ken_burns_*`, `zoom_*`), vertical pans | lateral pans (`pan_right` / `pan_east`), crane (`pan_up`) |
| Watermark | top right | bottom right |

A profile may change single values per orientation and shot:

```toml
[framing.landscape.establish]
native = "pan_left"        # native camera preset of a still without camera_move
hf = "pan_west"            # HyperFrames camera_preset of a map scene without one
intensity = 0.6
subject = [0.66, 0.5]      # guidance for the draft and director
```

Keys: `native`, `hf`, `intensity`, `subject`, `read`, `title_slot`, `caption_slot`. Unknown orientations, shots or
keys are errors. A profile without `[framing]` keeps its hash. The draft prompt lists the shot vocabulary with the
framing of the project's orientation.

Not yet specified in a shipped profile: whether 16:9 is long-form or stays short, and whether maps stay the main visual language in
16:9 or footage may take a larger share.

## Splitting a profile with `include`

A channel profile may list other `.toml` files of the profiles directory on a single top-level line, before its first table:

```toml
include = ["geoverum/entities.toml", "geoverum/layout.toml"]
```

- Paths are relative to the profiles directory, end in `.toml` and must stay inside it.
  An included file cannot include another file, and `default.toml` cannot include.
- Merge order: `default.toml` < included files (in list order) < the channel file itself.
- `profiles/geoverum/core.toml`: shared orientation-independent core tables included by both `profiles/geoverum.toml` (9:16 portrait) and `profiles/geoverum_landscape.toml` (16:9 widescreen).
  A key set in the channel file wins over the same key in an included file; later includes win over earlier ones.
- The merged profile and its hash do not depend on how the keys are split between files, so moving a table into an included file never changes a segment cache key.
  Check with `profiles.load(name).blake3` before and after.
- Included files are validated like any profile text: unknown keys are errors, and a TOML error names the included file.
- `profiles.flatten(name)` returns a profile as one TOML text that loads to the same profile without its `include` / `extends` files, for tools and tests: the layers above `default.toml` merged table by table in load order (a table two files write appears once, the higher layer's keys winning), comments kept, `rules_file` replaced by the rules it resolves to.

## Building on another profile with `extends`

```toml
extends = "geoverum"          # this profile = geoverum (its includes and its file) + the keys below
include = ["mychannel/sound.toml"]

[watermark]
text = "My Channel"
```

- Merge order: `default.toml` < the base profile's `include` files < the base profile's file < this profile's `include` files < this profile's file. A chain may be up to 8 profiles long.
- A loop, a missing base, `extends = "default"` (every profile already builds on it) and `extends` inside an included file or `default.toml` are errors.
- There is no way to unset a key of the base: a base key that must not reach the child (portrait caption geometry under a landscape child) belongs in a file only the base's own format includes. That is why `geoverum_landscape` shares `geoverum/core.toml` through `include` and does not extend `geoverum`.

## Placeholders in the creative rules

A `[draft] rules_file` line may quote a value of the profile that loads it as `{{table.key}}` (`{{caption_style.size}}`, `{{layout.sizes.title}}`). The engine replaces it with that value of the effective profile, so profiles that share one rules file (`geoverum` and `geoverum_landscape`) each quote their own numbers. A placeholder that names no key, or a key that is not set, is an error naming the file.


- Global safety limits: a profile may set `audio.true_peak` and `audio.tolerance_lu` only at or below the default profile value; a looser value is an error.
  Gates T02, R07, R14, A01, A02, W01, R38, R56px, M01, M03, G01 and L22 (`profiles.NEVER_ADVISORY`) and post-render QA cannot be listed in `[gates] advisory`
    (the 8 kernel gates in `KERNEL_GATE_IDS` always block on fail, while R07, R14, R56px and A02 are in `LINT_GATE_IDS` and warn unless listed in `require`).
- Unknown keys, wrong types, `clearance` < 0, `fixed_y` or `max_bottom_y` outside 0-1920 (portrait) or 0-1080 (landscape) or `lra` <= 0 are errors naming the profile.
- Segment cache keys contain the profile values that reach the segment's renderer: the HyperFrames key contains the scene, which holds `theme`, `print`, `layout` and `legibility`, plus the map derivative key and the toolchain identity (docs/RUST.md, Cache-key rules);
    native segment keys contain no profile value (no profile key reaches the native renderer).
  `build_report.json` -> `profile` records the name and the BLAKE3 hash of the merged profile.
