@AGENTS.md

# CLAUDE.md: working model of `video-engine`

AGENTS.md above holds the operating rules (commands, exit codes, mandatory principles). This file adds what an agent
needs to reason about the code cold: the mental model, the profile contract, the data flow and the known traps. A
fact has one home: when a doc already states it, this file links there instead of repeating it
([docs/README.md](docs/README.md) is the index).

## Mental model: one engine, two modes

The engine edits a video from a `master_timeline.json`. Every value it needs comes from one resolved `Profile`.

| Mode | When | What happens |
|---|---|---|
| **Default engine** | the timeline has no `profile` (or `"default"`) | `profiles/default.toml` alone. Channel style gates and the optional tables (`[legibility]`, `[layout]`, `[print]`, `[sound]`, `[watermark]`, `[grade]`) are off. |
| **Profile-adaptive** | the timeline names `profile: "<channel>"` | `profiles/<channel>.toml` is merged over the default. The channel sets look, gates, draft rules and sound. |

Fallback is **per key, never per file**. A key the channel does not set takes the default value. A profile that
cannot be read, parsed or validated is an `EngineError` (exit 1): the engine never renders with a silently
substituted default profile, because the output would look right and be wrong.

Resolution order, lowest to highest (`video_engine/profiles.py`, `load` -> `_parse` -> `_override`):

1. Pydantic field defaults in `profiles.py` (a key `default.toml` does not write).
2. `profiles/default.toml`.
3. Files in the channel's `include = [...]`, in list order.
4. The channel file `profiles/<channel>.toml`.
5. Timeline `profile_overrides` (only the tables in `profiles.OVERRIDABLE`; `null` turns an optional table off).
6. Timeline `caption_style` keys and `audio_chain` loudness (`profiles.caption_style`, `profiles.loudness`).

Tables merge key by key; a list or scalar replaces the lower value whole. `audio.true_peak` and
`audio.tolerance_lu` may only be tightened (`_check_safety`).

Entry points: `profiles.of(tl)` for a timeline, `profiles.load(name, overrides)` otherwise. Do not read profile TOML
anywhere else.

## Profile contract

- Schema: the Pydantic models in `video_engine/profiles.py` (`ProfileData` and its tables) plus `LayoutProfile` in
  `video_engine/layout.py`. Every model forbids unknown keys. Key reference: [docs/PROFILES.md](docs/PROFILES.md).
- Three tables are untyped dicts and accept any key: `caption_style` (Python keys are checked by
  `_check_python_keys`; the rest go to the Rust `Style`, which rejects unknown keys only when captions compile),
  `framing` (checked by `framing.check_overrides`) and `reference_style` (only `overlap.allowed` is read). A typo in
  these tables is not caught at load time: check the spelling against the reader.
- Optional tables without field defaults: `[legibility]` and `[sound]` must be complete when present. A new channel
  that wants them includes a full table (copy `profiles/geoverum/legibility.toml`, `sound.toml`).
- Orientation: `[format] orientation`, else inferred as portrait when the profile has `[layout]`, `[print]` or
  portrait caption geometry. Lengths are px at the reference canvas (1080x1920 or 1920x1080, `video_engine/units.py`).
- `Profile.blake3` hashes the merged, effective profile. Splitting or reordering files with `include`, and deleting a
  key that only repeats a lower layer, leave it unchanged. A new field must stay out of the hash at its default
  (`Field(exclude_if=...)`, or a `dump.pop` in `_parse`), or every existing profile hash changes.
- `[draft] rules_file` points at `profiles/<channel>/creative.md`; each `- ` line is one prompt rule. Numbers quoted
  there must equal the TOML values (`tests/test_geoverum_style.py` and `tests/test_creative_workflow.py` tie them).
- Adding a channel: `profiles/<name>.toml` with only the keys it changes, optional `include` parts under
  `profiles/<name>/`, then `engine gates --profile <name>` and `pytest -q tests/test_profiles.py tests/test_profile_include.py`.

## Data flow

```
engine draft   inputs (voiceover, script, credits.txt, assets/) -> STT words (pinned)
               -> director.plan: 3 storyboard concepts, validated and scored      (AI request 1)
               -> draft.prompt + draft.schema -> model JSON                        (AI request 2..n)
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
| Timeline contract | `video_engine/timeline.py` | All times are integer frames; `sec_to_frame` is the only seconds-to-frames conversion. |
| Profile resolution | `video_engine/profiles.py` | Models, merge, include, overrides, hash. |
| Gates | `video_engine/gate_registry.py`, `video_engine/gates/` | The registry is the single definition; `gates/severity.py` decides fail / warn / info. |
| AI providers | `video_engine/ai/` | `base.py` (parts, retry, reply parsing, schema subset), `gemini.py`, `openai_compat.py`, `agent.py` (file handoff, exit 3). |
| Draft | `video_engine/draft.py`, `draft_scene.py`, `recipes.py`, `director/` | Prompt and schema are built from the effective profile (`draft._profile`). |
| Renderers | `video_engine/native.py`, `hyperframes.py`, `template/`, `hf/` | Python/JS twins must stay in step (`FIGURE_POP_S`, eases). |
| Native core | `rust/hypoverum_core_rs/` | Camera, `.ass`, silence, hashing. Bump `ALGO_VERSION` on a behaviour change. |
| Paths, limits | `video_engine/config.py` | Everything machine-specific; `VIDEO_ENGINE_*` in [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md). |

`video_engine/config.py` imports the Rust extension and reads `vendor/VERSIONS.json` at import time, so no engine
module imports without a built `hypoverum_core_rs` wheel ([CONTRIBUTING.md](CONTRIBUTING.md), environment setup).

## AI layer contract

- A provider implements `generate_json(task, parts, schema, request_id, attachments) -> AIResult` and `cleanup()`.
- Retry rules live in `ai/base.run_models` and are documented in [docs/AI.md](docs/AI.md). Do not add a retry loop
  elsewhere.
- Model output is untrusted. Order of checks: `parse_json_text` -> `schema_errors` (structure) -> `assemble`
  (meaning) -> Pydantic `Timeline` -> pre-flight gates. A new AI call must run `schema_errors` on the reply before
  it reads fields; the provider does it only for `agent`.
- `schema_errors` checks a subset: type, properties, required, items, enum, minItems, maxItems. Anything else
  (`minLength`, `additionalProperties`) is enforced by Pydantic later, not there.
- Prompts are assembled in code from the profile and the timeline settings. Take the frame format from
  `units(settings)`, never a literal "vertical" or "1080x1920".
- Never print or store an API key value; reports name the environment variable only.

## Conventions

- Errors a user can act on are `EngineError` with the file or segment named; the CLI catches it and exits 1. A raw
  `KeyError` or `ValueError` reaching the CLI is a bug.
- Tests come in pairs: `test_good_*` and `test_bad_*` for every gate and feature. A bug fix adds the failing case.
- Writes that another process may read are atomic (temp file in the same directory, then `os.replace`):
  `timeline.save`, `director.save_taste`.
- Cache keys are content hashes of everything that shapes the product. A behaviour change that the key does not
  see is a correctness bug, not a performance detail.
- Code, comments and error messages are English; team-facing guides are Indonesian ([docs/README.md](docs/README.md)).
- Line length is long by convention (about 120-140); match the file you edit.

## Known traps

- `profiles._parse` is memoised on the TOML texts only. `creative.md` is read inside it, so a long-lived process
  (tests, a batch) keeps the first version of the rules: call `profiles._parse.cache_clear()` after editing it.
- `[type]` is validated and documented as the base of the type scale, but no renderer reads it. Sizes that must
  agree (`[type] title_px`, `[theme] title_px`, `[layout.sizes] title`) are kept equal by hand.
- `[motion]` frame tokens (`micro_frames`, `overlay_frames`, `panel_frames`, `stagger_frames`) are exported to the
  scene but only `entrance`, `move`, `exit`, `entrance_scale` and `overlay_fade_s` change a render.
- `geoverum_landscape` shares `geoverum/creative.md`, whose caption and title numbers are the portrait ones.
- `profiles/taste/<channel>.json` is both tracked seed data and runtime state: a successful `engine draft` rewrites
  `recent`. Tests redirect it with `VIDEO_ENGINE_TASTE_DIR`.
- Tests in `tests/test_draft_scene.py` and `tests/test_recipes.py` need the flag assets under
  `VIDEO_ENGINE_OPEN_ASSETS`; without them they fail instead of skipping.

## Before you finish a change

```bash
python scripts/check_docs.py
ruff check --select E9,F63,F7,F82,F401,F841 video_engine tests scripts
pytest -q tests/test_<area>.py
```

A profile change also runs `engine taste geoverum --lint` and `pytest -q tests/test_profiles.py tests/test_profile_include.py tests/test_geoverum_style.py`.
