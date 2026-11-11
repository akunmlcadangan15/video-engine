# AI providers, director and draft

The AI providers and the AI-assisted draft stage.

**What this document answers**

- How a provider is chosen (there is no vendor default: the model that runs the engine answers), and how the agent, Gemini and OpenAI-compatible providers differ.
- How the agent handoff works (request bundle, `response.json`, exit code 3, `--resume`).
- How to add a model.
- How the director picks a storyboard and how `engine draft` builds and repairs a timeline.

`engine draft`, `engine director` and `--ai-qa` send provider-neutral requests (prompt text, JSON schema, images, audio/video files) to one provider.
Prompts, schemas, normalising, gates and the repair loop are shared; only transport differs.

| Provider | Model | Media |
|---|---|---|
| `agent` (used when nothing is selected) | the AI model or person that is running the engine, answering request bundles; reported model = env `VIDEO_ENGINE_AI_MODEL`, else `[agent] model_label` (default `agent`) | files in the bundle; video as sampled JPEG frames, audio as transcript with word frames |
| `gemini` (optional; `--provider gemini` and the `gemini` extra) | `[gemini] models` (shipped: `gemini-3.8-flash`); `[gemini] qa_models` for AI QA | audio and video via the File API (deleted and verified), images inline |
| `[providers.NAME]` in `ai_providers.toml` | OpenAI-compatible chat completions endpoint | images inline (base64); audio / video inline when listed in `capabilities`, else transcript / sampled frames |

Selection: `--provider NAME` > env `VIDEO_ENGINE_AI_PROVIDER` > `default` in `ai_providers.toml` > `agent`.
The engine names no vendor default and the shipped `ai_providers.toml` sets no `default`: with nothing selected a request is a file handoff to the agent provider, so the model in use is the model that drafts, and no prompt, transcript or thumbnail leaves the machine unless a provider was chosen.
A request goes to that one provider. A failure is not forwarded to a second service. Several models in one provider's list are tried in order; another service is used only when that provider is the one selected.
An unknown name is an error listing the known names.
Every result records `provider` and the `model` that answered.

Retry and fallback (every API provider): HTTP 408 / 429 / 500 / 502 / 503 / 504 (the transient statuses; `RESOURCE_EXHAUSTED`, `UNAVAILABLE`, `INTERNAL`, `DEADLINE_EXCEEDED`) or invalid JSON -> retry the same model up to 3 times, waiting 2 s, 5 s, 12 s (+ up to 25 % random jitter).
Then the next model.
Transport drops without an HTTP status (`RemoteProtocolError`, read/connect timeouts, `ConnectionError`) count as 503.
A reply counts as JSON when it is a bare JSON document, a ```` ```json ```` fenced block (also after leading prose), or one complete document that starts at the first `{` / `[` and is followed by prose; chat-completions content given as a list of text parts is joined first. `NaN` / `Infinity` and a cut-off document are invalid JSON (`ai.base.parse_json_text`).
HTTP 404 -> next model at once.
Any other status (400, 401, 403: the request or the key is wrong) -> error, no retry.
Every HTTP request has a 300 s timeout.
Every result records `model`, `retries`, `fallback`, `tried` (every attempt with its status).
A fallback answer (the next model in that provider's list) prints `warning: FALLBACK: ...` to stderr and stores the same text in the result (`draft_report.json` -> `rounds[].ai`, AI QA -> a `warn` finding).

## Gemini

Optional. It runs only when `gemini` is the selected provider (`--provider gemini`, `VIDEO_ENGINE_AI_PROVIDER`, or `default` in the AI config). It needs the `gemini` extra (`google-genai`) and `GEMINI_API_KEY`.

- Models: `[gemini] models` for planning, draft and director; `[gemini] qa_models` for AI QA (absent: `models`). The shipped file lists `gemini-3.8-flash` for both. When the table omits them, the provider uses that same model.
- A missing key, a missing `google-genai` package, an unknown `[gemini]` key, or a failed call after the model list is exhausted is an error. The request is not sent to another provider. Known keys: `models`, `qa_models`.
- Config: JSON output with schema, `MEDIA_RESOLUTION_LOW`, `thinking_budget=1024`.
- A reply with no text and a finish reason other than a normal stop (`MAX_TOKENS`, `SAFETY`, ...) is an error naming the reason, not three identical retries.
- Uploaded files are deleted after use and the deletion is verified: `files.get` must fail with 404 or 403 (the File API answers 403 for a deleted name) and the name must be absent from `files.list` (`file_deletions[].verified_gone`).

## Agent handoff

The agent provider writes one self-contained request bundle per request and exits with code 3. The agent writes `response.json` into the bundle and reruns the same command with `--resume`.

| Request | Bundle directory |
|---|---|
| draft, round N | `P/ai_request/draft/N/` |
| `preflight` / `build --ai-qa` | `P/ai_request/aiqa/0/` |
| director | `<bundle-root>/director/<src stem>_<start>s/` |

| File | Content |
|---|---|
| `request.md` | Answer instructions, the resume command, then the exact prompt in request order with image references (`![label](images/NN_label.jpg)`); repair rounds include the previous draft and the gate errors |
| `schema.json` | JSON schema the answer must satisfy |
| `images/NN_*.jpg\|png` | Every image in request order: asset thumbnails (draft), sampled proxy frames labelled `t=S s` (director), segment snapshots (AI QA) |
| `contact_sheet.jpg` | All images on one numbered grid (2 or more images) |
| `transcript.json` | Narration words `{word, start_frame, end_frame}` (draft) |
| `inventory.json`, `credits.json` | Asset inventory and numbered credits.txt lines (draft) |
| `gate_errors.txt`, `previous_response.json` | Gate errors and the rejected answer of the previous round (draft rounds >= 1) |
| `response.json` | Written by the agent: one JSON object valid against `schema.json` |

Resume: the command replays every round from its `response.json`.
A response that passes the gates finishes the command (exit 0).
A draft response that fails the gates produces the next round's bundle with the errors (exit 3), up to `--max-repairs`;
  after the last round the draft is rejected (exit 1).
Invalid JSON, a non-object, or a schema mismatch is an error naming the file and the failing paths (exit 1).
A pending bundle without `response.json` on `--resume` is an error (exit 1).
Each bundle stores `request.digest`, a hash of the task, the prompt text and the schema it was written for. A `response.json` whose bundle was written for a different request (the inputs, the profile or an earlier answer changed since) is an error telling you to run without `--resume`: an answer is never replayed against a prompt it was not written for.
A run without `--resume` deletes the earlier bundles of that request first.
The draft sends no video proxies to the agent by default (thumbnails carry 3 frames per video).

## Adding a model (OpenAI-compatible)

Add a table to `ai_providers.toml` (or the file in `VIDEO_ENGINE_AI_CONFIG`) and put the key into the named environment variable:

```toml
# default = "mymodel"        # optional: provider when no --provider flag and no VIDEO_ENGINE_AI_PROVIDER (absent: agent)

[agent]
model_label = "agent"         # reported model of agent answers; env VIDEO_ENGINE_AI_MODEL wins

[providers.mymodel]
base_url = "https://api.example.com/v1"      # POST {base_url}/chat/completions
models = ["model-large", "model-medium"]     # primary first, then fallbacks (draft, director)
qa_models = ["model-small"]                  # optional (--ai-qa); default = models
api_key_env = "MYMODEL_API_KEY"              # env var holding the key; the key itself is never in the file
capabilities = ["image"]                     # image | audio | video input
json_mode = "json_schema"                    # json_schema (schema attached) | json_object (schema in the prompt)
timeout_s = 300
```

Use it with `--provider mymodel`, `VIDEO_ENGINE_AI_PROVIDER=mymodel` or `default = "mymodel"`.
A missing key variable is an error naming the variable.
Request: one user message with `text` and `image_url` (base64 data URL) items, `input_audio` with the `audio` capability, `video_url` with the `video` capability;
  without a capability, audio becomes its transcript, video becomes sampled JPEG frames, images become a text note.
Replies in a ```` ```json ```` fence are accepted.

## AI QA (`--ai-qa`)

The gate snapshots go to the provider's QA models with one prompt (the frame orientation comes from the timeline).
The answer is checked against its schema; an answer that breaks it is an `AIQA` warn. An entry about a segment that was not sent is ignored, and a snapshot the answer leaves out is a warn: only a segment the model reported on can pass.

## Director

- Footage: 360p 5 fps proxy (ffmpeg spawned by Rust `make_proxy`, written to the run's own `director-<pid>-<random>/proxy` scratch dir, deleted after the request) -> provider (the gemini provider: File API upload; agent and models without video input: 8 sampled frames).
- Output: shots with `start_s`, `end_s`, `description`, `focus_box` (0-1000 grid), `suggested_camera`, `has_watermark_or_text`.

## Director stage (`engine draft`)

Before the timeline request the provider writes 3 storyboard concepts (request `draft/storyboard`, one repair `draft/storyboard1`).
The answer is checked against its schema first; an answer that breaks it is not ranked and its schema errors go into the repair request.
A concept is a list of spans (`spans`; legacy alias `beats`) (`from_word`, `to_word`, `goal`, `pattern`, `assets`, `on_screen_text`, `camera`, `transition`) with self scores.
The engine rejects concepts that use unknown assets or facts absent from the narration, script, asset hints and credits, then scores the rest: fit 0.35, clarity 0.25, asset availability 0.25, render cost 0.15, minus 0.15 for an opener used in the last `avoid_recent` drafts and 0.03 per recently used transition (max 0.15).
The best concept goes into the timeline request as `CHOSEN STORYBOARD`; each segment carries its `goal`.
Prompt text and patterns: `video_engine/director/guidance.md`, `video_engine/director/patterns.toml` (establish, progressive_reveal, comparison, tracing, meaningful_repetition, emphasis_by_pause, footage_beat, paradox_hook, border_anomaly_tour, scale_overlay, causal_barrier, cross_section_depth, historical_flashback).
Output: `draft/storyboards.json`, `draft_report.json` -> `storyboard`.
No valid concept = warning and the draft continues without one.

Taste store `profiles/taste/<channel>.json`: `rules`, `liked`, `rejected`, `recent` (opener and transitions of accepted drafts), `avoid_recent`.
Its text goes into the storyboard prompt as historical preferences, not universal technical constraints. Dated notes are preserved even when the current profile differs; `engine taste CHANNEL --lint` exposes mismatches. The project brief and declared shot intent govern creative departures; source facts, render integrity and effective configuration remain authoritative.

`--review` (`--review-rounds` 1 or 2): after a draft passes the gates, up to 16 critical times (cuts, beats, text starts) are sampled as 360 px frames and sent with the timeline (request `draft/review<k>`).
A `revise` verdict with a blocker or major finding asks for a revised timeline (`draft/revise<k>`);
  a revision that fails the gates is dropped and the previous version kept.
Every version is saved as `draft/timeline.v<N>.json` and `draft/review.v<N>.json`; `draft_report.json` -> `review`, `versions`.
A review cannot undo a draft that passed the gates: a review answer that breaks its schema, or a provider error during the review (API providers), is recorded in `review[].errors` with `kept`, printed as a warning, and the gate-passing draft is written as usual. Findings about a segment the timeline does not have are dropped.

## Draft

- Inputs: narration (`--voiceover`, else `audio_chain.voiceover_path`, else the first audio file named narasi/narration/voice/vo in the project root or `assets/`), script (`--script`, else `script.txt` / `narasi.txt` / `narration.txt`), `credits.txt` in the project root (required), every file under `assets/` with metadata (`assets/manifest.csv`, `<file>.json` sidecars, existing timeline credit), optional 360p 20 s video proxies.
- Profile: `--profile`, else the existing timeline's `profile`, else `default`; written into the draft timeline. An existing timeline's `profile_overrides` are kept, and prompt, schema and assembly use that effective profile.
- The prompt names the frame format from the timeline settings and the narration language from `audio_chain.language`.
- `credits[].watermark_free` is written only as the model declared it; an asset without a declaration has none and gate R14 reports it.
- Pre-model analysis: narration pauses (profile `[draft]` `min_seg_s` / `max_seg_s`; default 1.5 s / 7 s) give the boundary candidates;
    words come from the pinned captions or a transcription.
- Prompt: engine rules 1-5 with suggested profile segment lengths, then channel identity, defaults and alternatives from `[draft] rules`. Concepts must differ in strategy, pacing and evidence, not only decoration. A moving-map opener, continuous camera, fixed act sequence and final keyword callback are not universal requirements.
- Segment `intent` and `creative_autofix` are accepted in draft JSON, validated and preserved into the timeline. The review prompt receives the local intent and judges visible comprehension failures rather than the absence of a preferred style. See [Timeline intent](TIMELINE.md).
  With `[draft] map_path = true` a map segment may return `map_path` keys `{at_s, x, y, zoom}` (converted to `camera_path`: first key at frame 0, later keys sorted, deduplicated by frame, clamped to the segment and the value ranges; fewer than 2 keys = repair).
- Map scene elements (`video_engine/draft_scene.py`, one table for schema and conversion): a `kind map` segment may return
  `map_pins[].flag` (ISO alpha-2), `map_pins[].lat/lng` (needs the map's sidecar `map_bounds` or a globe), `map_regions`
  (with `rings` for islands and holes, `flag`, `edge`, `style`, `label`, `changes`), `map_flows`, `map_thermometers`, a map
  `intensity` (camera_intensity), `map_path[].roll` (with `[draft] map_path`), `map_routes`, `map_borders`, `map_figures` (`motion: counter`) and
  `map_flag_characters`; the profile `[draft] scene` list adds `map_globe`, `map_props`, `map_morph` and `map_beats`
  (beats also need `[layout]`). Times are seconds from the segment start and must fall inside it; points are [x, y] 0..1
  of the map image; props and morph name draft asset ids (credited like the map). Each element is validated against its
  timeline model and the whole scene against `GsapScene`; an unknown key, a value out of range, a key the profile does not
  expose or a map key on an image/video segment is a repair error naming `segment N: <key>[i].<field>` (never dropped).
  A map image sidecar `<map>.json` with `"map_bounds": [south, west, north, east]` (and `"projection": "mercator"` for a
  Web Mercator raster) is offered to the model and copied to `gsap_scene_config.map_bounds` / `map_projection`.
  `video_engine/geodata.py` writes such a base map (`base_map`) and turns a Natural Earth country into a region
  (`country_region`: every part and hole, clipped to the bounds, simplified, flag from the ISO code; `label_anchor`). Open-asset credits the scene needs (flag-icons) are added from the asset registry at
  assembly, as the build does.
- `map_recipe` (any profile; needs a map with sidecar `map_bounds`): the model names a story pattern and its facts and the
  engine compiles the scene (`video_engine/recipes.py`): `country_reveal {countries, labels}`, `follow_route {points,
  labels}`, `compare_places {a, b, source, split}` (split = two stacked panels, one camera each), `explain_flow {paths, labels, legend, legend_note}` (moving `flows`, one colour and legend row per label), `stat_emphasis {value, unit,
  decimals, source, label, place}`, each with `at_s`. Camera keys come from the geometry: the deepest zoom that keeps the
  bbox (and its pin label boxes) inside the R63 safe area minus the text bands, capped by the map resolution (1.2 source
  px per screen px, gate M02) and the profile `max_zoom`, checked through the renderer's own transform; every camera leg
  moves >= 110 px (R56). Text bands and the compare layout (`bands` or `adjacent`) are chosen from where the geometry
  lands on screen. Geometry that must stay whole and cannot fit at zoom 1 is a repair error, not a crop. A recipe and
  the `map_*` fields it fills (camera, regions, routes, pins, figures, card) are exclusive. `country_reveal` reads Natural
  Earth admin-0 from `$VIDEO_ENGINE_GEO_LIBRARY` (`natural-earth/natural-earth-vector/geojson/`). `recipes.map_extent`
  gives a portrait map extent for a scene's points. Director patterns name their recipe (`patterns.toml` `recipe`) and
  the chosen storyboard passes it to the draft as `on a map: map_recipe <name>`.
- Before assembly the answer is checked against the request schema (`ai.base.schema_errors`): type errors come back as
  `schema: $.segments[i]...` repair errors.
- Model output: JSON matching a schema whose `asset` field is an enum of inventory ids and whose credit field is a credits.txt line number.
  URLs are copied from credits.txt lines; the model cannot write a URL.
- Each segment carries `narration` (the words it covers) and `why` (how the visual matches those words).
- Request media: narration audio and up to `--video-proxies` video proxies (default 2; agent 0); images and 3 frames per video as 384 px JPEG thumbnails.
  The gemini provider, when it is the one selected, uploads audio and proxies via the File API and deletes and verifies them after the last round.
- Pipeline: model -> normalise (frame snapping, contiguity, overlay slots) -> validate -> pre-flight -> on failure send the gate errors back as `GATE ERRORS` and ask for a corrected draft, at most `--max-repairs` rounds (default 2).
- `draft_report.json`: `provider` and `model` of the last draft round (`provider` is the configured provider name when no AI round ran), `fallback` (true when an answer came from a fallback model), `warnings`, `retries_total`, `repair_rounds`, `rounds[]` (errors per round), `file_deletions[]`, `segments[]` (`narration`, `why`), `normaliser_fixes`, `seconds`.
  On an exception it holds `error` and the CLI exits 1; while an agent response is pending it holds `awaiting` (bundle path) and the CLI exits 3.
