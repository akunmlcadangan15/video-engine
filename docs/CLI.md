# CLI reference

Every `engine` command with its options and exit codes.

**What this document answers**

- What a command does, its options and its exit code.
- How the AI options (`--provider`, `--resume`) work for the commands that take them.
- Where the gates, build and profile details live (they are linked from the commands).

All commands take the project directory.
Exit code 0 = success, 1 = gate failure or engine error, 2 = usage error, 3 = waiting for an agent response (agent provider wrote a request bundle).

AI options (`draft`, `director`, `preflight --ai-qa`, `build --ai-qa`): `--provider NAME` (`agent`, optional `gemini`, or a `[providers.NAME]` entry; none selected = `agent`, the model that runs the engine answers the request bundle), `--resume` (agent provider: read the pending bundle's `response.json`).

## `engine validate P`

Schema check only.

## `engine preflight P [--captions] [--no-dom] [--ai-qa [--provider NAME] [--resume]] [--write-fixes] [--mode release|draft]`

All pre-flight gates without rendering.
`--captions` transcribes the voiceover for caption gates.
`--write-fixes` writes applied fixes back into the timeline. Camera intensity is only changed after segment `creative_autofix` or native `camera_move.auto_intensity` opt-in; caption placement retains its existing technical fixes.
`--mode draft` reports a fail of every technical gate that a profile may make advisory as a warning (see [Draft builds](GATES.md#draft-builds)); the default `release` changes nothing.

## `engine build P [--no-cache] [--relock] [--label L] [--timing-tsv F] [--ai-qa [--provider NAME]] [--resume] [--keep-workdir] [--mode release|draft] [--quality delivery|proxy] [--repin]`

Full build.
Alias: `engine render`.
Exit 0 when the build published `out/final.mp4`; exit 1 on any gate fail or stage error (nothing published, `out/build_report.failed.json` written).
Delivery-quality builds with missing music or SFX of a profile `[sound]` (docs/SOUND.md): write `P/out/sound_request/` and exit 3;
  `--resume` (no `--ai-qa` needed) imports the agent's `response.json` and continues the build.
`--no-cache` deletes the files of the cache entry directories (`words/`, `segments/`, `maps/`, `models/`, `dom/`) first, except files leased by a running build (printed as `cache: kept N files leased by a running build`);
  the build repopulates them.
When the build's cache evictions deleted or skipped files, the command prints `cache: evicted N, skipped M leased` with one `skipped (leased): <path>` line per skipped file and the reclaimed stale leases.
Other directories under the cache root are kept.
`--timing-tsv` appends per-stage wall times.
`--mode draft` is a trial build: technical gates that a profile may make advisory only warn, and the output goes to `out/draft/final.draft.mp4` with its own `build_report.json` (marked `"mode": "draft"`) and `build_manifest.json`; `out/final.mp4` and its report stay untouched and `engine deliver` refuses a draft (see [Draft builds](GATES.md#draft-builds)).
Proxy does not create or import a sound handoff; cache the files first with `python -m video_engine.sound P` (resume that command after supplying the response). Proxy and delivery both append missing Epidemic Sound credit lines to `credits.txt`.
`--quality delivery` is the default and preserves the profile render settings. `--quality proxy` requires `--mode draft`: cheaper capture/encoding at unchanged dimensions and fps, output confined to `out/draft/`, separate visual cache keys, and effective settings recorded in report/manifest. It cannot be delivered or combined with `--repin`; it does not pin words back into the source timeline. See [Proxy render quality](BUILD.md#proxy-render-quality).
`--repin` pins the caption words again before the pre-flight when they are stale (gate W01); it runs the same step as `engine words`. Without the flag a stale pin fails the build.
`--relock` drops the sound lock of `out/build_manifest.json` and resolves music and SFX again (see [Build manifest](BUILD.md#build-manifest)).

## `engine preview P --seg N --time S [--out F] [--timeout S]`

One PNG of segment N at S seconds from segment start, no segment render. `--timeout`: HyperFrames DOM probe limit (default 120 s).

## `engine preview P --all [--out DIR] [--sheet [--out F.png]]`

The middle frame of every segment as PNGs (default dir `$VIDEO_ENGINE_SHM/preview/<project_id>/`);
  `--sheet` also assembles a contact sheet (default `out/preview_sheet.png`) without rendering the video.

## `engine draft-preview P [--segments N] [--out F] [--no-cache]`

Engine draft preview: renders the first N segments (default 5) with the normal segment renderers and joins them with the concat demuxer in stream-copy mode (`-c copy`). No composite and no final encode. After the picture is joined, audio is muxed when the timeline has any: the `[sound]` mix (narration, music and SFX, including `audio_chain.sfx`) when that mix can be planned from files already on disk, otherwise the narration (and `bgm_path`, through the simple voiceover/bed graph). A timeline with neither stays silent. The audio is AAC at the profile bitrate, cut to the preview length; the video packets are copied. `preview_report.json` records `audio` as `mix`, `narration` or `none`. The file goes to `out/draft/preview.mp4` and is never deliverable (`out/final.mp4` stays untouched). Segments whose stream parameters differ are rejected instead of re-encoded. Wrapper: `scripts/engine-draft` (same lock/disk/env guards as `engine-build`).

## `engine audio P [--no-cache]`

Prints caption words with frame ranges. Writes nothing.

## `engine words P [--retranscribe] [--print]`

Transcribes the voiceover (words cache unless `--retranscribe`), applies `caption_fixes`, writes `captions` + `captions_meta` into `master_timeline.json` (atomic replace).
Run after changing the voiceover, `caption_fixes`, `stt.model`, `stt.keyterms`, `language` or `fps` (gate W01 names the change).

## `engine deliver P --dry-run [--final F]`

Prints the delivery plan as JSON. No upload exists.

## `engine deliver P --export DIR [--final F]`

Writes the upload-ready tree (video or byte-split parts, JOIN, credits, SRT from pinned words, middle-frame thumbnail, YouTube Shorts / Reels metadata). A landscape project gets normal YouTube metadata (platform `youtube`, `shorts: false`, no `#Shorts`, no Reels block) and a 1280x720 thumbnail.
No upload; the Projects Manager bot handles Google Drive delivery.

## `engine draft P [--out F] [--force] [--max-repairs 0-2] [--no-director] [--review [--review-rounds 1-2]] [--video-proxies N] [--voiceover F] [--script F] [--no-dom] [--provider NAME] [--resume]`

The AI provider drafts a timeline (see [Draft](AI.md#draft)).
Writes `master_timeline.draft.json` (or `.rejected.json` when gates still fail after the repair rounds) and `draft_report.json`.
Never overwrites `master_timeline.json` unless `--out` points there and `--force` is given.
`--no-director` skips the storyboard stage; `--review` adds the review pass (see [Director stage](AI.md#director-stage-engine-draft)).

## `engine director SRC [--start S] [--seconds N] [--provider NAME] [--resume] [--bundle-root D]`

Footage analysis of a 360p 5 fps proxy by the AI provider.
Prints `provider`, `model`, `tried`, `usage`, `data` as JSON.
Agent bundles go under `--bundle-root` (default `<VIDEO_ENGINE_SHM>/ai_request`: `/dev/shm/video_engine/ai_request` on Linux, `$TMPDIR/video_engine/ai_request` on macOS).

## `engine pauses P [--threshold-db D] [--min-pause S] [--min-seg S] [--max-seg S] [--json]`

Narration pauses (Rust energy detector on 16 kHz mono PCM) as half-open frame ranges, plus suggested segment boundaries at pause midpoints with segment lengths in `[min-seg, max-seg]` seconds (defaults 0.2 / 2.0 / 6.0).

## `engine doctor [--install]`

Checks the tools a build will use on the host: Node, HyperFrames, puppeteer-core, three, chrome-headless-shell, ffmpeg (major version >= 7, with the `subtitles`, `loudnorm` and `sidechaincompress` filters and the `libx264` and `aac` encoders), and the related asset roots.
Resolution is machine-first (see [ENVIRONMENT.md](ENVIRONMENT.md)): an environment override, then global npm roots, `PATH`, platform cache/data directories and the user toolchain directory, then the repo `vendor/` tree as a fallback.
Each line reports found or missing, the resolved path, and the source; a missing tool lists the locations that were searched.
Exit 1 only when a required tool is missing or ffmpeg fails the filter/encoder or major-version check. Optional roots (downloads, Python, Modal) are `[WARN]` when absent and do not change the exit code.

`--install` installs the latest release of each *missing* tool into `<user-data>/video-engine/toolchain` (not the lockfile in `vendor/`). Node is the current official LTS tarball with a SHA-256 check; HyperFrames, puppeteer-core and three are `npm install <name>@latest`; chrome-headless-shell is the latest build from `@puppeteer/browsers`. ffmpeg is never installed by this command.
When the HyperFrames policy is `pinned` (`VIDEO_ENGINE_HYPERFRAMES_VERSION=pinned` or `engine toolchain use pinned`), `--install` instead restores the lockfile pins from `vendor/VERSIONS.json` and `vendor/npm/package-lock.json` into `vendor/`.

The `hyperframes-active` line names the HyperFrames the build renders with (see `engine toolchain`). A `[WARN]` appears only when the policy is `pinned` and the installed package is not the pin; it does not change the exit code.

## `engine toolchain [show | list | use SPEC]`

Shows or switches the HyperFrames version the build renders with. Node, Chrome, puppeteer-core and three are resolved independently by the same machine-first discovery (they are not locked to the HyperFrames policy unless an environment override says so).

SPEC:

- `auto` (default when unset): use a HyperFrames already on the host (globals, `PATH`, user toolchain, then `vendor/`).
- `pinned`: the `vendor/VERSIONS.json` pin in `vendor/npm` (opt-in).
- `latest`: the newest version already installed (machine, pin, or `vendor/hf/*`); `use latest` installs the newest npm release into `vendor/hf/X.Y.Z` first if needed.
- `system`: a HyperFrames found outside `vendor/` (global npm or `PATH`).
- an exact `X.Y.Z`: the pin tree when that is the pin, otherwise `vendor/hf/X.Y.Z`.

`use` writes the choice to `vendor/hyperframes.select`. `VIDEO_ENGINE_HYPERFRAMES_VERSION` overrides that file.
`list` prints the pin, any `vendor/hf` copies, and the system install. Exit 1 when the active version is not installed.
The active version and source are part of every HyperFrames segment cache key, so a switch rerenders HyperFrames segments. `build_report.json` records the versions and paths actually used under `toolchain` (`hyperframes`, `hyperframes_source`, `hyperframes_setting`, `hyperframes_pinned`, plus `node`, `node_path`, `chrome`, `puppeteer-core`, and the corresponding pin fields).

## `engine clean [P ...] [--cache]`

Deletes only the scratch dirs of the given projects under the scratch root (never another build's), `P/ai_request/` of every given project, and with `--cache` the files of the cache entry directories `words/`, `segments/`, `maps/`, `models/`, `dom/`, except leased ones (one `kept (leased by a running build): <path>` line each).
With no project it removes no scratch.

## `engine cache --prune [--project P ...] [--dry-run]`

Deletes `words/`, `segments/`, `maps/`, `models/`, `dom/` entries of the cache that are not referenced by the current keys of the projects (segment renders and, when the render is cached, its graded file; map derivatives and deep tiles; model prop sprites; DOM probe results of the current HyperFrames segment keys) (default: every project under `examples/` when present, or pass `--project P`).
Also deletes broken sound cache entries (`video_engine.sound.cache.prune`; reported as `sound/...`, rewritten index files in `sound_rewritten`).
Other directories under the cache root are not touched.
Files leased by a running build are kept and listed in `skipped_leased`; lease files of dead processes are deleted and listed in `reclaimed_leases`.
Sound files pinned by a project lock are kept (`skipped_pinned`). `--prune` refreshes those pins from each project's manifests before it deletes. `--dry-run` lists deletions and still refreshes pins and the `out/locked-sounds/` copies; it does not delete cache files.
A project that fails to load stops the prune (exit 1, nothing deleted).

## `engine cache --pin --project P [--project P ...]`

Reads `P/out/build_manifest.json` and `P/out/draft/build_manifest.json` and pins every sound-cache file those locks name. Copies each file to `P/out/locked-sounds/<sha256><ext>`. Exit 2 without `--project`. See [SOUND.md, Pins](SOUND.md#pins).

## `engine cache --unpin --project P [--project P ...]`

Drops every sound-cache pin owned by P. The next LRU eviction may delete those cache files. Copies under `P/out/locked-sounds/` stay until deleted; the lock still accepts them by hash. Exit 2 without `--project`.

## `engine import-shots CSV DST --search DIR [--search DIR ...] --credits F --voiceover F [--manifest F] [--substitute OLD=NEW ...] [--exclude SHOT ...] [--watermark-checked] [--project-number N] [--topic T] [--date D] [--format F] [--force]`

Builds a project (`master_timeline.json`, `credits.txt`, `assets/`) from a shot-list CSV (`shot,type,file,title,source_url,author,licence,on_screen_s`) of an existing edit.
Sources are only read.
`--format` sets the output size: `short` (1080x1920, default), `short-4k`, `landscape-1080p`, `landscape-1440p` or `landscape-4k` (`units.FORMATS`); a landscape project needs a landscape profile such as `geoverum_landscape`.
Credits come only from CSV or manifest rows; a row without `source_url` is an error.
`--substitute` takes the new file's credit from the manifest (no row = error).
`--exclude` merges a shot into the previous one.
Map rows become `map_pins` HyperFrames segments with empty pins (add pins before building); stills alternate zoom in/out.
Refuses an existing timeline without `--force`.

## `engine taste CHANNEL [--like WHAT --why WHY | --reject WHAT --why WHY] [--check "KEY OP VALUE" ...] [--lint [--profile NAME]]`

Prints the channel taste store (`profiles/taste/<channel>.json`, or `$VIDEO_ENGINE_TASTE_DIR/<channel>.json`).
`--like` / `--reject` appends an entry with its reason.
`--check` (repeatable, only with `--like` / `--reject`) attaches a condition that the channel profile has to satisfy for the entry to be true, for example `"caption_style.size == 84"`.
`KEY` is a dotted path into the merged profile, `OP` is one of `==` `!=` `<` `<=` `>` `>=` `in` `exists`, and `VALUE` is JSON (`84`, `true`, `[84]`, `"epidemic"`).
`--lint` compares the checks of all entries with the profile named like the channel (or `--profile NAME`) and exits 1 on a mismatch, a missing key or a malformed check.
Entries without a check and the free-text `rules` cannot be checked by a program; `--lint` only counts them.


## `engine profile [NAME] [--origin] [--table T]`

Read-only. Without a name: every profile with the first 16 characters of its hash and its description.
With a name: the profile as the engine resolves it (model defaults < `default.toml` < `extends` chain < `include` files < the profile's file), as JSON; the full hash goes to stderr.
`--origin` prints one line per key instead: the value and the profiles-relative file that set it, `[type] <size> (<file>)` for a size that reached the surface through the type scale, or `(engine default)` for a key no file writes.
`--table T` limits the output to one top-level table (`theme`, `caption_style`, `gates`, ...).
Exit 1 for an unknown profile or table, or a profile that does not validate (the error names the file and key).

## `engine schema timeline|profile [--out F]`

Prints the JSON Schema of `master_timeline.json` or of a profile, generated from the Pydantic models that validate them (`timeline.Timeline`, `profiles.ProfileData`), so it is always the contract the engine enforces.
`--out F` writes it to a file.

## `engine gates [ID ...] [--profile NAME] [--layer L] [--kind technical|style] [--format table|json|md] [--stats PATH ...]`

Read-only. Lists the gates of the registry (`video_engine/gate_registry.py`): id, class, layer, name and rule.
Gate ids as arguments print the full entry of each gate.
`--profile NAME` adds what a failing gate does under that channel profile: `off` (its table is absent), `warns`, `blocks`, `on request` (`AIQA`, only with `--ai-qa`) or `meta`.
`--layer` and `--kind` filter the list; `--format json` prints machine-readable rows.
`--format md` prints the generated gate index that docs/GATES.md embeds; it takes no filters.
`--stats PATH ...` counts fails, warns and waived findings per gate in build reports.
A `PATH` is a report file, a project folder (reads `out/build_report.json` and `out/build_report.failed.json`), an `out/` folder, or a folder of projects.
The summary line counts reports that were ok, failed on a gate, or failed with an engine error; gates that never appear in a finding are listed at the end.
Exit 0 when something was listed; exit 1 for an unknown gate, layer or path, a conflicting option, or no report found.

## `engine sheet P [--video F] [--out F] [--cols N]`

Contact sheet `out/sheet.png`: one tile per segment (middle frame, 1/5 size), labelled `seg N HF|N mm:ss.f`.

## `engine batch [P ...] [--queue F] [--fail-fast] [--label L] [--no-cache] [--relock] [--mode release|draft] [--quality delivery|proxy]`

Builds projects in queue order, one lock per project (`<project>/out/.batch.lock`).
A failed item does not stop the queue unless `--fail-fast`.
The queue is a JSON array, a JSON object with `projects`, or one path per line (relative to the queue file).

## `engine assets [--query WORDS] [--kind KIND] [--limit N] [--json] [--copy PATH --project P] [--open-assets DIR] [--stock-root DIR ...]`

Searches the local `open-assets/catalog.csv` and stock/B-roll index (files must exist), or copies one indexed file into `P/assets/` and prints its `credits_manifest` row.
No network request.

## `engine block chart|timeline|infographic --data JSON_OR_@FILE --out PNG [--width W] [--height H] [--colour HEX] [--background HEX]`

Renders a deterministic infographic PNG (transparent by default) for use as a still, prop or layer.

