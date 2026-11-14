# Video Engine — Specification

The specification of the engine: goal, architecture, components, gates, profiles and the data contract.

**What this document answers**

- What the engine must do, and the four layers it is built from (Goal, Architecture).
- How each component is specified: Rust crate, AI providers, gates, infrastructure, director, profiles (Components A to E).
- How adaptive legibility, printed typography and the beats layout work (Components F, F2, G).
- What `master_timeline.json` looks like (Data contract).

- Location: this repository (package + CLI `engine`); on the render machine it is checked out at /workspace/video-engine.
- Scope: standalone system.
  Code, configuration and toolchain (`vendor/`) live inside this repository; no path outside it is read except project inputs given on the command line.

## Documentation style (applies to every doc in this repo)
- Audience: people and AI agents.
  README, CONTRIBUTING, the glossary and the index are written in Indonesian for the team.
  The technical reference documents (SPEC, RUST, SOUND, RATIONALE, CLI, ENVIRONMENT, TIMELINE, PROFILES, GATES, BUILD, DELIVERY, AI, TESTING, LIMITS) are written in concise English.
  Code, comments and error messages are English.
  English prose uses British spelling (licence, colour, centre, normalise); field and key names keep the spelling of the code (`license` in `credits_manifest`).
- Reference documents state rules, facts, inputs, outputs and limits in a declarative tone: no conversational tone, no decision history, no comparison with past versions in normative text.
- Docs hold final, ready-to-use content only: no change reports, revision reasons or old-vs-new comparisons.
  Rationale goes in [RATIONALE.md](RATIONALE.md), and changes go in [CHANGELOG.md](../CHANGELOG.md).
- Every fact has one home; other documents link to it instead of repeating it.
- A long reference document opens with a "What this document answers" block of at most 5 bullets.
- Prose is written one sentence per line, and wide tables become lists, so that diffs and terminals stay readable.
- A document changes in the same commit as the code it describes; `python scripts/check_docs.py` checks links, anchors and generated tables.

## Goal
- Function: produce a finished vertical video from a master_timeline.json plus project assets.
- Supported edit types: cut/trim, camera moves, subtitles, HTML/GSAP motion graphics (maps, pins, cards, charts).
- Validation runs before render; a project that passes preflight renders once.
- Target: total processing 2-5 min per project on 8 vCPU / 15 GB RAM / no GPU.

## Architecture: 4 layers
1. PRE-PROCESSING & AI DIRECTOR (Python + Rust + the selected AI provider; with nothing selected, the agent file
   handoff): Rust makes 360p low-fps proxy + silence detection in /dev/shm (~3 s); the provider analyses and drafts
   master_timeline.json.
2. ZERO-RENDER PRE-FLIGHT GATES (< 5 s): text/credit gates from JSON/text; camera (R56) by curve math; DOM margins
   (R62/R63) via seek-at-3-points snapshots.
3. SMART ROUTER, parallel by segment type:
   A. NATIVE path (Rust PyO3 + FFmpeg, 8 vCPU): cut/trim, pan/zoom camera on footage/stills, audio chain, .ass subtitles.
   B. MOTION-GRAPHICS path (HyperFrames HTML/GSAP): ONLY map/pin/card/chart segments, with NO captions in HTML.
4. SINGLE-PASS MUX, PIXEL QA & AUTO DELIVERY (~20-45 s): concat A+B, burn captions .ass, audio normalise -14 LUFS,
   auto name; the Projects Manager bot uploads to Google Drive.

## Components
### A. Rust crate `hypoverum_core_rs` (PyO3 + maturin + rayon)
1. RAM disk: all temp I/O (segment cuts, proxies, .ass, audio extraction, HyperFrames segment output) in
   /dev/shm/video_engine/<project_id>-<pid>-<random>/, one dir per invocation (created with mkdtemp, removed by
   that invocation only). `project_id` matches `^[A-Za-z0-9_][A-Za-z0-9_.-]*$`; `settings.work_dir_shm`, when
   set, is the parent of these dirs and must resolve (symlinks followed) strictly inside the scratch root.
   Project folder on disk stays < 100 MB.
2. Integer frame index everywhere: frame_idx = round(seconds*fps) at the boundary only; all cuts, ffmpeg trims, GSAP
   durations (tl.js) derived from integer frames. At 30 fps an 8 s segment is exactly 240 frames. Frame counts always consistent.
3. .ass subtitle compiler: captions removed from the HyperFrames HTML template. Rust turns word timestamps into .ass with
   style, active-word highlight, automatic safe margins (so R62/R63/L22 pass by construction). Caption text/style edits never
   trigger a Chrome re-render.
4. Parallel ffmpeg executor: pure cuts via stream copy at keyframes or chunked parallel encoding
   (libx264 yuv420p BT.709; profile `[render]` segment_* / final_* values, else crf 16 veryfast for intermediates and
   crf 18 medium for the final composite). Camera moves on normal video/stills:
   focus box [ymin,xmin,ymax,xmax] -> smooth crop/scale (zoompan) expressions. BLAKE3 hash of segment config = cache key.
5. Fast audio chain (< 15 s) + caching: BLAKE3 of source audio/script; if unchanged, reuse cached word timestamps (0 s).
   xAI Grok Voice Transcribe (`POST https://api.x.ai/v1/stt`, model `grok-voice-transcribe-2.0`, `XAI_API_KEY`,
   16 kHz mono WAV, language from `audio_chain.language`, optional `stt.keyterms`, 300 s timeout, retries on
   429/503/transport errors) provides word-level timestamps. Master audio pass (own cache entry; the
   final file is a stream-copy mux of the composite video and this audio):
   sidechaincompress auto-ducking of BGM + loudnorm=I=-14:TP=-1.5:LRA=11. The silence-padded voiceover and the looped
   BGM run unbounded through the compressor and the mix; the mixed signal is cut to exactly duration x 48000 samples
   before loudnorm and padded and cut to that count again after it, so the audio stream has the exact sample count
   and identical PCM on every render.
6. Fast pixel gates: R38 (no third of frame empty; each third judged on its own per sample), R56 (visible motion), black-frame detection on sampled frames in
   memory, parallel, < 0.5 s.

### B. AI providers `video_engine/ai/`
- Shared, provider-agnostic: prompts, JSON schemas, normalise/assemble, gates, repair loop, reports (`draft.py`,
  `ai/tasks.py`). Requests are lists of parts: Text, Image (inline bytes), Media (audio/video file with transcript).
- Provider interface: `generate_json(task, parts, schema, request_id, attachments) -> AIResult` (task `planning` or
  `qa`), `cleanup() -> file deletion records`. AIResult records provider, model, retries, fallback, warning, tried.
- Selection: --provider > env VIDEO_ENGINE_AI_PROVIDER > `default` in ai_providers.toml (env VIDEO_ENGINE_AI_CONFIG) >
  agent. The shipped file sets no `default`.
- gemini (optional: `--provider gemini`, extra `gemini` / google-genai SDK). Models from `[gemini] models` and
  `qa_models` (shipped: gemini-3.8-flash) for planning, draft, director and visual QA. A failed call is an error;
  it is not sent to another provider. Unknown `[gemini]` keys are an error (known: `models`, `qa_models`).
  Rules: raw footage -> Rust 360p low-fps proxy in /dev/shm -> upload via the Gemini File API;
  media_resolution=MEDIA_RESOLUTION_LOW; thinking_config thinking_budget=1024; response_mime_type="application/json";
  every upload deleted after the last request and the deletion verified. Key: env GEMINI_API_KEY (never print it).
- agent: file handoff, no API. Request bundle <root>/<request_id>/ (request.md, schema.json, images/,
  contact_sheet.jpg, transcript.json, request attachments); exit code 3; the agent writes response.json; `--resume`
  validates it against schema.json and continues (draft: gates, then the next round's bundle with the gate errors up
  to max repairs). Bundles: P/ai_request/ (gitignored, `engine clean P`), director under --bundle-root.
- openai_compat: entries [providers.NAME] in ai_providers.toml (base_url, models, qa_models, api_key_env,
  capabilities image/audio/video, json_mode, timeout_s). Chat completions over httpx with JSON schema or JSON mode
  output and base64 inline images; audio -> transcript and video -> sampled JPEG frames without the capability.
  Key read from the named env var at call time, never stored or printed.
- Retry semantics, all API providers: 429/503/transport drop/invalid JSON -> same model up to 3 retries (2/5/12 s +
  jitter), then the next model in that provider's list; 404 -> next model; other status -> error. 300 s HTTP timeout.
  Once that list is exhausted the error is raised. Nothing hands the request to a different provider.

### C. Gates: pre-flight gates in 3 layers, all BEFORE render
- Layer 1 Static & metadata (~0.05 s): R07 every source URL in credits; R14 licence + watermark-free flag; R44 no duplicated
  text for one spoken line; L22 caption slot valid. Check master_timeline.json + credits at the very start; fail in the
  first second with a specific message.
- Layer 2 Math & DOM keyframes (~2 s): R56 from start/end delta (dx, dy, dzoom) in JSON; if too small auto-scale
  camera_intensity instead of failing. For HyperFrames segments: open index.html on a dynamic port, tl.seek(t) at 3 times
  per segment, getBoundingClientRect() to detect pin/card/text collisions and margins (R62/R63).
- Layer 3 Visual snapshot QA (~3 s): 1 screenshot per segment (from those seeks or 1 frame via ffmpeg); R38 pixel
  distribution in Rust; optional (--ai-qa) snapshots to the AI provider's QA models to verify nothing overlaps.
- 3-level severity (`KERNEL_GATE_IDS` / `LINT_GATE_IDS` in `gate_registry.py`):
  - **error**: the 8 kernel gates (`T02`, `M01`, `W01`, `L22`, `R38`, `A01`, `G01`, `M03`); any kernel fail stops `build`.
  - **fix**: auto-repaired findings (camera intensity, caption slot, layout adjustments), recorded in top-level `preflight_fixes`.
  - **lint**: the 51 non-kernel gates in `LINT_GATE_IDS` (style, layout `L01`-`L10`, caption contrast `R65`, and margin/camera/collision gates), which warn and publish with warnings unless listed in channel `[gates] require` or timeline `gates.require`.
  Timeline `gates.waive` and `hold: "read"` (segment or beat; P01/N01 findings) lower non-required lint findings to `info`.
  `build_report.json` `gates` always holds `{required, segment_intents, fails, warns, waived}` (plus `waivers` when active) alongside top-level `preflight_fixes`. `F01` (prop files) and `AS04` (readable on-screen time; default level `warn`, raised with `[gates.anti_slop.levels] AS04 = "fail"`) are technical-class gates outside `KERNEL_GATE_IDS` and `LINT_GATE_IDS`: they block a release build on `fail` unless lowered via `[gates] advisory` or `--mode draft`.
No M-patches, no meta-gates. Every gate must be proven with labelled good + bad cases in tests.

### D. Infra, template, delivery
1. One central Python package (pyproject.toml, pip install -e), CLI `engine`. Project folders hold DATA only:
   project_spec.json / master_timeline.json, credits.txt, assets/.
2. Dynamic ports (bind port 0). `engine preview --seg <n> --time <t>` runs outside the render queue, so helpers can
   preview anytime.
3. Camera presets with `intensity: float = 1.0` and `focus_target`; R56 shortfalls raise intensity in JSON, no code change.
4. Delivery: after segments render in /dev/shm, concat, burn captions.ass, normalise audio, name per project
   convention. Delivery goes to Google Drive; the Projects Manager bot arranges and handles the upload. Hypoverum Drive convention:
   per-project folder `NN - YYYY-MM-DD - Topik/05 Final`, files `NN - Topik - final.mp4[.partN]` (files over 100 MB are split into parts of at most 100,000,000 bytes, never re-encoded),
   `NN - credits.txt` (into `03 Aset`), `NN - JOIN.txt`.
   The JOIN text carries the join command.
   Once a project is finished, delete every asset downloaded for it; assets already on the box stay.

### D2. Director stage (`engine draft`)
- 3 storyboard concepts (beats from_word/to_word, goal, pattern, assets, on-screen text, camera, transition) before the
  timeline request; invented assets or facts rejected; scored fit/clarity/availability/render cost with opener and
  transition repetition penalties from the channel taste store (profiles/taste/<channel>.json); the best concept is
  given to the timeline request. Pattern library and prompt guidance in video_engine/director/ (no minimum-effect rules).
- `--review`: sampled critical frames + timeline -> review verdict; revise on blocker/major findings, at most 2 rounds;
  versions kept as draft/timeline.vN.json; a revision failing the gates is dropped.
- Works with every provider, including the agent handoff (one bundle per request).

### E. Channel profiles `profiles/`
- `profiles/default.toml` = engine values; `profiles/<channel>.toml` = one channel's overrides, selected by the
  optional `profile` field of master_timeline.json (absent = default).
- Merge: default < channel profile < project values (caption_style keys, audio_chain loudness fields).
- Channel-styleable: caption style and placement (incl. fixed caption y and a lowest caption bottom), loudness target, draft segment length and
  prompt rules, channel style gates (H01 hook, C01 hard cuts and shot changes, V01 visual type at shot changes, N01
  novelty, P01 camera motion, K01/K02 keyword return, D01/D02 duration, C02 no call-to-action, S01 silence on the final file, AS01-AS06 anti-slop rules with their per-rule levels).
  Absent gate table = gate off.
- Caption timing comes from the voiceover word timestamps for every profile; captions per 10 s is reported only.
- Caption bottom limit (`caption_style.max_bottom_y`, px at 1920 height; default profile: none): gate Z01 fails when
  any caption box bottom (centre y + 0.75 x font size + `caption_pad`; contains glyphs, descenders, outline and
  shadow) is below it; automatic caption placement never moves a caption below it. `caption_pad` = the ink reach
  below the glyphs beyond 24 px x font size / 84 (the band's slack below the glyphs scales with the font): soft
  shadow `shadow_dy` (default 1.5 x `shadow`) + `outline` + 2 + 2 x `shadow_blur`, hard shadow `outline` + `shadow`
  (0 for the default style). Geoverum: `fixed_y` 1366, `max_bottom_y` 1480 (caption box bottom 1402 px at Inter
  Bold 48, pad 0; rendered ink and shadow included, inside it); `auto_place` moves a caption only inside
  `place_band` 0.58-0.76 H.
- Caption soft shadow offset: `shadow_dx`, `shadow_dy` (px; absent = `shadow`, 1.5 x `shadow`). Geoverum captions:
  white Inter Bold 48 (the caption size measured on the channel's 48 reference Shorts: x-height 24 px and cap
  height 36 px at 1080x1920; 84, 92 and 104 px crowd the map), at most 4 words (`max_words = 4`, pure white `primary = "FFFFFF"` and `highlight_colour = "FFFFFF"`), no outline, soft shadow 0 3 px, `shadow_blur` 4 (libass
  Gaussian, about a CSS 8 px blur), black at 55 % (`shadow_alpha` 115), no backing box.
- No captions (no voiceover): every caption gate passes or skips on the empty list.
- Segment field `visual_type` (map / photo / video / diagram / archive / illustration; inferred when absent).
- Renderer theme (`[theme]`): values the HyperFrames template reads from the scene; part of the HyperFrames segment
  cache key. Default theme values reproduce the engine look.
- Motion tokens (`[motion]`, `profiles.MotionProfile`): named curves `ease_out` [0.23, 1.0, 0.32, 1.0], `ease_in_out`
  [0.77, 0.0, 0.175, 1.0] and `ease_drawer` [0.32, 0.72, 0.0, 1.0]; durations expressed in video frames at 30 fps
  (`micro_frames` 4, `overlay_frames` 7, `panel_frames` 12, `stagger_frames` 2); role eases `entrance` (`power2.out`),
  `move` (`power2.inOut`) and `exit` (`power2.in`), plus `entrance_scale` 0.96 and `overlay_fade_s` 0.20. The role names
  keep the existing look until a profile names an Emil curve. The HyperFrames scene carries `theme.motion` and the
  template uses the role names plus `overlay_s` and `panel_s` (for unboxed region labels, map-text fades,
  in-template overlay cards, unboxed cards, thermometer exits and flow legends; `micro_s` and `stagger_s` remain
  exported tokens), while native drawn overlay fades read `overlay_fade_s`. A default `[motion]` table is left
  out of the profile hash. Native camera easing is baked in the Rust `camera_windows` kernel (`ALGO_VERSION`), so the
  Python path carries no easing literal to rewire.
- HyperFrames `camera_path`: several camera keys in one map segment (legs tweened key to key, glide end speeds continuous
  across keys, optional key holds, drift after the last key);
  key move `glide` or `whip` (drift, then a fast in-out move with directional motion blur);
    R56 per leg;
    DOM probe also at every key frame;
    drafts may return `map_path` when the profile enables it.
- Camera pulses (`[camera]`, off by default): every HyperFrames drift holds evenly spaced cubic-bezier pulses away
  from the drift and back (push in or pull out about `pulse_anchor`, optional alternating pan); camera key `pulse`
  sets per-wait pulse sizes; beats scenes shrink pulses that break a layout rule and leave push-in headroom in the
  camera fit as far as the map edge keeps the content centred in Z2. P01 (`[gates.camera]`) grades holds and the
  moving fraction of the planned camera.
- Camera zoom limit (`[camera] max_zoom`, default 4, Geoverum 16): bounds camera keys, the layout fit and draft
  `map_path` zooms. Map frames past the 3000 px derivative's density draw deep source tiles, rastered at the width
  the deepest zoom needs up to the full source width (JPEG rows below the deepest tile are not decoded; past a
  640 Mpx decode budget the raster drops to half width). M02 grades the real source detail per screen px
  (warn < 1.2x, fail < 0.85x; a 2x pre-upscaled source counts half its width; the derivative and deep raster count
  only when cut below the on-screen width). Tilted scenes drop will-change/force3D on the camera so Chrome rasters at the camera
  scale instead of scale 1 under the perspective.
- Colour grade (`grade` on a segment or beat, profile `[grade]`): lift/gamma/gain, saturation, tint, .cube LUT and
  strength baked into one 3D LUT, applied by ffmpeg lut3d to the rendered segment per frame range, under the captions.
  G01 checks every LUT file.
- Camera tilt (camera_path key `tilt`, 0-60 deg): #cam and the map props layer turn about the frame centre under
  perspective(H) rotateX(tilt); camera3d.py is the Python mirror (props boxes, deep tiles, M02 x bottom-edge
  magnification, P01 tilt speed). M03 fails frames whose corners show no map.
- Parallax layers (gsap_scene_config.layers): repeating images over the map at depth -0.9..2, grid origin and tile
  size from the camera per frame (parallax.py mirrors hf/parallax.js); T02 checks the files.
- Globe (gsap_scene_config.globe): three.js unit sphere textured with a whole-world 2:1 map_asset (8192 x 4096
  derivative) and level-of-detail patches from the NASA 86400 x 43200 tiles or the map; camera keys {lat, lng,
  altitude, tilt, hold}, C1 monotone cubic camera with optional key holds and profile start/end speeds, drift after the
  last hold; globe.py supplies per-frame views to hf/globe.js (projection, pins).
  T02 checks the tile set, M04 the texel density, R56 and P01 measure the globe legs; the probe browser runs
  SwiftShader WebGL for globe scenes.
- Props (`props` on a map scene or a beat): PNG or WebP images with alpha, or owned glTF / GLB / OBJ models rendered by `models.py` (three.js, pinned in vendor/npm, on SwiftShader in the vendored Chrome) to a deterministic RGBA sprite at a given yaw, pitch and fov, or to a looping turntable sprite sheet, cached by content hash (kind `models`), map-anchored (map point or lat/lng, moves and scales with the camera; `depth` 0-2 adds scale (s / s_in)^(1+depth) and parallax) or screen-anchored (frame px), with fade, pop or slide enter and exit, bob or rotate idle, z order and drop shadow.
  Map props may travel a `path` of normalised map points from `t_in` over `path_s` (default to `t_out`): glide eased arc length (`props.path_at`, mirrored by `pathAt` in hf/props.js);
    `heading` adds the segment direction to `rotation`.
  `hf/props.js` positions them every frame from the shared camera legs; `props.state` is the same math in Python (DOM within 1 px).
  Gates F01 alpha, F02 resolution (fail < 1.0x, warn < 1.5x of the largest on-screen width), F03 clear of texts, pins, cards and captions, F04 safe area;
    R07 credits, T02 timing;
    props join the caption avoid-list for their frames.
- Counter-scale under the camera: pin labels and heads, area labels, hand notes, region edges and casings, and route
  lines, casings, dashes and arrowheads scale by v / (camera scale of the same frame) on every frame of every camera
  leg (drift, glide, whip, pulse), so the camera adds nothing to their on-screen size (DOM size within 1 % of plan
  through a 1 -> 4 zoom). On-screen size = planned size x the element's own designed scale: the fly-through entry
  and exit scale applies to the label inside the counter-scaled box, and the boundary transitions (zoom whip, page
  turn) act on the rendered frames, so both keep their designed motion.
- Transitions (`[transitions]`, off by default): crossfade (`fade_s`, centred on the boundary; before it the
  incoming first frame is held, after it the outgoing last frame), zoom whip, zoom through (a larger push-in for
  scene changes with an optional white flash) or page turn per segment boundary, by visual type; drawn
  as lossless clips from the segment files (`video_engine/transitions.py`) and overlaid frame-exactly in the final
  mux under captions and watermark; without transitions the mux command is unchanged. C01 grades hard cuts (warn
  levels) and shot changes (fail levels).
  `same_type` `{visual type: kind}` takes precedence when both sides of a boundary share that type.
  `page_turn = false` rejects any page_turn in the profile; `map_morph = false` makes a map `morph` a T02 fail
  (`geoverum`: hard cuts across visual types, 0.35 s `state` dissolve on same-map boundaries, 0.5 s explicit crossfades, no page turns, no paper morphs).
- Render quality (`[render]`, every key optional; absent = engine defaults: HyperFrames `standard` + CRF 14,
  x264 crf 16 veryfast for intermediates, crf 18 medium for the final composite, map JPEG q90): `hf_quality`
  (draft | standard | high), `hf_crf`, `segment_crf` / `segment_preset` / `segment_tune`, `final_crf` /
  `final_preset` / `final_tune` (`none` = no -tune) / `final_maxrate_kbps` (VBV cap, bufsize 2x),
  `final_bit_depth` (8 | 10; 10 = final composite in yuv420p10le, x264 High 10, which avoids encoder banding in
  gradients; intermediates stay 8-bit; needs an ffmpeg whose libx264 has 10-bit, else the composite stage fails with
  that message), `map_jpeg_quality`. Every value is part of the cache key of the stage it reaches.
- HyperFrames `regions`: entity highlights; one colour per entity from profile `[theme.entities]` then
  `theme.palette`; E01 checks the painted region fill colours in the DOM probe. Region `style` (default
  `theme.region_style`; beats scenes default to `tint`): `tint` = fill + white or glow edge; `block` = a raised slab in
  `entities.block_colour` (entity colour, OKLCH L clamped to `block_l_min..block_l_max`, chroma >= `block_chroma`) with
  an extruded side `block_side_dl` darker, a drop shadow, an inner bevel and a neon edge (`edge_colour`, `dashed`)
  drawn on over `draw_s`. `changes` `[{at_frame, entity}]`: the region takes the next owner's colours (block: face,
  side, bevel; tint: fill, edge, `[print]` edge ink, `[legibility]` casing; label box) over `block_change_s`; each
  change is a `region` SFX and style event. `label` (`label_at` map point,
  default the polygon centroid; `label_at_frame`): a solid box label in `entities.box_colour` (block colour darkened
  until white text reaches APCA `label_box_lc`), counter-scaled, with an elastic pop. Globe scenes accept regions
  (polygons in whole-world normalised x, y; projected every frame by hf/globe.js, hidden while any vertex faces away).
- HyperFrames `borders` `[{points, at_frame, draw_s, colour, dashed, closed}]` (flat maps only): neon lines (casing
  from `legibility.line_casing`, glow `block_glow_px`) drawn on along their length; width, dash and gap
  (`border_px`, `border_dash_px`, `border_gap_px`) keep their on-screen size. T02 checks their frames; R64 line rows.
- HyperFrames `morph` `{asset, at_frame, s, back_at_frame, sepia, paper, grain}` (flat maps only): a second map of the
  same extent and aspect ratio (within 0.5 %) crossfaded over the base map (satellite <-> old paper) with its own
  deep tiles, sepia tone, the vendor paper texture multiplied at `paper` and grain at `grain`; at_frame 0 = shown from
  the first frame. T02 checks its frames and file.
- `theme.label_style` `box` (scenes without a layout): pin labels, region labels, cards, title and figures sit in solid
  boxes (`label_box_colour` or the entity's box colour) with fixed white text, `label_box_px`, `label_box_radius_px`,
  elastic pop `label_pop_s`; they leave the adaptive ink set, and R64 checks each visible box (`data-box`) text against
  its box at the box target instead.
  `year_box_colour` (with `year_box_ink`, `year_box_px`, optional `year_box_texture` from vendor/textures, multiplied
  over the box): year figures on that box instead of the figure box; R64 checks the year ink against the box colour
  times the texture's darkest factor (0.5th percentile luminance, `data-box-dim`).
- HyperFrames `routes`: dashed self-drawing movement lines with an arrowhead riding the tip; style from `[theme]`.
- HyperFrames `title` (entity colour + glow) and `figures` (years in bold serif, numbers with an elastic bounce
  or a counter); counters emit `counter_tick` events into build_report.json `sfx_events`.
- Pin themes: `dot` (default look) or `teardrop` (bounce drop-in, shadow, label beside the head; per-pin `label_side`);
  colour and label font from `[theme]`; theme fonts ship in `vendor/fonts` with their licences.
- Theme text paint: outlined labels and titles (theme outline + drop shadow, CSS variables), soft region glow, title
  tint toward white. Profiles without `[legibility]` use it; `[legibility]` replaces it (below).
- E01: every region fill painted in the DOM probe equals its entity colour; two entities sharing a colour = warn.
  Text colour is not part of E01.
- SFX cue model (`video_engine/sfx.py`, `timeline.SfxCue`): profile `[sound]` maps render events (pin, region,
  route, whip, label, title, figure, counter_tick) to SFX kinds; one cue per event:
  `{time_s >= 0, kind, gain_db -60..0, asset_id, event, frame, trim_s >= 0, max_s 0-10}`, where `kind` is one of
  pop, tick, whoosh, click, beep, counter_tick, impact, riser, rumble and `asset_id` is the source asset id of the file the
  sound resolver gives for that kind (provenance `id`).
- Music brief (`audio_chain.music_brief`, `timeline.MusicBrief`): `{mood: str, energy: low|medium|high,
  bpm_min: int, bpm_max: int, duration_s: float}`.
- Mixer (`video_engine/mixer.py`, profile `[sound]`): music gain + fades + keyframed duck to `duck_db` under the
  voiceover word spans, SFX cues at their gain, summed (no normalisation) before the two-pass loudnorm and limiter;
  every input is cut to exactly duration x 48000 samples and the sum is cut to that count before loudnorm and after it;
  the final file meets the profile loudness target (default -14.0 LUFS +/- 0.2, true peak <= -1.5 dBTP).
  build_report `sound` lists the music and SFX provenance and every cue.
- Sound resolver adapter (`mixer._sound_cache`, `mixer.resolve_sfx`, `mixer.resolve_music`, called by
  `mixer.plan`): with the `video_engine.sound` package installed it calls `video_engine.sound.cache.resolve(kind,
  profile)` per cue kind and `resolve_music(brief.model_dump(), profile)`, each returning `ResolvedSound` with
  `.path` and `.provenance` `{source, id, title, artist, credit}`; `resolve` uses a generated local-test tone only when
  `VIDEO_ENGINE_LOCAL_TONES=1` (or `allow_generated=True`) and otherwise raises for every kind, `resolve_music` returns
  None when no licensed file is cached (None fails the build). Without the package it uses local
  test audio marked provenance source `local-test`: generated tones per SFX kind and `audio_chain.bgm_path` as music.
- Sound sourcing (`video_engine/sound/`, docs/SOUND.md): content-addressed cache `<cache>/sound/` with provenance
  sidecars; the build calls `sound.request.ensure_sounds` before `mixer.plan`. Missing files (with `[audio]
  require_source`: every SFX kind and the music brief not cached from that source; without it, when generated tones
  are off: every SFX kind with no cached epidemic file, plus an uncached music brief; with tones allowed: an uncached
  music brief only) = request bundle `<project>/out/sound_request/` (`request.json`, `request.md`, `schema.json`, `files/`),
  exit code 3; the agent answers through the Epidemic Sound connector and writes `response.json`; `engine build
  --resume` validates and imports it, removes the bundle and continues. After the plan, proxy and delivery append one credit
  line per Epidemic Sound file to `credits.txt` under `Audio (Epidemic Sound)`:
  `- <id> — <artist> — "<title>" — https://www.epidemicsound.com — Epidemic Sound licence`.
- A02 (delivery, `[audio] require_source`): when set, checks that every music and SFX file of the final mix has the
  matching provenance `source` (warns by default unless `A02` is in `gates.require`). No finding when `require_source` is unset (`geoverum` leaves it unset by default).
- Profile `[watermark]`: one-line text mark top right inside the safe area, drawn to a PNG and overlaid by the final
  mux (no segment re-render); B01 checks it against element boxes and captions before rendering.
- Anti-AI-slop gates (`[gates.anti_slop]`, `video_engine/gates/anti_slop.py`): AS01 flags a drawn `lower_third` or
  `text_card` left accent strip; AS02 flags em-dash saturation, listed buzzwords, >= 3 "No ..." rebuttal endings and
  "theater"; AS03 flags an ease-in `[motion] entrance` or `[layout.fly] ease_in`, `entrance_scale` < 0.9 and bounce
  figure entrances; AS04 flags drawn overlay text on screen shorter than max(`min_read_s`, characters / `chars_per_s`)
  and is technical-class; AS05 scans project composition sources and the built HyperFrames HTML for gradient text,
  `backdrop-filter` glass, side borders wider than `side_strip_px`, repeating stripes, zero-offset glow,
  organic/high-vertex clip-path and kicker/eyebrow classes; AS06 flags literal source colours in forbidden AI-tell hue
  ranges (violet 260-310, cyan 170-197) that the profile does not declare. Engine-generated template effects and
  markup (`MO.*` motion tokens, whip blur, built-in `lower_third` markup) are allowlisted, so only project content is
  flagged. Taste rules default to warn; `[gates.anti_slop.levels]` raises a rule, and for style-class ids
  `[gates] require` keeps a raised fail blocking. Geoverum keeps every anti-slop rule at warn (`AS01`-`AS06`) and sets `[gates] require = []` (no lint gate blocks by default).
  Declared brand palettes are exemptions.
- Global safety and never-advisory gates (identical for every profile; a profile may only tighten true peak and loudness tolerance, and
  no `[gates] advisory` list can include them): T02, R07, R14, A01, A02, W01, R38, R56px, M01, M03, G01, L22
  (`profiles.NEVER_ADVISORY`) and post-render QA. At runtime, the 8 kernel gates (`KERNEL_GATE_IDS`: T02, M01, W01, L22, R38, A01, G01, M03) always block on fail, while R07, R14, R56px and A02 are in `LINT_GATE_IDS` (warn by default unless listed in `require`).
- build_report.json records the profile name and the BLAKE3 hash of the merged profile.
- Locked build manifest out/build_manifest.json (before render): build_id, sha256 of timeline, inputs, fonts and sounds
  (portable paths), profile hash, toolchain pins, seeds, worker budget, stage cache keys. Sounds stay locked until
  `engine build --relock`. Delivery refuses a final file whose build_id or sha256 does not match the last build.
- Final pass split into cached composite (video), master audio and stream-copy mux; stage keys are the ffmpeg command
  with inputs replaced by their sha256. Same inputs -> same bytes.
- Worker budget `[resources] workers` / VIDEO_ENGINE_WORKERS (auto from CPU and RAM) shared by every stage; per-stage
  wall, CPU and peak RSS in build_report.json `resources`. HyperFrames segments render up to 2 at once when the budget
  holds both next to the native renders; the output is the same bytes in the same order as one at a time.
- DOM probe results (rects, screenshots) are cached (cache kind `dom`) under the content of the built segment dir, the
  probe times, viewport and mode, the toolchain identity and the HyperFrames engine source; gate verdicts are computed
  on every run. build_report.json `preflight_subtimings.layer2_dom`: seconds of build, browser_load, r63_rects,
  snapshots, bg_screenshots, glyph_masks, r64_numpy, plus probes and cache_hits.
- Publishing: the build muxes into a staging dir inside out/ and runs every gate on that file; only a build with no kernel fail and no stage error replaces out/final.mp4 and out/build_report.json (os.replace). Lint findings warn and publish with warnings.
  A failed build keeps both byte-identical, writes out/build_report.failed.json, and fails the API (BuildFailed, or ok=False with raise_on_fail=False) and the CLI (exit 1).
  `build_report.json` always contains the full `{required, segment_intents, fails, warns, waived}` gate summary and top-level `preflight_fixes`.

### F. Adaptive legibility (profile `[legibility]`; `geoverum`)
- Scope: teardrop pin labels, the title, years and counters, beats area labels (`video_engine/legibility.py`), and
  the lines they sit with: pin heads, routes, region outlines, pin leader lines. Absent section = theme
  text paint, no adaptive ink, R64 off; output of such profiles does not depend on this section. Captions are not
  adaptive elements.
- Units: Y = APCA-W3 0.1.9 screen luminance; Lc = APCA-W3 0.1.9 contrast (dark text positive); WCAG = WCAG 2 ratio,
  logged as a backup; OKLCH for colour construction.
- Element interval: from the end of its entrance (labels +0.7 s, title +0.5 s, bounce figures +0.9 s, counters
  +0.2 s after `at_frame`) to the last segment frame. Beats scenes (G): the element's longest layout span, from
  its entry end + 0.1 s to one frame before its exit (or the last segment frame); an element without a span is not
  sampled. `samples` frame times spread evenly over it.
- Measurement: the segment is built without inks and probed at every sample time twice: text hidden (background) and
  the adaptive text alone as a black glyph mask, restricted to the element's own box + 0.1 em. Background pixels =
  glyph mask dilated by the halo width (labels, years and counters at `measure_scale` resolution; titles at full
  resolution). Adjacent pixels = full-resolution glyph mask dilated by `adjacent_px`, minus the mask. The halo is
  composited over both at its painted coverage (stroke, blur, 0 in counters; `legibility.halo_coverage`).
- Hard case: dark ink is judged on the `dark_percentile` Y of the effective background (map, then vignette, then
  halo); light text on its `light_percentile`. Adjacent check: Lc against the `adjacent_percentile` darkest (dark
  ink) / lightest (light text) adjacent pixel >= `lc_floor`.
- Targets: title `lc_title` (aim `lc_title_aim`), years and counters `lc_figure`, labels `lc_label`, area labels
  `lc_area`, all >= `lc_floor`. WCAG backup: labels `wcag_label`, title / years / counters / area labels `wcag_title`.
- Polarity: dark ink unless the median over samples of the background p50 Y < `light_p50_max`. White text on a paper
  background is never chosen.
- Dark ink: oklch(`ink_l` `ink_c` `ink_hue`). Entity-coloured text (title, figures with an entity) keeps the entity
  hue and chroma with L <= `entity_text_l_max`; colours in `never_text` become `text_fallback`. Area labels take their
  region's entity colour the same way. Water area labels (`AreaLabel.water`: sea, strait, river) take
  oklch(`water_ink_l` `water_ink_c` `water_ink_hue`) (geoverum #033D57) and are set in italic.
- Light text: oklch(`light_l` `light_c` `light_hue`), glow `glow_px` at `glow_alpha` and shadow (0, `shadow_dy_px`,
  `shadow_blur_px`, `shadow_colour` at `shadow_alpha`). Dark ink has no glow and no shadow.
- Halo (dark ink): a band painted behind the glyphs (the element's `::after`, colour `--halo-c`, masked by a mask
  image computed in the page): the glyphs stroked with round joins at width 2 x w plus their fill, Gaussian blur
  `halo_blur_ratio` x w, and alpha 0 in every enclosed counter (glyph background pixels not 4-connected to the
  mask border). w = `label_halo_width_em` (labels) or `halo_width_em` (title, figures, area labels) x font size,
  both within `halo_width_min_em`-`halo_width_max_em`; `halo_blur_ratio` <= `halo_blur_max_ratio` (<= 0.5). The
  mask draws the glyphs the DOM shows, including `font-variant-numeric` digit forms. No text stroke. The element
  carries `data-halo` {w, blur, em, px, holes}. Colour = pooled background p50 in OKLCH with L clamped to
  `halo_l_min`-`halo_l_max`, C x `halo_c_scale`, same hue (never white); alpha `halo_alpha`-`halo_alpha_max`.
- Escalation (dark ink), first step passing every sample wins: 0 halo `halo_alpha`; 1 halo `halo_alpha_max`; 2 + a
  radial paper vignette behind the text in the halo colour (ellipse `vignette_scale` x the text box, outer
  `vignette_feather` x box height feathered, alpha `vignette_alpha_min` to `vignette_alpha_max` in `vignette_steps`
  steps; never a box); 3 ink L - `ink_l_step` per step down to `ink_l_min`. Then the other polarity. None passing =
  stability schedule.
- `ladder_order` "ink_first" (geoverum, printed ink): 0 the ink; 1 ink L - `ink_l_step` per step down to `ink_l_min`;
  2 the darkest ink on the paper vignette (`paper_colour`, `vignette_alpha_min`-`vignette_alpha_max`). No glyph
  halo (`halo_alpha` = `halo_alpha_max` = 0). `polarity_fallback` false: the other polarity is never tried; an
  element nothing passes is flagged (R64 warn).
- Stability: one polarity and one style per element per interval. Schedule (fallback only): a polarity switch needs a
  run of samples needing the other polarity spanning >= `hold_frames` frames (first to last sample), starts >=
  `hold_frames` after the previous switch, and its crossfade midpoint passes target - `lc_fail_margin` on the switch
  sample against the final styles of both segments; a switch failing that is dropped. Midpoint: inks mixed 50 %, halo
  and vignette alphas moved by 1 - exp(-`crossfade_ms` / 2 / `ema_tau_ms`) from the old to the new value. Painted:
  colour crossfades linearly over `crossfade_ms` from the switch; halo, vignette and glow alphas follow an EMA with
  time constant `ema_tau_ms` from the switch; the halo colour is one per element. A segment with no passing
  style takes its last ladder step and is flagged.
- Painted state: every adaptive element carries `data-ink-state` (polarity, ink, halo, alphas, step, flag) at each
  seek; inks are part of the scene, and the segment cache key covers `legibility.py`.
- Overlays: basemap saturation x `basemap_chroma` from the first region or route frame; region fill alpha
  `entity_fill_alpha` and a `entity_outline_px` outline in the full entity colour, no glow; teardrop pins a
  `pin_outline_px` outline in `paper_colour`; routes a dark casing `route_casing_px` per side in
  `route_casing_colour`; region outlines a casing `region_casing_px` per side under the outline in `paper_colour` or
  `route_casing_colour`, whichever has the larger |Lc| against the outline colour; pin leader lines in the label's
  painted ink with a `leader_casing_px` casing in its halo colour (dark ink) or `route_casing_colour` (light text).
  Every gated line carries `data-line` (id) and `data-line-ink` (its painted colours). Labels and figures sit outside
  the camera whip blur (no motion blur on text).
- Palette (geoverum, 17 muted OKLCH colours, L 0.40-0.82, C <= 0.12, pairwise OKLab distance >= 0.07): teal #136868,
  umber #5F4025, wine #74364B, mauve #796394, jade #75A58B, olive #6A662B, plum #B56A95, indigo #4B4D8B, ochre #C99D4E
  (never text), moss #6B8B5A, steel #437AA6, brick #9C443B, sienna #A7693C, taupe #A19073, sky #549CC0, lagoon
  #70B8C6, mist #AEC8DC; teal and indigo are not adjacent in palette order. `[theme.entities]` pins Indonesia brick
  (`#9C443B`), Sekutu indigo, Belanda ochre, Jepang moss, Caspian Sea steel, Russia brick, Kazakhstan sienna,
  Turkmenistan ochre, Iran moss, Azerbaijan taupe, Black Sea sky, Mediterranean lagoon, Paratethys mist, Atlantic
  indigo, plus all 242 Natural Earth countries/territories (`NAME_ID`, `NAME`, `NAME_LONG`, and common Indonesian
  aliases) across the 7 non-marine colours with zero land or key maritime border conflicts; the six unpinned colours
  (teal, umber, wine, mauve, jade, olive, in that order) stay free for any other entity.
- Camera drift: a constant ln zoom rate `camera_drift_per_s` (geoverum 0.06 ln / s, above the P01 `still_zoom_ln_s`
  0.05) for the whole wait, zooming about `pulse_anchor` (frame centre without pulses). Camera key `drift_max` (ln
  zoom, 0.01-2) bounds a wait's drift: it turns at 0 and the bound. Camera key `fit` (alias `keep_in_frame`, pin
  labels or indexes) scales that wait's push-in and pulses down so each named pin stays inside the safe area, inset
  by 24 px at the reference canvas, for every frame of the wait. The layout planner sets `drift_max` on each wait whose
  drift frames break a per-frame layout rule that the camera held on the wait's first view does not (0.7 of the
  offending offset, floor 0.03). Whip and pulse legs carry the drift rate at both ends and never reverse inside a leg.
- R64 (pre-flight DOM layer, every probed sample time in each element's interval at opacity >= 0.9): measures the
  painted text colour, the painted halo (`data-halo` width and blur, `::after` colour and alpha) and the
  `data-ink-state` vignette alpha over the background probe.
  Fail: Lc < target - `lc_fail_margin` or adjacent Lc < `lc_floor`.
  Warn: WCAG below its backup, or a flagged
  element. Pass reports the lowest Lc relative to target. R63 overlap and margin checks keep their own probe times.
  Dark ink also fails on a painted halo (`data-halo`, `::after` colour) with width outside
  `halo_width_min_em`-`halo_width_max_em`, blur > `halo_blur_max_ratio` x width or alpha outside
  `halo_alpha`-`halo_alpha_max`, and on counters: the enclosed loops of its glyph mask, eroded 1 px, components of
  >= `counter_min_px`; a counter pixel is open when the painted frame differs from the text-hidden frame (with the
  paper vignettes of the adaptive elements on screen composited) by <= `counter_diff_max` per channel; fail when
  less than `counter_open_min` of them are open.
- R64 lines (each `data-line` element at opacity >= 0.9 at each probed time): its painted pixels = pixels of a
  lines-only screenshot on white differing from white by > `line_mask_diff` inside its box + `line_ring_px`; ring =
  those pixels dilated by `line_ring_px` minus every line pixel, read from a lines-hidden screenshot. Per ring pixel
  the largest |Lc| of the line's painted colours; Lc = its `line_percentile`. Fail below `lc_line_fail`, warn below
  `lc_line`.
- `[legibility]` keys and ranges are validated by `profiles.Legibility`; low/high pairs must be ordered, defaults
  included. Keys with defaults (may be omitted): `lc_area` 60, `water_ink_l` 0.34, `water_ink_c` 0.07, `water_ink_hue`
  235, `halo_width_min_em` 0.06, `halo_width_max_em` 0.10, `halo_blur_ratio` 0.25, `halo_blur_max_ratio` 0.5,
  `counter_min_px` 6, `counter_diff_max` 16, `counter_open_min` 0.9, `region_casing_px` 1.0, `leader_casing_px` 1.75,
  `lc_line` 30, `lc_line_fail` 25, `line_ring_px` 3, `line_percentile` 10, `line_mask_diff` 24, `never_text` [].
  Every other key is required.

### F2. Printed typography (profile `[print]`; `geoverum`; `video_engine/printstyle.py`)
- Surface per map segment: paper when the map image p50 APCA Y >= `paper_min_y`, else dark imagery. The surface fixes
  the polarity of every adaptive text (paper: dark ink; dark: light text) and the roles below.
- Paper: map text is ink printed into the map: `ink` (pin labels, years), entity ink (titles; F), multiply, no halo,
  no glow, no box. Dark imagery: white sans with a soft shadow; glow only in the entity colour.
- Map grade (paper): saturation `grade_saturate`, contrast `grade_contrast`, frame vignette `vignette_alpha`,
  `paper_texture` multiplied at `paper_alpha`, `grain` multiplied at `grain_alpha` (files in `vendor/textures`).
- Regions: fill at `entity_fill_alpha` and a solid edge in the entity colour at OKLCH L `region_edge_l` (paper) or
  `region_edge_l_dark` (dark imagery), alpha `region_edge_alpha`, both multiplied on paper (`region_multiply`).
- Entity title: inside its region (layout G), EB Garamond `title_weight` caps on paper (Inter Bold on dark imagery),
  `title_px` shrinking to `title_px_min` until its box width <= `title_width_max` x the region's screen box width,
  |rotation| <= `title_rot_max`, tracking `title_tracking_in` easing to `title_tracking` over `title_in_s`, paper
  emboss (`emboss_colour` copy `emboss_dy` px under the ink at `emboss_alpha`). None fits: hidden, L02 warn.
- Year alone: Playfair Display centred at (`year_cx`, `year_cy`), ink width `year_width` x frame width within
  `year_px_min`-`year_px_max`; optional time under it in Cinzel `time_px` at `time_alpha`.
- Heading (beat `heading`, native overlay kind `heading` with `on` light | photo): Cinzel caps `heading_px`,
  baseline `heading_baseline_y`. `heading_ornaments` (default false; a scene `heading_ornaments` or an overlay
  `ornaments` overrides it) adds two `rule_len` rules with diamonds. Plain is the caps alone. `heading_ink` on
  light, `heading_light` with a shadow on photos. A heading and the title never share a beat.
- Pin labels: above the pin on a vertical leader (`leader_gap`, `leader_len`, `leader_gap`), EB Garamond Italic
  `label_px` on paper, Inter Bold `label_dark_px` white on dark imagery. The camera fit (G) frames the label above
  the head (`label_lift` = 2 x `leader_gap` + `leader_len` + 1.2 x label px).
- Hand notes (beat `notes`, at most 1 per beat): Kalam `note_px` in `note_ink` (`note_light` on dark imagery),
  rotated -4 to -8 deg, optional arrow to `arrow_to`, written on over `note_write_s`. Notes and captions are not part
  of the L04 hero ratio.
- T01: distinct typefaces per probed frame <= `max_typefaces` (DOM font families on map segments, native overlays;
  caption and watermark excluded). B01: every element box keeps `watermark_clear_px` from the watermark.
- Fonts (`vendor/fonts`, OFL): EB Garamond, EB Garamond Italic, Cinzel, Kalam Bold, Playfair Display, Inter.
  Textures (`vendor/textures`, CC0): paper, grain. Each file has a row in the folder README.
- Camera fit zoom is capped at the profile camera limit (`[camera] max_zoom`: default 4, `geoverum` 16; schema ceiling `timeline.ZOOM_LIMIT` = 64; `timeline.ZOOM_MAX` = 4 is the default-profile limit).

### G. Beats layout (profile `[layout]`; `geoverum`)
- Scope: HyperFrames map scenes with `gsap_scene_config.beats` (`video_engine/layout.py`). Positions, sizes,
  zones, sequencing and camera framing only; colour, halo and contrast stay with F. A profile without `[layout]`
  has no layout, and its profile hash and output do not depend on this section. A beats scene under such a profile
  fails L06.
- Units: px at the reference canvas (1080x1920 portrait, 1920x1080 landscape; docs/PROFILES.md, Orientation), scaled to the output size; map px = map image at cover scale; times in frames of the
  timeline fps; `at_frame` of a beat is relative to the segment start.
- Grid (`[layout.grid]`): `columns` columns of `col_w` with `gutter` from `x0` (edges 64, 208, 352, 496, 640, 784);
  every top and gap is a multiple of `unit`.
- Zones (`[layout.zones]`): `safe` for every text except the caption; Z1 `z1` holds the title or the data card,
  left-aligned on the first column edge; Z2 `z2` holds the active region, pins, map labels and routes; the camera
  fits the beat content (active region, its routes, its pin heads) into Z2 minus `z2_pad`, clamped so the map
  covers the frame (a beat that cannot fit is logged and reported as an L06 warn); Z3 = the caption band (Z01).
- Sizes (`[layout.sizes]`): title, year, time, pin label (`pin_label_min` floor), area label (uppercase, tracking
  `area_tracking` em). Text boxes are font-metric boxes of the loaded fonts plus the painted halo, glow and shadow.
- Data card: year and time on one alphabetic baseline in Z1; the time ink starts `card_gap` right of the year ink.
- Pin labels: candidates `order` (TR, BR, TL, BL, TC, BC, R, L) at `offset` from the head; a candidate is valid when
  its box + `edge_clear` crosses no region edge, its box + `text_clear` touches no other text, head or leader, and
  it lies in Z2. Cost = 10 x rank + distance / 16 + 50 x (not the previous position) + 5 x map detail under the box;
  lowest valid wins, the previous position is tried first. No valid candidate: a 2 px leader of at most
  `leader_max`; still none: the label is hidden and logged (L02 warn).
- Area labels: inside the polygon, horizontal first, then along the polygon's long axis, inset `area_inset`; else
  outside with a leader; `group_gap` from other texts and heads, max(`route_clear`, `group_gap`) from routes.
- Routes: start on their first map point; a route with `to_pin` ends `route_pin_gap` (euclidean) short of that
  pin's head; never closer than `route_clear` to text.
- Sequence per beat: exits (texts, routes, region dims) staggered from `at_frame`, each `exit_s`; then the camera
  `settle` key (`settle_s`); then entries in the order region, dim, head, label, route, area, card, title, staggered
  `stagger_s` (min `stagger_min_s`), each `enter_s`; at most `budget.animating` tweens at once. One active region at
  full fill; every other region dims to fill alpha `dim_alpha`.
- Stability: label positions are planned for every frame. A hard violation moves the label at once with a
  `move_fade_s` fade; a soft move needs a cost gain >= `move_gain` held `move_hold_s`, at most `moves_per_shot` per
  shot, never two within `move_gap_s`.
- Fly-through (`[layout.fly]`, optional): the title, figures, pin labels and area labels enter by scaling from
  `title_in` / `figure_in` / `label_in` to 1 over the span's enter time (`ease_in`) with opacity over
  `<kind>_opacity_in_s` (span enter time when unset), and exit by scaling to `out` with opacity to 0 over the exit
  time (`ease_out`). Absent = opacity fades. Geoverum: 1.0 / 1.1 / 1.15 -> 1 in 0.33 s `power3.out` (the title enters by tracking-in, theme
  `title_entrance`), title opacity 0.15 s, figure opacity 0.40 s, exit 1 -> 1.15 in 0.33 s `power2.out`. R63, caption clearance and B01 skip a text while it flies
  (inside an entry or an exit of any of its spans).
- Output: the planner writes `camera_path`, element `at_frame`s and the scene `layout` block (spans, positions,
  label tracks, area labels, region steps); the template applies it after the main timeline.
- Gates L01-L10 (pre-flight, after layer 2 place): every segment frame against the plan; when DOM is on, the built
  segment is probed at each beat's settled frame and mid-leg and every visible managed box must match the plan
  within 3 px (L01).

## Defaults
- Caption: no backing box; white bold text, dark outline, soft shadow; 1-2 words per caption; active-word highlight optional; all style fields configurable.
- Performance target: total processing 2-5 min per project on 8 vCPU / 15 GB RAM / no GPU.

## Data contract: master_timeline.json (example)
{
  "project_id": "surabaya_doc_v2",
  "profile": "geoverum",
  "settings": {"width":1080,"height":1920,"fps":30,"work_dir_shm":"/dev/shm/video_engine/surabaya_jobs"},
  "credits_manifest": [
    {"asset_id":"map_sby_01","source_url":"https://example.com/source1","license":"CC-BY-4.0","watermark_free":true},
    {"asset_id":"archive_photo_02","source_url":"https://example.com/source2","license":"Public Domain","watermark_free":true}],
  "audio_chain": {"voiceover_path":"assets/vo_master.wav","bgm_path":"assets/bgm_ambient.mp3","target_lufs":-14.0,"auto_ducking":true},
  "segments": [
    {"seg_index":0,"engine_type":"NATIVE_RUST_FFMPEG","start_frame":0,"end_frame":240,"source_video":"assets/raw_intro.mp4",
     "source_in_frame":120,"camera_move":{"preset":"ken_burns_zoom_in","intensity":1.35,"focus_box":[200,300,800,700]},"overlays":[]},
    {"seg_index":1,"engine_type":"HYPERFRAMES_GSAP","start_frame":240,"end_frame":480,
     "gsap_scene_config":{"map_asset":"assets/map_sby_01.jpg","map_asset_id":"map_sby_01",
       "map_bounds":{"north":-7.15,"south":-7.35,"west":112.60,"east":112.85},
       "pins":[{"label":"Tunjungan","lat":-7.2575,"lng":112.7521}],"cards":[{"title":"Pusat Kota","asset_ref":"archive_photo_02"}],
       "camera_preset":"pan_northeast","camera_intensity":1.5}}],
  "captions": [
    {"word":"Surabaya","start_frame":5,"end_frame":18,"highlight":true},
    {"word":" berkembang","start_frame":18,"end_frame":32,"highlight":false},
    {"word":" pesat","start_frame":32,"end_frame":48,"highlight":true}]
}
