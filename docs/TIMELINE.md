# Timeline and captions

The rules of `master_timeline.json` and of captions.

**What this document answers**

- Which fields a timeline has, how time is counted, and what each engine type and scene field means.
- How project gate requests (`gates.require`, `gates.waive`, `gates.waive_with_reason`, `hold`) are written.
- How caption words, their style and their placement work.

- `profile` (optional): channel profile name (`profiles/<profile>.toml`).
  Absent = `default`.
  An unknown name fails schema validation and lists the known profiles.
- `gates` (optional):
  - `require`: style gate ids added to the channel's required set; a project can add, never remove.
  - `waive`: style gate ids whose findings become info; a waive of a required or technical gate is ignored with a GW01 warn.
  - `waive_with_reason`: a list of `{"gate": "L01", "reason": "..."}` for the technical gates the registry marks waivable (`L01`, `L06`, `O01`).
    The finding becomes info, and the reason (at least 10 characters) is kept in `build_report.json` under `gates.waivers` and in the waived row.
    Every other gate is rejected when the timeline loads; a gate may be listed once.
- `hold: "read"` (optional, on a segment or a HyperFrames beat): the viewer reads there;
    P01 hold and N01 gap findings starting inside it become info unless the gate is required, and no camera pulse is placed on that beat's wait.
- Time unit is the integer frame. `start_frame` inclusive, `end_frame` exclusive. Seconds are converted with `sec_to_frame` (rounds half away from zero, clamps negatives to 0; see [RUST.md, Frame and time rules](RUST.md#frame-and-time-rules)).
- Segments are contiguous from frame 0 (gate T02).
- `engine_type`:
  - `NATIVE_RUST_FFMPEG`: exactly one of `source_image` / `source_video`.
    Stills require `camera_move`.
    Video without `camera_move` is stream-copied when its video stream has exactly the engine segment format (Stream-copy and concat rules) and a keyframe at `source_in_frame`, else re-encoded.
  - `HYPERFRAMES_GSAP`: `gsap_scene_config` with `map_asset`, `pins` (`label`, `x`, `y` in 0-1 of the map, `at_frame`, `label_side`), `cards` (`title`, `subtitle`, `position`), `camera_preset`, `camera_intensity`, `focus`, `camera_path`, `regions`, `routes`, `title`, `title_entity`, `title_at_frame`, `figures`.
  - `camera_path` (optional, replaces `camera_preset`): >= 2 keys `{at_frame, focus: [x, y] 0-1 of the map, zoom 1.0 up to profile `[camera] max_zoom`, move}`.
    Key 0 is at frame 0; `at_frame` strictly increases past the previous key's hold and stays below the segment length (T02).
    `move` = the move from the previous key: `glide` (travel over the whole leg under a cubic ease whose end speeds keep the screen speed continuous: at a key between glides the slower leg's mean, next to a drift, pulse, hold or whip that leg's speed there, 0 where the motion turns by 90 deg or more, and profile `theme.camera_start_speed` / `camera_end_speed` x the leg's mean at the segment start / end), `settle` (see `beats`), `reveal` (one move over the whole key-to-key time under the profile cubic-bezier `theme.reveal_ease`, default `[0.28, 0.37, 0.29, 0.91]`: fastest at about 19 % of the move, then a long settle onto the key; fitted to the reference in `docs/proposals/geoverum-camera-reference.md`; a glide next to it matches its end speeds) or `whip` (the camera drifts on the previous view, then a fast `power3.inOut` move of profile `theme.whip_s` lands on the key, with directional motion blur peaking at `theme.whip_blur_px` mid-move and none on its first and last frame; a leg shorter than `whip_s` whips over its whole length).
    After the last key the camera drifts (push-in at a constant profile `theme.camera_drift_per_s` ln zoom per second about `[camera] pulse_anchor`) to the segment end.
    Optional key field `hold` (0-90 frames): the camera stays still on that key's view after it.
    Optional key field `drift_max` (ln zoom 0.01-2) bounds the drift of the wait on that key's view: it turns between 0 and the bound.
    Beats scenes write it for each wait whose drift breaks a per-frame layout rule (0.7 of the offending offset, floor 0.03).
    Pulse legs keep the drift's ln rate at both ends; a whip starts and lands at rest.
    With profile `[camera]` `pulse_zoom` > 0 every drift (before a whip and after the last key) holds evenly spaced pulses: (drift length - `pulse_gap_s`) // (`pulse_s` + `pulse_gap_s`) pulses alternating away from the drift and back to it.
    Optional key field `pulse` (0-1, or a list with one size per away pulse, the last repeating) sets the pulse sizes of the wait on that key's view; 0 = drift only.
    Beats scenes write it: the layout planner shrinks (0.75, 0.5, 0.25, 0 of `pulse_zoom`) each away pulse whose frames break a per-frame layout rule or move a pin label.
    Whips and drifts are inside one segment, so they are never cuts (C01).
    Transforms are clamped so the map always covers the frame.
    Optional key field `tilt` (deg, 0-60, default 0): perspective tilt of the map plane at that view, reached with the key's move and ease;
      the map, regions, routes, pins and map props turn together about the frame centre under `perspective(frame height) rotateX(tilt)` (top recedes), screen texts and screen props stay flat.
    Beats scenes do not tilt.
  - Pins follow `theme.pin_style`.
    `dot`: a dot in `theme.pin_colour` with a pulse ring and a boxed label above.
    `teardrop`: a teardrop head in `theme.pin_colour` with a white centre dot drops in with a bounce onto its shadow (0.6 s);
      a white, shadowed label of `theme.pin_label_px` in `theme.label_font` (`inter` or `script` = Kaushan Script) sits beside the head on `label_side` (`right` when absent).
    Pin bodies and labels keep one on-screen size at every camera key.
    With `theme.text_outline_px` > 0 teardrop labels and titles get an outline of that width outside the glyph in `theme.text_outline_colour` at `text_outline_alpha` (stroke painted under the fill) and a drop shadow (`text_shadow_dy_px`, `text_shadow_blur_px`, black at `text_shadow_alpha`) in place of the plain dark shadow;
      CSS variables `--txt-outline-px`, `--txt-outline`, `--txt-shadow`.
  - Map pixels: the map is drawn from a derivative of at most 3000 px on its longest side.
    When a frame zooms past the derivative's density, the segment also carries deep tiles: 1024 px tiles (2 px overlap) of the source resampled to the width the deepest frame needs (at most the source width), only where such frames look (+5 % margin), cached in `maps/` and drawn over the derivative inside the map layer, so map filters and grades cover both.
  - `regions` (optional): entity highlights `{entity, polygon: [[x, y], ...] >= 3 points 0-1 of the map, at_frame (default 0), edge}` drawn on the map under the pins: fill in the entity colour at `theme.region_fill_opacity`, edge `white` (white line) or `glow` (line and glow in the entity colour at `theme.region_glow_alpha`, radius `region_glow_px`; below 1 the glow sits on a stroke-only copy so the fill keeps exactly its opacity; absent = `theme.region_edge`) of `theme.region_edge_px` on screen at every zoom;
      fade-in 0.5 s from `at_frame` (below the segment length, T02).
    A region counts as a graphic event for H01 and N01.
  - `routes` (optional): movement lines `{points: [[x, y], ...] >= 2 points 0-1 of the map, at_frame (default 0), draw_s}` drawn on the map under the pins: a dashed line in `theme.route_colour` (`route_width_px` wide, `route_dash_px` dashes, `route_gap_px` gaps on screen at every zoom) draws itself from the first to the last point over `draw_s` (absent = `theme.route_draw_s`) with an in-out ease, and an arrowhead of `theme.route_arrow_px` rides the drawing tip.
    The drawing must end inside the segment (T02).
    A route counts as a graphic event for H01 and N01.
  - `title` (optional): big title centred near the top (top edge at 11 % of the height, max 900 px wide, wraps) from `title_at_frame` (default 0) to the segment end, in the colour of `title_entity` (absent = white) mixed `theme.title_tint` (0-0.6) toward white, with a same-colour glow of `theme.title_glow_px`;
      `theme.title_px` in `theme.title_font` (`inter` = Inter Bold, `title` = Archivo Black);
      0.5 s scale and fade in.
    `title_entity` without `title` = T02 fail.
  - `figures` (optional): big years and numbers `{kind: year|number, value, decimals (numbers, 0-3), prefix, suffix, motion: bounce|counter, count_from, count_s, at_frame, position: [x, y] 0-1 of the frame (centre, default [0.5, 0.3]), entity}` shown from `at_frame` to the segment end at `theme.figure_px` in the entity colour (absent = white) with a dark shadow.
    Years: Playfair Display weight 800, whole numbers 1-9999.
    Numbers: `theme.title_font`, thousands separator `theme.number_group`, decimal separator `theme.number_decimal`, rounded half up.
    `bounce`: elastic scale-in over 0.9 s. `counter`: counts from `count_from` to `value` over `count_s` (absent = `theme.counter_s`) with a cubic ease-out (GSAP `power2.out`).
    The entrance must end inside the segment (T02).
    Titles and figures count as graphic events for H01 and N01, and their text counts as screen text for K01.
  - `props` (optional; also per beat, timed from the beat start): free-positioned PNG or WebP images with alpha, or glTF / GLB / OBJ models rendered to one `{asset, asset_id, model {yaw (deg, default 30), pitch (deg above the horizon, -89 to 89, default 20), fov (0 = orthographic, else perspective degrees, default 0), size_px (sprite side, 64-2048, default 512), turntable_s (seconds per turn, looping; omitted = still), turn_deg (default 360)} (model assets only; defaults when omitted), anchor: map|screen, x, y (map: 0-1 of the map; screen: frame px) or lat, lng (map, via map_bounds), width_px or width_map (fraction of the map width), rotation (deg), opacity, t_in, t_out (frames, t_out exclusive, default segment or beat end), enter / exit {kind: fade|pop|slide, s, ease: glide|drift|whip, dir, distance_px (slide)}, idle {kind: bob|rotate, amplitude, period_s}, depth (0-2, map props: grows (s / s_in)^(1+depth) and parallaxes by 1+depth against the camera), z (stacking), shadow {dx, dy, blur, alpha}}`.
    Map props are positioned every frame from the shared camera legs (`window.__legs`) over the map; screen props sit over the shade under the texts.
    The segment key holds every prop image's bytes and parameters and `hf/props.js`.
    Model props: `video_engine/models.py` renders the model with three.js in the vendored headless Chrome (SwiftShader) to an RGBA PNG sprite, fitted by its bounding sphere and lit by a fixed hemisphere and key light;
      a turntable is min(240, round(turntable_s x fps)) frames composed into one sprite sheet (at most 8192 px wide).
    Sprites are cached (kind `models`) under the BLAKE3 of the model, every file it references, the render parameters, the renderer scripts and the toolchain identity.
  - `layers` (optional): parallax layers, repeating full-frame images over the map and the map props, under the shade and the texts `{asset (.png/.webp with alpha or .jpg, tiled), asset_id, depth (-0.9 to 2, default 0.5: 0 moves with the map, > 0 nearer and faster, < 0 farther and slower), tile_px (on-screen tile width at frame 0, default the image width), opacity, blend: normal|screen|multiply|overlay|soft-light, drift [vx, vy] (px/s at frame 0 scale)}`.
    Per frame, with k = (s / s0)^(1+depth): grid origin = frame centre - s0 k (1+depth) (c - c0) + k drift t, tile width = tile_px k (`video_engine/parallax.py`, rendered by `hf/parallax.js`).
    Tilted scenes tilt the layers too.
    The segment key holds every layer image's bytes.
  - `globe` (optional): Blue Marble globe instead of the flat map `{keys [{at_frame (first 0, increasing), lat (-85 to 85), lng, altitude (camera height over the surface in globe radii, 0.005-12), tilt (0-60 deg, view tipped from straight down toward local north), hold (optional, 0-90 frames still on the key)}], detail_tiles (project-relative dir of the NASA 8-tile set *.A1.jpg ... *.D2.jpg, 90 deg each)}`.
    `map_asset` is a whole-world 2:1 equirectangular image.
    A globe scene takes no camera_path, beats, regions, routes, props, layers or map_bounds;
      pins take lat/lng. three.js (vendor/npm) draws a unit sphere with the map downscaled to 8192 x 4096 (cache kind `maps`) at a 30 deg vertical field of view;
      the position travels along the great circle of each key-to-key leg (shortest path on the sphere; a leg that would pass
      beyond 85 deg latitude interpolates latitude and longitude instead), timed by a C1 monotone cubic in arc length that
      stops at holds and where the path turns by 90 deg or more; log altitude and tilt follow the same kind of cubic;
      profile `camera_start_speed` and `camera_end_speed` set the endpoint speeds as shares of the adjacent leg mean;
      at an inner key the speed is the slower neighbouring leg's mean, so no leg slows down and speeds up again.
    Altitude drifts in at the profile drift rate after the last key and hold.
    Frames needing more texels per degree than that texture get detail patches cut from the tiles (240 px/deg) or the map: one per density level and run of frames, at most 4096 px a side, drawn coarse to fine (`video_engine/globe.py`, rendered by `hf/globe.js`; pins are placed at their three.js projection and hidden on the far side).
    The segment key holds the texture and patch content keys.
  - `beats` (optional, profile `[layout]` only): `[{at_frame (relative to the segment start, strictly increasing, first 0), region, pins, markers, routes, title, card, area_labels: [{text, region}], camera}]` — what is on screen from each beat to the next.
    `pins` show head and label, `markers` the head only, `card` = `[year figure]` or `[year figure, time figure]` (Z1, not with `title`), `camera` false keeps the previous view.
    With beats the layout derives `camera_path`, every element `at_frame`, `title_at_frame` and all positions; hand-written values for them are replaced.
    `routes[].to_pin` (beats only): the route ends short of that pin's head.
    Camera key `move` `settle` = an eased `power3.inOut` move of `move_s` (default `theme.whip_s`) ending on the key, without blur.
    Rules: docs/SPEC.md, G.
  - Entity colours: one colour per entity for the whole video.
    `[theme.entities]` maps entity -> `#rrggbb`;
      every other entity takes the next unused `theme.palette` colour in order of first appearance (segment order; in a segment regions, title, figures).
    E01 checks the painted colours.
- `camera_move`: `preset` (`ken_burns_zoom_in`, `ken_burns_zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `static`), `intensity`, `auto_intensity` (default true), `focus_box` `[x0, y0, x1, y1]` in source pixels.
- `grade` (optional, any segment; also per beat, from the beat to the next): full-frame colour grade `{lift, gamma, gain (one value or [r, g, b]; -0.5-0.5, 0.2-5, 0-4; defaults 0, 1, 1), saturation (0-3, default 1, Rec. 709 luma), tint {color #rrggbb, amount 0-1} (hue and chroma toward the colour at the same luminance, clipped), lut (project-relative 3D .cube, applied after the values above), strength (0-1, default 1: mix over the ungraded picture)}`, applied in that order.
  Per frame: the beat's grade, else the segment's, else profile `[grade]`; `{}` = no grade.
  The grade is baked into one 33-point 3D LUT and applied by ffmpeg `lut3d` (tetrahedral, BT.709 both ways) to the rendered segment file, per frame range, before transitions and captions (captions are not graded).
  Graded files are cached in `segments/` under the rendered file's hash, the baked LUTs, their ranges and the encoder settings;
    the segment's mode in `build_report.json` gains `+grade`.
- `visual_type` (optional, any segment): `map`, `photo`, `video`, `diagram`, `archive` or `illustration`.
  Absent = HyperFrames `map`, native still `photo`, native video `video`.
  Read by gate V01 and profile `[transitions]` `by_type`; not part of any cache key.
- `overlays` (native segments): `{id, box: [x, y, w, h] output px, kind, text, subtitle, start_frame, end_frame}`.
  - `kind`: `reserve` (default; not drawn, reserves space for caption placement), `label` (centred single title on a dark plate), `text_card` (title + optional subtitle, accent bar), `lower_third` (name + role, accent bar).
  - Drawn kinds need `text`.
    `start_frame` / `end_frame` are relative to the segment start (half-open; defaults: whole segment).
    Fade in/out 0.2 s (shortened to a third of a short range).
  - Rendering: Pillow draws an RGBA PNG exactly the box size (Inter Bold/SemiBold; text wrapped, font shrunk from 54/72/64 px down to 26 px at 1920 height until it fits);
      ffmpeg composites it in the same encode as the segment (`overlay` with `enable=between(n,a,b-1)`).
    A segment with drawn overlays is never stream-copied.
    Overlay spec, font hashes and the Pillow version are part of the segment cache key.
  - Previews (`engine preview`) and gate snapshots include the drawn overlays.
- Every visual source and the BGM need a `credits_manifest` entry (`asset_id`, `path`, `source_url`, `license`, `watermark_free`).
  `credits.txt` in the project must contain every source URL.
  Licence `Own` / `Generated` needs no URL.
- `audio_chain`: `voiceover_path`, `bgm_path`, `bgm_asset_id`, `bgm_gain_db`, `auto_ducking`, `music_brief` (`{mood, energy: low|medium|high, bpm_min, bpm_max, duration_s}`, 30-240 BPM, `bpm_max >= bpm_min`; with a profile `[sound]` the music comes from the sound resolver for this brief, else from `bgm_path`), `target_lufs`, `true_peak`, `lra` (absent = profile value; default profile -14.0 / -1.5 / 11.0), `language`, `stt` (`{model, keyterms}`; model default `grok-voice-transcribe-2.0`, keyterms at most 100, each at most 50 chars), `caption_fixes` (`{from, to}` word-sequence replacements).
- `captions`: pinned word list `{word, start_frame, end_frame}`.
  The build reads these words and never re-transcribes while they exist.
  When absent and `auto_captions` is true, the build transcribes once (Grok STT + `caption_fixes`) and writes the words into the timeline before pre-flight;
    later builds reuse them.
- `captions_meta` (written with pinned words): `source`, `voiceover_blake3`, `stt_model`, `language`, `keyterms_blake3`, `fixes_blake3`, `fps`, `total_frames`, `clipped_words`, `words_cache_hit`.
  Absent = hand-written captions (W01 passes).
  Do not edit by hand; run `engine words`.

## Captions

- Compiled to `.ass` by Rust (`compile_ass`). 1-2 words per event.
  No background box (BorderStyle 1).
  White bold Inter, outline 5, soft offset shadow layer.
  Default font size 84 at 1920 px height.
- `caption_style` keys passed to Rust: `slot` (`lower` 0.70H, `mid` 0.50H, `upper` 0.24H), `font`, `size`, `bold`, `outline`, `soft_shadow`, `max_words`, `max_chars`, `active_word_highlight`, `margin_x`, and the other keys of the Rust `Style`.
  Unknown keys fail gate L22.
- Effective style = profile `caption_style` with the project `caption_style` keys on top.
- Python-only keys: `auto_place` (default profile: true), `clearance` (px at 1920 height between a caption box and an element box; default profile: 160; 0 = must not intersect), `y_overrides` (`[[start_frame, end_frame, y_px], ...]`; a caption whose start frame is in `[start, end)` is centred at `y_px`), `fixed_y` (px at 1920 height, scaled to the output height; centre y of every caption no `y_overrides` entry or placement override matches; absent = the slot y), `max_bottom_y` (px at 1920 height, scaled to the output height; lowest allowed caption box bottom, gate Z01; default profile: none).
- Caption box: centre y +/- 0.75 x font size, the caption width wide;
    it contains the rendered ink (glyphs, descenders, soft shadow) and is the box R62, R63, Z01 and automatic placement use.
- Caption timing always comes from the voiceover word timestamps (pinned words); no profile sets a caption rhythm.
  `build_report.json` -> `captions` reports `events`, `per_10s` and `mean_s` (information only, no gate).
- Automatic placement (R63): per segment, the caption centre moves in 10 px steps to the nearest y inside the safe area (bottom capped at `max_bottom_y` when set) that keeps `clearance` from every HyperFrames DOM element (pins, labels, cards) and every declared native overlay, and does not intersect the projected camera `focus_box`.
  No free y = fail.
