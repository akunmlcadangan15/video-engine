# Video Engine — Rationale

Why the engine is designed the way it is.

**What this document answers**

- Which problems of the earlier system the design prevents.
- Why a specific rule, threshold, order or file layout was chosen (one reason per bullet).
- What to check before changing a rule that looks arbitrary.

## Problems the design prevents
1. HyperFrames (headless Chrome + SwiftShader) used for non-graphic work (cuts, pan/zoom, captions): ~20x slower than native
   stream processing; caption edits forced full Chrome re-renders.
2. 65 R gates ran AFTER render (~19 min draft gates, up to 7 drafts of 164-256 s). Most (R07 credits URLs, R14 licence,
   R44 duplicate text, R56 camera motion delta, R62/R63 text margins) must be validated BEFORE render.
3. Spaghetti patching: ~9.9k lines, 883 files, 27 bash scripts, 132 one-incident patches (M1-M132), meta-gates (R41/R43/R46);
   80 of 87 gates unproven; selftest 19 min.
4. Slow sequential audio chain (~11 min), no content-hash caching.
5. Disk pollution: scripts copied into every project (version drift), temp frames on disk, 1.8 GB per project.
6. Infra bugs: hardcoded port 8837; 2-project queue blocks helper previews; float-time frame rounding mismatches; rigid
   camera presets (no intensity); manual final join and Drive naming.
7. Scratch deletion outside its own job: a scratch dir keyed by project id alone lets a project id of "." or ".." name
   the scratch root or its parent, a string-prefix check accepts a sibling such as /dev/shm/video_engine_other, and two
   jobs with the same project id share and delete one dir. Each invocation therefore creates its own dir with mkdtemp,
   resolved strictly inside the scratch root, and removes only that dir; timeline writes use writer-unique temp names
   for the same reason. Caches meant to be shared (words, segments, maps, sounds) live in the cache dir, never in
   scratch, so per-invocation scratch costs no cache hits.

## Design choices

- Integer frames everywhere remove float drift between segments, captions and the concat.
- Pre-flight gates run before any render because a failed render costs several times more than the gate checks.
- Caption placement is computed from measured element boxes (DOM probe) instead of fixed slots because map pins and cards move with the scene.
- Words are pinned in the timeline because speech-to-text output can vary between requests;
    a cold build with an empty cache must render the same captions as the build that was reviewed.
- HyperFrames uses the default capture profile (JPEG q82 screenshots, x264 `ultrafast` at CRF 14; overridden by profile `[render]`) because screenshot transfer and encoding dominate map-segment time, and the intermediate is re-encoded once more by the mux, so the profile does not change visible label or map detail.
  The profile is part of the segment cache key.
- Loudness is enforced on the final file because concat, caption burn and AAC encoding change the measured integrated loudness after the mix stage.
- AI work goes through a provider interface so that prompts, schemas, gates and the repair loop are identical for every model;
    a provider only moves bytes, which keeps the gate results comparable across models.
- The agent provider hands off through files and a distinct exit code because an agent or editor works between commands, not inside an HTTP call;
    resume replays every round from its stored `response.json`, so the engine keeps no state between invocations and every bundle can be inspected or re-answered.
- Media that a model cannot take is degraded to a transcript or sampled frames instead of being dropped, so every provider sees the narration timing and the footage content.
- Channel style lives in profiles merged over one default profile, so a channel changes only the keys it differs in and every other channel keeps the engine values.
- Profiles may only tighten the true-peak limit and the loudness tolerance because those limits protect every delivered file, not one channel's look.
- `audio_chain` loudness fields are absent unless the project sets them, so saving a timeline (`engine words`, `--write-fixes`) never pins profile values into the project file.
- The profile cache is keyed by file content, because two edits inside one filesystem timestamp tick must still load different profiles.
- Segment cache keys carry exactly the profile values their renderer reads (the HyperFrames scene `theme`), not the profile hash;
    a hash there would re-render every segment after a gate threshold edit without changing a pixel.
- A camera path is one HyperFrames segment with several legs because moving between places on one map is a camera move, not a cut;
    R56 checks each leg so a key that barely moves cannot hide behind a large leg.
- Camera legs tween x, y and scale with one ease, so every in-between transform is a convex mix of two clamped transforms and the map never exposes its edge.
- A whip waits on a drift and spends only its last `whip_s` moving, so a far jump reads as one fast gesture that lands and settles instead of a slow slide;
    the blur deviation follows the on-screen travel (a quarter on the other axis because zoom smears every direction) and is applied in map pixels divided by the zoom.
- The whip blur is switched on only strictly inside the whip (half a frame in from each end), so the frames on either side of a whip are pixel-identical to a scene without blur, and scenes without a whip never carry the filter.
- Entity colours resolve once per timeline (profile map, then palette by first appearance) so an entity keeps its colour across segments;
    the scene carries the resolved colour per element, and `palette` / `entities` stay out of `theme` so adding an entity to the profile map re-renders only the segments whose colours change.
- E01 reads the colour the browser actually paints, not the scene value, so a template change that breaks the colour wiring fails the gate.
- Pin colour and label font are CSS variables set from the scene theme, so a channel restyles pins without a second template and the default theme reproduces the default pins pixel for pixel.
- Theme fonts are copied into a HyperFrames build only when the theme uses their role, so a theme that does not use a font keeps the same build files and cache key.
- A route is revealed by animating a solid mask stroke rather than the dash offset of the line itself, so the line keeps its dash pattern while it draws.
- The soft region glow sits on a separate stroke so the fill opacity is not compounded by the glow filter.
- E01 checks region fills only: text on a map must change colour with its background to stay readable, so an exact entity colour on text would contradict R64;
    the region fill still carries the entity identity.
- Adaptive legibility uses APCA Lc rather than the WCAG 2 ratio as the gate metric because APCA models polarity and spatial frequency of text, so dark-on-light and light-on-dark text at the same ratio are judged differently, which the WCAG ratio does not do;
    WCAG is still logged as the familiar backup (APCA documentation, apca-w3 0.1.9; https://git.apcacontrast.com/documentation/APCA_in_a_Nutshell.html).
- Lc targets follow the APCA use-case levels: 75 where readability matters for thin or small text (the script labels), 60 for content text people must read (titles, years, counters; 75 is the aim), 30 for spot-readable marks and solid non-text, 15 as the point of invisibility that no sample may cross.
- The background is measured under the dilated glyph mask, not a box ring, because only the pixels the glyphs and their halo cover decide legibility;
    the darkest percentile for dark ink and the lightest for light text are the hard cases, and the adjacent-pixel check catches a thin dark road or border running through a letter.
- Dark ink is the default and light text only goes on dark backgrounds because a paper-toned map is mostly light;
    white text with a dark outline on paper depends entirely on the outline and reads as a sticker (Esri, "Polishing Your Halo", https://www.esri.com/arcgis-blog/products/arcgis-living-atlas/mapping/polishing-your-halo).
- A halo in a lightened, desaturated version of the local background, thin and partly transparent, separates text from map detail without punching a white hole into the map, which is the classic cartographic halo rule (Esri, "Polishing Your Halo"; Esri / Aileen Buckley, "Make Maps People Want to Look At", https://www.esri.com/news/arcuser/0112/make-maps-people-want-to-look-at.html; Axis Maps, "Labeling", https://www.axismaps.com/guide/labeling).
- W3C technique G145 (contrast 3:1 for large text) sets the WCAG backup for titles and numbers;
    4.5:1 applies to labels (https://www.w3.org/WAI/WCAG21/Techniques/general/G145).
- The halo is a computed mask behind the text rather than a text stroke or an SVG closing filter: a stroke and a morphological closing both fill the counters of letters (and closing also bridges the gaps between script letters), which darkens the inside of o, e, a and digits and makes them read as blobs;
    a mask whose enclosed loops are cleared keeps the counters showing the map, as a cartographic halo does (Esri, "Polishing Your Halo").
- The halo blur stays at or below half the halo width because a wider blur spreads the halo alpha so thin that the band no longer separates the letter edge from the map;
    width 0.06-0.10 em and alpha 0.6-0.85 are the range where the halo separates text without becoming a visible sticker (Esri, "Polishing Your Halo"; Axis Maps, "Labeling").
- The halo mask uses font faces with the digit features baked in because canvas text ignores font-variant-numeric;
    without them Playfair's default old-style digits would be masked under lining digits and the counters would sit in the wrong place.
- The counter gate compares the painted frame with the text-hidden frame inside the counters, so it catches any paint that fills a loop (a stroke, a closing, a misplaced mask) without knowing how the halo was drawn.
- Water names are blue italic because that is the standard cartographic convention for hydrography, so a viewer reads a sea or strait name as water before reading the word (Axis Maps, "Labeling"; Esri / Buckley, "Make Maps People Want to Look At").
- Pins, routes, region outlines and leaders are gated at Lc 30 because APCA puts spot-readable marks and solid non-text at Lc 30;
    25 is the fail floor so a short pass over a busy map patch warns instead of failing.
- Line contrast takes, per background pixel, the best of the line's colours because a cased line is seen by whichever edge differs from the map there: the dark casing on light paper, the light core or outline on dark map;
    a per-colour percentile would fail every two-tone line on a mixed map.
- Region outlines get a casing in the tone opposite their colour because a mid-tone entity colour has no contrast against a mid-tone map;
    the opposite-tone casing gives one of the two edges contrast against any map pixel, the same double-contour principle as the route casing and the pin outline.
- Leader lines take the label's ink and halo because a white leader disappears on paper; in the label's colours the leader reads as part of the label.
- Escalation goes halo opacity, then a feathered paper vignette, then darker ink, because each step changes the map less than the next;
    a hard box behind text hides the map and is never used.
- One polarity and one style per element interval, with hysteresis only as a fallback, because a label that flips colour reads as flicker;
    a switch must survive `hold_frames` and its grey crossfade midpoint must itself be readable, otherwise the element keeps one style and is flagged.
- Muting basemap chroma while thematic layers are on screen, translucent entity fills and full-colour outlines keep the entity layer and the labels ahead of the basemap in the visual hierarchy (Esri / Buckley design principles; Axis Maps).
- The palette is chosen in OKLCH at similar lightness so no entity dominates; ochre is too light for text, and teal and indigo are too close in hue to sit side by side.
- Geoverum keeps six unpinned palette colours so any project can show six regions with distinct colours on top of the pinned entities;
    colours stay at low chroma so they sit on the vintage paper texture, and a minimum OKLab distance of 0.07 keeps neighbouring fills apart.
  Pinned entities of different stories share a colour when they never appear in the same video (E01 compares only the entities of one timeline), and each pinned entity takes the palette colour nearest its target reference blocking colour.
- Text layers sit outside the whip blur because motion-blurred text cannot be read during the move and its contrast cannot be measured.
- Inks are computed once per segment from a background probe and stored in the scene, so the render and the R64 probe paint the same style at every seek;
    the segment cache key includes the hash of the module that computes them.
- The watermark is overlaid once in the final mux rather than drawn in each segment: it covers native and HyperFrames segments alike, keeps segment cache keys and default output unchanged, and B01 checks its fixed box against the element boxes the DOM probe already collects.
- Counter tick events are computed in Python with the same ease and rounding as the template counter, so a sound cue lands on the frame where the shown text changes without reading the render.
- Region edges are counter-scaled per camera leg like pin labels, so a highlight edge keeps one on-screen width through zooms and whips.
- The camera drifts after the last key because a stopped camera reads as a frozen frame;
    the drift rate is a theme value so a channel can change it without touching the template.
- Style gates are off unless a profile declares their table, so adding a channel gate cannot change another channel's pre-flight result.
- Every segment boundary is a shot change;
    it counts as a hard cut only without a `[transitions]` transition, because a whip or page turn carries the eye across the boundary.
  The cut-rate warn applies to hard cuts and the fail levels to every shot change, so transitions cannot hide an over-cut edit.
  Camera moves inside one segment change the frame continuously and are not counted.
- Camera pulses replace long still drifts because short-form maps that hold still for seconds read as frozen;
    an earlier reference channel moved the camera in about 90 % of frames with holds under 1.7 s (the 48 reference Shorts re-measured in 2026-11: median 77 %, see below). Pulses are fast cubic-bezier moves (0.3, 0, 0.15, 1) separated by drift so the camera never stops;
    Geoverum pushes in rather than pulling out because pulling out shrinks the space between map labels and heads, while pushing in only spreads it.
- Geoverum camera values keep the rendered camera moving (`[camera]` `pulse_zoom = 0.18`, `pulse_s = 0.85`, `pulse_gap_s = 0.45`, and `[theme]` `camera_drift_per_s = 0.06`);
    the P01 thresholds (`hold_warn_s = 3.0`, `hold_fail_s = 5.0`, `moving_warn = 0.65`) follow the 48 reference Shorts measured in 2026-11
    (camera moving on a median 77 % of frames, p10 64 %; longest hold per Short median 2.6 s, p75 3.6 s, p90 4.5 s, while overlays keep animating),
    so they flag freezes longer than the reference holds without warning on its ordinary settles.
- The Geoverum drift (0.06 ln / s) lies above the P01 still threshold (0.05 ln / s), so the drift between pulses counts as active camera motion;
    the still threshold stays at 0.05 so a near-stopped camera is still counted as a hold.
- The Geoverum pulse anchor (`[0.5, 0.48]`) is centred in zone Z2, so the content framed in Z2 stays centred while the camera pushes in.
- Geoverum uses instantaneous hard cuts (`kind = "cut"`) when switching between distinct visual types and a short 0.35 s state dissolve (`same_type = { map = "state" }`) when transitioning between consecutive map segments on the same basemap;
    page-turn transitions (`page_turn = false`) and automatic map morphs (`map_morph = false`) are disabled by default.
- P01 measures the frame-centre speed in two dimensions, so a vertical pan counts as motion like a horizontal one.
- The drift zooms at a constant ln rate because a linear scale growth slows in ln terms as the zoom grows: a linear wait fell under the P01 still threshold after 3.3 s and held.
  It zooms about the pulse anchor (the Z2 centre in Geoverum) so content placed in Z2 stays there while the scale grows;
    a frame-centre drift pushes route ends near the edge out of Z2.
- The planner bounds a drift only where the drift caused the break: the same frame with the camera held on the wait's first view must be clean, so an element entering outside its zone does not stop the camera.
  A bounded drift ping-pongs instead of stopping, so the wait keeps moving.
- Pulse legs superpose the drift rate on their ease (a constant ln rate plus the shaped rest, signed with the leg's own zoom change) because bezier ends are still for several frames each.
  Whips do not: one ease drives pan and zoom together, so a drift share would turn a whip's in-out pan linear when its zoom change is small.
  Across multi-segment map timelines the camera moves 93 % of planned frames this way (91 % before the drift changes).
- Pin label moves are matched to pulse frames by whole frame number, and every pulse shrink step is strictly smaller with a floor at which the pulse becomes drift, so the planner's shrink loop ends after at most four steps per pulse.
- The layout planner checks every frame off the base drift against the per-frame layout rules and shrinks only the offending pulse, so a pulse never pushes a label off its zone, across an edge or into a move, and the rest of the wait keeps its motion.
- P01 measures the planned camera rather than decoded frames, so the gate runs before render and agrees with the template and the native renderer by construction.
- Transitions are drawn after the segments, from their decoded frames, and overlaid in the one-pass mux, so segment renders and their cache keys stay as they are and a profile without transitions produces the same mux command.
  Clip timestamps are frame indices in a 1/fps time base so each clip frame covers exactly one output frame whatever the time bases of the segment files.
- The page turn's shade fades with the turn and the whip settles to scale 1 on its last frame, so the frame after a transition window is identical to the frame the transition ends on.
- Counter-scaled elements take, on every camera leg, the ease that makes their scale exactly 1 / camera zoom on each frame;
    a 1/a -> 1/z tween with the camera ease is exact only at the leg ends, which leaves a label 56 % oversize at the middle of a 1 -> 4 drift, glide or whip and up to 2 % off mid-pulse (L01 DOM match).
  One leg builder (`window.__legs`) serves every template block, so the camera and each counter-scale read the same ease.
  The fly-through scale sits on the label wrapper inside the counter-scaled box, so its designed entry and exit multiply a constant planned size.
- R63, caption clearance and B01 skip a fly-through text only inside each entry and exit of its spans, so a text that exits and enters again is checked at scale 1 in every later span.
  They read fly-through texts only at scale 1: a text in flight crosses the frame by design, and its settled box is the one the layout gates hold to the safe area.
- Fly-through texts scale only on entry and exit and stay at scale 1 while held, so the layout plan (boxes at scale 1) and the L gates hold for every settled frame.
  Geoverum exits in 0.33 s (`[layout.timing] exit_s`): at 0.35 s the shifted beat schedule puts the first wait's area label within a pin head's clearance on pushed-in frames, which removes most of that wait's pulses.
- Hook and novelty gates read pin and card appear frames from the same functions the HyperFrames scene uses, so the gate and the render cannot disagree on when an element appears.
- The default mix cuts its length after the sidechain compressor and before loudnorm, not on the compressor inputs: sidechaincompress ends at the first input EOF without draining its buffered samples, so trimmed inputs lose a music tail whose length depends on packet sizes and thread timing, and padding afterwards hides the loss in the duration.
  Unbounded inputs (padded voiceover, looped music) keep the compressor fed up to the cut, which makes the PCM identical on every render;
    the cut before loudnorm keeps the loudness measurement on exactly the delivered span.
- A build publishes `out/final.mp4` and `build_report.json` only after every gate passed on the staged file, through `os.replace` within the output dir, because a reader (delivery, contact sheet, an agent workflow) must never see a file that failed a gate or a half-written file, and a failed run must not destroy the last good deliverable.
  The failure report has its own name so a reader of `build_report.json` always gets the report of the published file, and the API fails on every fail finding (raise, or `ok=False` on the return path) so a library caller cannot publish what the CLI rejects.
- Silence is gated on the final file because ducking, loudnorm and the AAC encode change the level of quiet passages after the mix.
- A fixed caption y is a Python override placed last, so project `y_overrides` still win and the Rust compiler keeps one placement mechanism.
- Geoverum titles track in (letters close in from 0.35 em at the settled width) above their region instead of fading in on a fixed top line, because the reference titles appear about 0.2 s after their spoken word next to the place they name, and a letter-level entry reads as one deliberate event where a whole-word scale or bounce read as random.
    The title keeps its settled width on every entry frame, so the entry never crosses the safe area that the settled title respects.
- Entity colours are vivid only on dark satellite maps (`vivid_chroma_scale`): the reference fills there carry chroma 0.17-0.20, while a muted fill at 85 % opacity read as a flat patch; on paper maps the muted palette multiplies into the paper as a print would, so it stays.
    Scaling chroma (not raising it to one floor) keeps the palette's differences, so the vivid twins stay at least 0.07 apart in OKLab like the palette.
- Numbers pop with a short back ease from 0.6 rather than an elastic bounce from 0, because the elastic overshoot of a wide number crossed the frame edge.
- Geoverum captions (Inter Bold 48, the reference Shorts' caption size) are centred at `fixed_y = 1366` (the reference ink centre) and end at or above `max_bottom_y = 1480` because the YouTube Shorts lower UI overlay covers the bottom of the 1080x1920 frame below y 1480;
    the limit is a profile key enforced by gate Z01 so a later `fixed_y`, project override or automatic placement within `place_band = [0.58, 0.76]` cannot move a caption below the safe boundary.
  Z01 checks the same caption box as R62/R63 and placement; a libass render test keeps that box around the real ink, so the gate does not need to read pixels.
- Caption rhythm follows the voiceover word timestamps; a fixed cadence target would contradict the narration timing, so captions per 10 s is reported and not gated.
- The loudness range is reported and not gated because no stage of the mix controls it.
- Responses are checked by a small built-in validator for the schema subset the engine emits, which avoids a schema-library dependency.
- SFX cues are derived from the same element frames the renderers use (pin drop, region fade, route draw, whip, card, title, figure, counter step), so a sound cannot drift from the motion it marks;
    a whoosh is aligned by its loudest sample because its onset is a quiet build-up.
- Under profile `[sound]`, music ducking is a keyframed gain built from the voiceover word timestamps rather than a sidechain compressor, so the duck depth and ramps are exact and the mix is reproducible;
    cues are summed before loudnorm so the delivered loudness still meets the profile target.
- Sound files come through one adapter in the mixer so the sound package can replace the local test audio without touching the mixer;
    local test audio is marked in its provenance so it cannot pass as licensed sound.
- Sounds come from a cache that an agent fills through a request/response file handoff, so the engine stays offline and holds no keys and connector credentials stay with the agent.
  Files are content-addressed with a per-file provenance sidecar, and the engine writes the credit lines from id, title and artist, so an agent cannot produce a mismatched credit.
  A profile's `require_source` is checked by a delivery gate (A02) rather than by the resolver, so builds and tests without sourced audio still mix with local tones and A02 warns by default unless required.
- Map scenes are laid out from beats rather than hand positions because untidy frames came from independent choices (centred stacks, labels across region edges, numbers on seams, several highlighted regions at once);
    one planner that knows every box per frame can keep one hero, one active region and a fixed reading order.
- Positions use a 6-column grid with an 8 px unit and the layout text safe area (`[layout.zones] safe`, x 48-888, y 288-1248), so titles and data cards line up on one left edge and nothing sits under the YouTube UI;
    Z1 above the map keeps text off the map, and Z2 gives the camera a known window for the active region.
- One hero per beat at >= 1.5x the next text and never two big numbers together follow the size-contrast rule of visual hierarchy: a reader finds the focal element first only when it is clearly larger (Nielsen Norman Group, "Visual Hierarchy in UX", https://www.nngroup.com/articles/visual-hierarchy-ux-definition/).
- Pin labels try the cartographic preference order (upper right first) and keep an edge clearance, because a label that crosses a region edge reads as belonging to both areas (Imhof, "Positioning Names on Maps", 1975; Axis Maps, "Labeling", https://www.axismaps.com/guide/labeling).
- Area labels go inside their polygon, uppercase and tracked, because a name inside an area labels the area and a name beside a point labels the point (Axis Maps, "Labeling").
- Routes anchor to map points and stop short of the pin head, because an arrow that starts at a title points from text, not from a place.
- Elements enter only after the camera settles and leave before it moves, staggered, with holds of max(`min_hold_s`, chars / `chars_per_s`) (engine default max(1 s, chars / 20); Geoverum max(2 s, chars / 20)), because text moving with the camera cannot be read and a reader needs time proportional to the text length.
- Label positions keep their previous choice unless a hard rule breaks or a clear gain persists, because labels that jump between candidates read as flicker.
- Layout gates check every frame and, with DOM on, the rendered boxes at each beat's settled frame and mid-move, because an overlap may exist only during a move (for example a pin label over a figure for one second) and a plan that the template paints differently would pass a plan-only check.
- Legibility samples a beats element only while its span shows it, because the layout hides elements between beats and a hidden element has no contrast to measure.
- The layout lives in its own profile section and module, so a profile without `[layout]` keeps its hash and output.
- Printed map text has no glyph halo at any step: a halo on paper reads as a sticker, so the ink first darkens, then a faint paper knock-back helps, and an element that still fails is flagged rather than haloed.
- The surface, not the background sample, fixes the text polarity under `[print]`, because white text on a paper map and dark ink on a satellite photo both break the printed look even where a sample would allow them.
- Paper maps for printed text are calm and low-detail: dense archival linework under a multiplied fill caps contrast below the label and line targets, which no ink or knock-back within the brand limits can recover.
- The camera fit counts the label above each pin, because a framing that ignores it pushes the label out of Z2 and the planner then hides it.
- Caption Z01 counts the outline and shadow reach beyond what the caption band already holds, so a deeper shadow cannot slip ink below the caption limit.
- Cache files a build resolves are leased for the whole build, and every eviction (LRU after a store, `--no-cache`, `clean --cache`, `engine cache --prune`, the sound prune) skips leased files, because a build reads its cached segments, maps and sounds long after resolving them (concat, mux) and a prune in that window, from the same build's own store or from another process, would delete an input it already counted on.
  A lease is a per-process file under the cache root rather than an flock held for the build, so a pruner can list what is held and report it;
    leasing happens under a shared flock before the existence check and eviction runs under the exclusive flock, so no prune can fall between a lookup and the lease.
  Leases carry the holder's pid and /proc start time, so a crashed build's lease is reclaimed and a recycled pid cannot keep files alive.
  The entry being stored is exempt from the eviction its store triggers, so a cache budget smaller than one output cannot delete that output.
- The HyperFrames segment key hashes the toolchain's bytes (node, the Chrome install, the whole npm tree, GSAP, template, DOM probe) and the ffmpeg version text rather than the pinned version strings, because a reinstall, a patched package or a transitive dependency can change pixels without changing a pin, and hashing about 400 MB costs under a second once per process;
    a stat-keyed memo in the cache skips even that on later runs, and relative paths keep keys shared between checkouts.
  The map derivative enters the segment key as its recipe key (source hash, width cap, resampler, JPEG quality, Pillow version) rather than its bytes, so `engine cache --prune` and the gates can compute keys without rendering the derivative.
- Card images are copied into the build dir under their content hash, because two images with one basename in different folders would otherwise overwrite each other and render the same picture twice.
- Stream copy and the concat demuxer require every compared stream parameter to be equal, time base included, because both keep the input packets and timestamps as they are: two clips with time bases 1/15360 and 1/90000 joined by the demuxer moved the second clip's start from 0.200 s to 1.172 s, and a mismatched SPS, SAR, colour or field order would reach the output as an unannounced mid-stream change.
  One function decides every copy and join, so the paths cannot disagree.
- The copy target is the probed output of the engine's own encoder settings rather than a list of expected values, so the check follows `config.X264` and the installed ffmpeg without a second copy of those settings.
- Segments that are not joinable are decoded and retimed with `setpts=N` by the concat filter instead of being re-encoded per segment, because the final mux encodes once anyway: the join stays deterministic and the segment pixels are unchanged.
- A native stream copy keeps the source edit list instead of shifting timestamps with `-avoid_negative_ts make_zero`, because the shift moves the first frame of a B-frame stream to the decoder delay (0.066 s at 30 fps) while the edit list presents it at 0.
- Every segment, the final mux and the gain pass are checked for frame count, start and duration after they are written, so a timing fault stops the build at the step that caused it.
- Map props are positioned per frame from the camera legs instead of living inside the camera layer, so depth can scale and parallax them differently from the map while staying on the same eased transform;
    a prop inside `#cam` could only move exactly with the map.
- Prop resolution is graded against the largest on-screen width over all shown frames, not the first frame, because a camera push-in or depth growth enlarges a prop well after it enters.
- Props enter the caption avoid-list with their own frame ranges, so a caption only moves for a prop shown at the same time.
- Models render on SwiftShader (software WebGL) in the pinned Chrome rather than a GPU, because GPU drivers rasterise differently from machine to machine;
    the software path gives byte-identical sprites, so the content-hash cache and the segment keys stay valid across hosts.
- A model is fitted by its bounding sphere rather than its box, so every yaw and pitch of a turntable fits the same canvas at the same scale and the sprite never jumps in size while it turns.
- The model page waits for its loading manager to go idle before rendering, because OBJ material textures load after the OBJ itself resolves;
    rendering earlier made the sprite depend on network timing.
- A turntable is one sprite sheet shown through a clipping box rather than a sequence of image swaps, because the sheet is one element the frame capture already waits for;
    swapping sources per frame can capture a frame before the next image is decoded.
- The sheet cell is taken with a 1e-4 frame tolerance, because seek times reach the page rounded to microseconds and an exact floor would show the previous cell on some frames.
- Camera depth is a profile value rather than an engine constant, because a channel that zooms from a continent to a single city needs 16x while the default look never needs more than 4x;
    the engine only refuses zooms past 64x.
- Close zooms read deep tiles of the source instead of a larger whole-map derivative, because a whole map at 16x density would be a 30000 px image per segment;
    tiles are cut only where frames actually look.
- Every map image of a deep scene is its own compositing layer, because Chrome keeps the raster scale a painted layer had on its first frame;
    inside one layer a zoomed tile shows no more detail than the derivative.
- M02 fails only below half the needed width, because a 2x upscale of a satellite mosaic still reads as terrain, while the warn at 1x keeps the softness visible in the report.
- Grades are applied to the rendered segment file rather than inside each renderer, because one pass covers native footage, HyperFrames maps and props alike and leaves the render cache untouched when only the grade changes.
- Every grade is baked into one 3D LUT, because a single lut3d pass per range gives the same result for parameter grades and .cube looks and keeps the order of operations fixed.
- The grade pass upsamples chroma by nearest neighbour and downsamples by area, because the default bilinear round trip through RGB blurs 4:2:0 chroma (about 33 dB on saturated test patterns) while this pair returns an ungraded frame within one code value (about 49 dB).
- Captions are burned in after the grade, because a look must never change caption contrast that the legibility gates measured.
- Geoverum sets no default grade: the reference keeps the satellite map ungraded and uses a full-frame red grade only on one beat (42.43-43.6 s, landlocked), which a beat `grade` expresses.
- Tilt turns the flat camera picture as one CSS 3D layer instead of moving a 3D camera over the map, because every existing 2D plan (legs, pins, deep tiles, props) stays valid and the tilt is one exact projection that the gates can mirror.
- Pins and labels tilt with the map rather than being re-projected flat, because they are map-anchored and the DOM probe measures their real on-screen boxes, so R63 and R64 judge what is drawn.
- Tilt is limited to hand-written camera paths, because the beats planner fits elements into flat screen zones that a tilt would move.
- M03 fails instead of zooming automatically, because a silent extra zoom would change the planned framing; the message names the fixes.
- Parallax layers repeat their image instead of sizing one picture to the camera path, because a tiled grid covers the frame at any pan, zoom or depth without per-scene oversize math, and cloud, haze and texture assets tile by design.
- The globe is drawn by three.js on a sphere instead of reprojecting the flat map, because the perspective view, the limb and the far-side pin hiding all come from one camera model, and SwiftShader renders it deterministically in the same headless Chrome the props already use.
- The globe texture is capped at 8192 x 4096 because that is SwiftShader's largest texture side;
    deeper views use detail patches per density level and run of frames, so each patch stays within one texture while the texel density follows the camera altitude.
- Patches are drawn without depth test on the same radius, coarse to fine, because offset shells shift under tilt at low altitude and a convex sphere's front faces never hide one another.
- M04 measures density at the view centre against px per degree there;
    the frame edges under tilt sit nearer the camera and may be softer, which the centre measure accepts as the focus of the shot.
- Layers use the props depth law (scale ratio to the power 1 + depth), so a layer and a prop at the same depth move together.
- Style gates warn unless the channel requires them, because taste rules (cut rate, novelty, keyword return) differ per edit, while a broken file, a missing credit or a loudness miss is wrong for every edit.
- A project can add required gates but not remove them, and a waive of a required gate is ignored with a warning, so a channel rule cannot be switched off from one timeline.
- `hold: "read"` lowers only P01 and N01 findings, because a reading pause is a deliberate lack of motion and novelty, not a lack of anything else.
- P02 checks the theme drift rate rather than frames, because a drift below the still threshold turns every wait between moves into a P01 hold.
- The manifest is written before any render and sounds are locked in it, because a second resolver call can return a different music file and silently change a rebuilt video.
- Manifest paths are relative to the project, cache root or engine checkout, so the committed manifest is valid on any checkout.
- Lock and delivery checks hash files fresh, because a same-size rewrite within one process would otherwise reuse a stale hash.
- Composite video and master audio are separate cached passes joined by a stream copy, because a music or loudness correction must not re-encode the video, and a visual change must not re-run the mix.
- Stage keys are the ffmpeg command with inputs replaced by their sha256, so a key changes exactly when an input or a parameter changes.
- One worker budget caps every stage, because native, HyperFrames and composite pools sized independently oversubscribe the CPU and RAM of small machines.
- The director picks among several storyboards scored by the engine instead of trusting the model's first plan, because the engine can check asset availability, invented facts and render cost, and the taste store can penalise an opener the channel just used.
- The pattern library describes intent (reveal, comparison, tracing) and sets no minimum number of effects, because effect quotas push edits toward motion for its own sake.
- A review revision that fails the gates is dropped and the previous version kept, so the review pass can only improve a passing draft.
- Storyboard craft notes (a generic span, text said twice, text over the word budget, several numbers, one pattern sequence for every concept) lower a concept's score and never reject it, because gates catch mistakes, not dull ideas: a hard rule on taste would turn a preference into a technical constraint.
- The review request carries frames, the timed narration and a per-segment brief, not the timeline or the builder's reasons, and a finding must cite a frame that was sent, because a reviewer that reads the argument for a shot judges the argument instead of the picture.
- R38 judges each third of every sample on its own instead of tracking one blankest third, because two empty thirds that take turns being the blankest each reset a single tracked run and an empty area goes unreported.
- Delivery splits only above the part limit and names parts from 1 with the JOIN text built from the same list, because in the old shell splitter a file at exactly the limit produced a lone `.part0` that the JOIN instructions did not name.
- Each stage cache key holds the content hash of the engine sources that shape its product, beside the stage version constant, because a forgotten version bump otherwise reuses stale renders;
    gate modules stay out of every key because gate results are never cached, so a checker edit costs no re-render.
- The native key holds the ffmpeg `-version` output and the x264 thread count, because both change the encoded bytes.
- libx264 encodes with `config.X264_REPEATABLE` (one lookahead thread, sliced threads off, deterministic frame threading), because the default threaded lookahead encodes the same frames to different bitstreams when the CPU is busy, so a second uncached build of the same timeline would not match the first.
- HyperFrames capture is a SwiftShader screenshot (`--no-browser-gpu`, `PRODUCER_FORCE_SCREENSHOT`). Current Chrome has no HeadlessExperimental BeginFrame, so that path is the one that runs. The map camera is not a composited layer (`will-change` is left unset): a promoted layer is resampled on a busy CPU and the textured part of the frame changes by a fraction of a pixel. HyperFrames encodes the frames with its own libx264 at one thread per CPU; `video_engine/hf/ffmpeg_repeatable.py` rewrites that encode to one thread and `X264_REPEATABLE`, because that thread count does not repeat. Headless Chrome launches also pass through `video_engine/hf/chrome_repeatable.py`, which adds `--disable-gpu-rasterization` and `--num-raster-threads=1` so parallel SwiftShader workers rasterize 2D layers on one CPU thread without per-worker GPU-raster variance while WebGL 3D scenes keep SwiftShader.
- DOM probe measurements are cached while gate verdicts are not, because the measurements are a pure function of the built segment dir, the probe arguments and the browser toolchain, which the probe key hashes in full, whereas a gate verdict also depends on checker code that must re-judge every run;
    the browser seeks and screenshots dominate layer2_dom, and the numpy legibility math stays live.
- Probe entries carry the segment cache key as a name prefix, because `engine cache --prune` keeps entries by the keys of the current timelines and needs no second index.
- At most 2 HyperFrames segments render at once, and only when native x264 threads and both Chrome pools fit the worker budget, because one segment render leaves most of the 8 vCPU idle (3.3 of 8 cores measured) while 2 concurrent renders peaked at 7.4 of 15.6 GB RAM.
- Camera-only stretches stay HyperFrames renders and the Rust warp is not extended to rotation, tilt, whip blur or alpha, because the HyperFrames CLI renders no frame range (a native stretch would need a second proxy timeline, a screen/camera layer split, alpha stills and a re-encode), and in continuous map timelines the map scale changes 8-49 % over every 93-frame window while counter-scaled labels stay within 3 % for only 23-52 frames, so a still per stretch is not exact;
    the warp extension has no consumer without the stretches.
- Transition clips are written bit-exact (no random Matroska UIDs), because the composite key hashes them and a random container id made every build with transitions miss the composite cache.

## Why hybrid is retained

The engine is hybrid on purpose: the native Rust/ffmpeg path handles cuts, trims, pan/zoom, captions and the audio chain, while the HyperFrames HTML/GSAP/Three.js path renders the motion-graphics segments (map, globe, props, chart, card, title, and related animated scenes). HyperFrames (headless Chrome + SwiftShader) is roughly 20x slower than native stream processing, so routing everything through it would slow the whole build and force a full Chrome re-render on every caption edit; routing everything through native would lose declarative graphic interactivity (camera legs, beats layout, adaptive legibility, counter-scaled labels, globe and props) that has no native equivalent. Keeping both paths, joined by one concat/mux pass, therefore gives fast cuts and captions and rich motion graphics in one deterministic pipeline, with pre-flight gates before either path renders and a single staged publish that only a kernel error can stop.
