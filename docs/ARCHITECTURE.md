# Architecture: working model of `video-engine`

What an engineer or an AI agent needs to reason about the code cold: the mental model, the profile contract, the
data flow, who owns what, and the known traps. It is written for any agent, whatever model runs it. Operating rules
(commands, exit codes, mandatory principles) are in [AGENTS.md](../AGENTS.md). A fact has one home: where a document
already states it, this one links there.

**What this document answers**

- How the default engine and a channel profile relate, and what "fallback" means here.
- In which order profile values are resolved, and where to look when a value is not what you expect.
- How a draft and a build flow through the modules.
- Which module owns which concern, and which checks model output goes through.

## Mental model: one engine, two modes

The engine edits a video from a `master_timeline.json`. Every value it needs comes from one resolved `Profile`.

| Mode | When | What happens |
|---|---|---|
| **Default engine** | the timeline has no `profile` (or `"default"`) | `profiles/default.toml` alone. Channel style gates and the optional tables (`[legibility]`, `[layout]`, `[print]`, `[sound]`, `[watermark]`, `[grade]`) are off. |
| **Profile-adaptive** | the timeline names `profile: "<channel>"` | `profiles/<channel>.toml` is merged over the default. The channel sets look, gates, draft rules and sound. |

Fallback is **per key, never per file**. A key the channel does not set takes the value of the layer below. A
profile that cannot be read, parsed or validated is an `EngineError` (exit 1): the engine never renders with a
silently substituted default profile, because the output would look right and be wrong.

Resolution order, lowest first (`video_engine/profiles.py`: `load` -> `_layers` -> `_build` -> `_override`):

1. Model field defaults (`video_engine/profile_schema.py`): a key `default.toml` does not write.
2. `profiles/default.toml`.
3. The profile's `extends` chain, the base first. For each profile: its `include` files in list order, then its file.
4. The `[type]` cascade: a base size the channel sets reaches the surfaces the channel left open.
5. Timeline `profile_overrides` (only the tables in `profiles.OVERRIDABLE`; `null` turns an optional table off).
6. Timeline `caption_style` keys and `audio_chain` loudness (`profiles.caption_style`, `profiles.loudness`).

Tables merge key by key; a list or scalar replaces the lower value whole (`profiles.merge`, the one merge).
`audio.true_peak` and `audio.tolerance_lu` may only be tightened.

Entry points: `profiles.of(tl)` for a timeline, `profiles.load(name, overrides)` otherwise. Do not read profile
TOML anywhere else. To see what a profile resolves to and which file set a key:

```bash
engine profile geoverum_landscape --origin --table caption_style
```

## Profile contract

- Schema: the Pydantic models in `video_engine/profile_schema.py` (`ProfileData` and its tables) plus
  `LayoutProfile` in `video_engine/layout.py`. Every model forbids unknown keys. `engine schema profile` prints the
  JSON Schema. Key reference: [PROFILES.md](PROFILES.md).
- `caption_style` and `framing` stay dicts, each with its own check at load: `profiles.check_caption_style` (known
  keys, value types, geometry; it also runs for a timeline's `caption_style`) and `framing.check_overrides`.
- `[audio]`, `[draft]` and `[theme]` are filled by `default.toml`. An optional table (`[legibility]`, `[sound]`,
  `[layout]`, `[print]`) has a default for every key, so it may list only the keys the channel changes;
  `[watermark]` needs its `text`, and a style-gate table (`[gates.hook]`, ...) needs its thresholds.
- Orientation: `[format] orientation`, else inferred as portrait when the profile has `[layout]`, `[print]` or
  portrait caption geometry. Lengths are px at the reference canvas (1080x1920 or 1920x1080, `video_engine/units.py`).
- `Profile.blake3` hashes the effective profile (`profiles.hash_dump`). Splitting files with `include`, building on
  a base with `extends`, and deleting a key that only repeats a lower layer leave it unchanged. A new field must
  stay out of the hash at its default (`Field(exclude_if=...)`, or a rule in `hash_dump`), or every existing profile
  hash changes. `tests/test_landscape.py` pins the default profile's hash.
- `[draft] rules_file` points at `profiles/<channel>/creative.md`; each `- ` line is one prompt rule. A number that
  differs per format is written `{{table.key}}` and filled from the effective profile. Numbers written as literals
  must equal the TOML values (`tests/test_geoverum_style.py`, `tests/test_creative_workflow.py`).
- Adding a channel: `profiles/<name>.toml` with only the keys it changes (or `extends = "<base>"`), optional
  `include` parts under `profiles/<name>/`, then `engine profile <name> --origin`, `engine gates --profile <name>`
  and `pytest -q tests/test_profiles.py tests/test_profile_include.py tests/test_profile_layers.py`.

## Data flow

```
engine draft   inputs (voiceover, script, credits.txt, assets/) -> STT words (pinned)
               -> director.plan: 3 storyboard concepts, schema-checked, validated and scored   (AI request 1)
               -> draft.prompt + draft.schema -> model JSON                                     (AI request 2..n)
               -> ai.base.schema_errors -> draft.assemble -> Timeline (Pydantic) -> gates.run_preflight
               -> errors fed back (max_repairs) -> master_timeline.draft.json + draft_report.json
engine build   timeline.load -> profiles.of -> gates.run_preflight (layer 1, 2, 3; severity.apply)
               -> segments: native.py (Rust/FFmpeg) or hyperframes.py (headless Chrome), cached by content key
               -> transitions, captions (.ass via Rust), audio mix + loudnorm, final mux
               -> post-render gates -> out/final.mp4 + out/build_report.json
```

The model never writes URLs, licences, frame-exact geometry or file paths: it picks ids and line numbers, and the
engine copies facts from `credits.txt`, sidecars and the profile (`draft.assemble`, `recipes.compile`).

## Module map (who owns what)

| Concern | Module | Notes |
|---|---|---|
| Timeline contract | `video_engine/timeline.py` | All times are integer frames; `sec_to_frame` is the only seconds-to-frames conversion. `engine schema timeline` prints the JSON Schema. |
| Profile tables | `video_engine/profile_schema.py` | Pure models: keys, ranges, defaults. Reads no file. |
| Profile resolution | `video_engine/profiles.py` | Layers, `extends`, `include`, cascade, overrides, hash, `origins`, `flatten`. Re-exports the table models (`profiles.Theme`). |
| Gates | `video_engine/gate_registry.py`, `video_engine/gates/` | The registry is the single definition; `gates/severity.py` decides fail / warn / info. |
| AI providers | `video_engine/ai/` | `base.py` (parts, retry, reply parsing, schema subset), `agent.py` (file handoff, exit 3), `gemini.py`, `openai_compat.py`. |
| Draft | `video_engine/draft.py`, `draft_scene.py`, `recipes.py`, `director/` | Prompt and schema are built from the effective profile (`draft._profile`). |
| Renderers | `video_engine/native.py`, `hyperframes.py`, `template/`, `hf/` | Python/JS twins must stay in step (`FIGURE_POP_S`, eases). |
| Native core | `rust/hypoverum_core_rs/` | Camera, `.ass`, silence, hashing. Bump `ALGO_VERSION` on a behaviour change. |
| Shared helpers | `video_engine/util.py` | `EngineError`, hashing, `atomic_write_text`, `frame_jpeg` (video frame), `image_jpeg` (still image, in process), `wav_16k_mono`: one implementation each. |
| Paths, limits | `video_engine/config.py` | Everything machine-specific; `VIDEO_ENGINE_*` in [ENVIRONMENT.md](ENVIRONMENT.md). |

`video_engine/config.py` imports the Rust extension and reads `vendor/VERSIONS.json` at import time, so no engine
module imports without a built `hypoverum_core_rs` wheel ([CONTRIBUTING.md](../CONTRIBUTING.md), environment setup).

## AI layer contract

- The engine names no vendor default. Selection: `--provider` > env `VIDEO_ENGINE_AI_PROVIDER` > `default` in
  `ai_providers.toml` > `agent`. With nothing selected the request is a file handoff: the model (or person) running
  the engine answers the bundle under `<project>/ai_request/` and reruns the command with `--resume`. A request
  is not forwarded to a second service. Details: [AI.md](AI.md).
- A provider implements `generate_json(task, parts, schema, request_id, attachments) -> AIResult` and `cleanup()`.
- Retry rules live in `ai/base.run_models`. Do not add a retry loop elsewhere.
- Model output is untrusted. Order of checks: `parse_json_text` -> `schema_errors` (structure) -> `assemble`
  (meaning) -> Pydantic `Timeline` -> pre-flight gates. A new AI call runs `schema_errors` on the reply before it
  reads a field (`draft._attempt`, `director.plan`, `draft._review`, `tasks.qa_snapshots` all do).
- `schema_errors` checks a subset: type, properties, required, items, enum, minItems, maxItems. Anything else
  (`minLength`, `additionalProperties`) is enforced by Pydantic later, not there.
- A failure after the draft passed its gates (an unavailable or malformed review) is recorded in the report and the
  draft is kept; only the agent provider's pending bundle stops the command (exit 3).
- Prompts are assembled in code from the profile and the timeline: the frame format from `units(settings)` (worded
  once, `director.format_words`), the narration language from `audio_chain.language`. Never write a literal
  "vertical", "1080x1920" or a language name. Shared prompt principles live in `video_engine/director/guidance.md`
  (`director.guidance`), channel rules in `profiles/<channel>/creative.md`.
- Never print or store an API key value; reports name the environment variable only.

## Conventions

- Errors a user can act on are `EngineError` with the file or segment named; the CLI catches it and exits 1. A raw
  `KeyError` or `ValueError` reaching the CLI is a bug.
- Tests come in pairs: `test_good_*` and `test_bad_*` for every gate and feature. A bug fix adds the failing case.
- Writes that another process may read go through `util.atomic_write_text` (`timeline.save`, `director.save_taste`).
- A lock file is unlinked only while its `flock` is held, and a lock is trusted only when the path still names the
  locked file (`batch.ProjectLock`).
- Cache keys are content hashes of everything that shapes the product. A behaviour change that the key does not
  see is a correctness bug, not a performance detail.
- Code, comments and error messages are English; team-facing guides are Indonesian ([README.md](README.md)).
- Line length is long by convention (about 120-140); match the file you edit.

## Known traps

- `profiles.load` serves an unchanged profile from a memo keyed on the files' path, mtime, size and inode, and
  `_build` memoises on the file contents. A test that swaps profile files under one name inside the same timestamp
  tick is still correct (recently changed files are always re-read); call `profiles.clear_cache()` when in doubt.
- `[motion]` frame tokens (`micro_frames`, `overlay_frames`, `panel_frames`, `stagger_frames`) are exported to the
  scene but only `entrance`, `move`, `exit`, `entrance_scale` and `overlay_fade_s` change a render.
- `extends` cannot unset a key of its base. Format-specific keys (portrait caption geometry) live in a file only
  that format includes, which is why `geoverum_landscape` shares `geoverum/core.toml` by `include`.
- `profiles/taste/<channel>.json` is both tracked seed data and runtime state: a successful `engine draft` rewrites
  `recent`. Tests redirect it with `VIDEO_ENGINE_TASTE_DIR`.
- A redraft keeps the project's `profile_overrides` but starts with no project `gates` (waivers are decisions about
  the timeline they were written for).
- Tests that need the vendored HyperFrames toolchain or the open-assets flag library skip with the reason when it
  is not installed; `VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1` makes them fail instead ([TESTING.md](TESTING.md)).

## Before you finish a change

```bash
python scripts/check_docs.py
ruff check --select E9,F63,F7,F82,F401,F841 video_engine tests scripts
pytest -q tests/test_<area>.py
```

A profile change also runs `engine taste geoverum --lint` and
`pytest -q tests/test_profiles.py tests/test_profile_include.py tests/test_profile_layers.py tests/test_geoverum_style.py`.
