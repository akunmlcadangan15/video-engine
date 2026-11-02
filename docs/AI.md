# AI providers, director and draft

The AI providers and the AI-assisted draft stage.

**What this document answers**

- How to choose a provider, and how the Gemini, agent and OpenAI-compatible providers differ.
- How the agent handoff works (request bundle, `response.json`, exit code 3, `--resume`).
- How to add a model.
- How the director picks a storyboard and how `engine draft` builds and repairs a timeline.

`engine draft`, `engine director` and `--ai-qa` send provider-neutral requests (prompt text, JSON schema, images, audio/video files) to one provider.
Prompts, schemas, normalising, gates and the repair loop are shared; only transport differs.

| Provider | Model | Media |
|---|---|---|
| `gemini` (default) | `gemini-3.8-flash`; falls back to `deepseek` | audio and video via the File API (deleted and verified), images inline |
| `agent` | an agent or human editor answering request bundles; reported model = `[agent] model_label` (default `grok-bot`) | files in the bundle; video as sampled JPEG frames, audio as transcript with word frames |
| `[providers.NAME]` in `ai_providers.toml` | OpenAI-compatible chat completions endpoint | images inline (base64); audio / video inline when listed in `capabilities`, else transcript / sampled frames |

Selection: `--provider NAME` > env `VIDEO_ENGINE_AI_PROVIDER` > `default` in `ai_providers.toml` > `gemini`.
An unknown name is an error listing the known names.
Every result records `provider` and the `model` that answered.

Retry and fallback (every API provider): HTTP 429 / 503 (`RESOURCE_EXHAUSTED` / `UNAVAILABLE`) or invalid JSON -> retry the same model up to 3 times, waiting 2 s, 5 s, 12 s (+ up to 25 % random jitter).
Then the next model.
Transport drops without an HTTP status (`RemoteProtocolError`, read/connect timeouts, `ConnectionError`) count as 503.
HTTP 404 -> next model at once.
Any other status (400, 401, 403, ...) -> error, no retry.
Every HTTP request has a 300 s timeout.
Every result records `model`, `retries`, `fallback`, `tried` (every attempt with its status).
A fallback answer prints `warning: FALLBACK: ...` to stderr and stores the same text in the result (`draft_report.json` -> `rounds[].ai`, AI QA -> a `warn` finding);
  the warning names the answering provider and model as `provider/model`, with no doubled prefix when the model id already starts with `provider/`.

Gemini has one model.
Any Gemini error after these rules, including a missing `GEMINI_API_KEY`, sends the same request (task, parts, schema) to the configured fallback provider.
The next request tries `gemini-3.8-flash` again first.

## Gemini

- Model: `gemini-3.8-flash` for planning, draft, director and AI QA.
- Fallback: `[gemini] fallback_provider` in `ai_providers.toml` names the `[providers.NAME]` entry that answers a request Gemini cannot answer (absent: `deepseek` when configured, else no fallback).
  An explicit value that is not a `[providers.NAME]` entry, including `gemini` and `agent`, is an error naming the value and the known entries.
  The result records provider `deepseek`, the model that answered, `fallback = true`, the FALLBACK warning and every attempt in `tried`.
  If the fallback provider fails too, the command fails naming both errors.
  With no configured fallback the Gemini error is raised.
- Config: JSON output with schema, `MEDIA_RESOLUTION_LOW`, `thinking_budget=1024`.
- Uploaded files are deleted after use and the deletion is verified: `files.get` must fail with 404 or 403 (the File API answers 403 for a deleted name) and the name must be absent from `files.list` (`file_deletions[].verified_gone`).
  The fallback provider's `cleanup()` runs in the same step.

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
A run without `--resume` deletes the earlier bundles of that request first.
The draft sends no video proxies to the agent by default (thumbnails carry 3 frames per video).

## Adding a model (OpenAI-compatible)

Add a table to `ai_providers.toml` (or the file in `VIDEO_ENGINE_AI_CONFIG`) and put the key into the named environment variable:

```toml
default = "gemini"            # provider when no --provider flag and no VIDEO_ENGINE_AI_PROVIDER

[agent]
model_label = "grok-bot"

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

## Director

- Footage: 360p 5 fps proxy (ffmpeg spawned by Rust `make_proxy`, written to the run's own `director-<pid>-<random>/proxy` scratch dir, deleted after the request) -> provider (Gemini: File API upload; agent and models without video input: 8 sampled frames).
- Output: shots with `start_s`, `end_s`, `description`, `focus_box` (0-1000 grid), `suggested_camera`, `has_watermark_or_text`.

## Director stage (`engine draft`)

Before the timeline request the provider writes 3 storyboard concepts (request `draft/storyboard`, one repair `draft/storyboard1`).
A concept is a list of beats (`from_word`, `to_word`, `goal`, `pattern`, `assets`, `on_screen_text`, `camera`, `transition`) with self scores.
The engine rejects concepts that use unknown assets or facts absent from the narration, script, asset hints and credits, then scores the rest: fit 0.35, clarity 0.25, asset availability 0.25, render cost 0.15, minus 0.15 for an opener used in the last `avoid_recent` drafts and 0.03 per recently used transition (max 0.15).
The best concept goes into the timeline request as `CHOSEN STORYBOARD`; each segment carries its `goal`.
Prompt text and patterns: `video_engine/director/guidance.md`, `video_engine/director/patterns.toml` (establish, progressive_reveal, comparison, tracing, meaningful_repetition, emphasis_by_pause, footage_beat, paradox_hook, border_anomaly_tour, scale_overlay, causal_barrier, cross_section_depth, historical_flashback).
Output: `draft/storyboards.json`, `draft_report.json` -> `storyboard`.
No valid concept = warning and the draft continues without one.

Taste store `profiles/taste/<channel>.json`: `rules`, `liked`, `rejected`, `recent` (opener and transitions of accepted drafts), `avoid_recent`.
Its text goes into the storyboard prompt.

`--review` (`--review-rounds` 1 or 2): after a draft passes the gates, up to 16 critical times (cuts, beats, text starts) are sampled as 360 px frames and sent with the timeline (request `draft/review<k>`).
A `revise` verdict with a blocker or major finding asks for a revised timeline (`draft/revise<k>`);
  a revision that fails the gates is dropped and the previous version kept.
Every version is saved as `draft/timeline.v<N>.json` and `draft/review.v<N>.json`; `draft_report.json` -> `review`, `versions`.

## Draft

- Inputs: narration (`--voiceover`, else `audio_chain.voiceover_path`, else the first audio file named narasi/narration/voice/vo in the project root or `assets/`), script (`--script`, else `script.txt` / `narasi.txt` / `narration.txt`), `credits.txt` in the project root (required), every file under `assets/` with metadata (`assets/manifest.csv`, `<file>.json` sidecars, existing timeline credit), optional 360p 20 s video proxies.
- Profile: `--profile`, else the existing timeline's `profile`, else `default`; written into the draft timeline.
- Pre-model analysis: narration pauses (profile `[draft]` `min_seg_s` / `max_seg_s`; default 1.5 s / 7 s) give the boundary candidates;
    words come from the pinned captions or a transcription.
- Prompt: engine rules 1-5 with the profile segment length, then the profile `[draft]` `rules`.
  With `[draft] map_path = true` a map segment may return `map_path` keys `{at_s, x, y, zoom}` (converted to `camera_path`: first key at frame 0, later keys sorted, deduplicated by frame, clamped to the segment and the value ranges; fewer than 2 keys = repair).
- Map scene elements (`video_engine/draft_scene.py`, one table for schema and conversion): a `kind map` segment may return
  `map_pins[].flag` (ISO alpha-2), `map_pins[].lat/lng` (needs the map's sidecar `map_bounds` or a globe), `map_regions`
  (with `flag`, `edge`, `style`, `label`, `changes`), `map_routes`, `map_borders`, `map_figures` (`motion: counter`) and
  `map_flag_characters`; the profile `[draft] scene` list adds `map_globe`, `map_props`, `map_morph` and `map_beats`
  (beats also need `[layout]`). Times are seconds from the segment start and must fall inside it; points are [x, y] 0..1
  of the map image; props and morph name draft asset ids (credited like the map). Each element is validated against its
  timeline model and the whole scene against `GsapScene`; an unknown key, a value out of range, a key the profile does not
  expose or a map key on an image/video segment is a repair error naming `segment N: <key>[i].<field>` (never dropped).
  A map image sidecar `<map>.json` with `"map_bounds": [south, west, north, east]` is offered to the model and copied to
  `gsap_scene_config.map_bounds`. Open-asset credits the scene needs (flag-icons) are added from the asset registry at
  assembly, as the build does.
- Before assembly the answer is checked against the request schema (`ai.base.schema_errors`): type errors come back as
  `schema: $.segments[i]...` repair errors.
- Model output: JSON matching a schema whose `asset` field is an enum of inventory ids and whose credit field is a credits.txt line number.
  URLs are copied from credits.txt lines; the model cannot write a URL.
- Each segment carries `narration` (the words it covers) and `why` (how the visual matches those words).
- Request media: narration audio and up to `--video-proxies` video proxies (default 2; agent 0); images and 3 frames per video as 384 px JPEG thumbnails.
  Gemini uploads audio and proxies via the File API and deletes and verifies them after the last round.
- Pipeline: model -> normalise (frame snapping, contiguity, overlay slots) -> validate -> pre-flight -> on failure send the gate errors back as `GATE ERRORS` and ask for a corrected draft, at most `--max-repairs` rounds (default 2).
- `draft_report.json`: `provider` and `model` of the last draft round (`provider` is the configured provider name when no AI round ran), `fallback` (true when an answer came from a fallback model), `warnings`, `retries_total`, `repair_rounds`, `rounds[]` (errors per round), `file_deletions[]`, `segments[]` (`narration`, `why`), `normaliser_fixes`, `seconds`.
  On an exception it holds `error` and the CLI exits 1; while an agent response is pending it holds `awaiting` (bundle path) and the CLI exits 3.
