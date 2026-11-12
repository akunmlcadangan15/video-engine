# Sound sourcing

How music and sound effects reach a build.

**What this document answers**

- Which profile keys switch sound on, and what gate A02 enforces.
- How the sound cache, the agent request and the Epidemic Sound connector work together.

Music and SFX files for a profile with `[sound]` come from the sound cache (`video_engine/sound/`). The engine makes
no network calls and holds no keys; an agent fills the cache through a file handoff and the Epidemic Sound connector.

## Profile key

`[audio] require_source`: `"epidemic"` or absent (default: absent).

| Value | Resolver | Gate A02 |
|---|---|---|
| absent | SFX: cached Epidemic Sound file, else cached local-test file, else the generated local-test tone. Music for a brief: cached Epidemic Sound file, else cached local-test file | skip |
| `"epidemic"` | SFX: cached Epidemic Sound file, else the generated local-test tone. Music for a brief: cached Epidemic Sound file | fail on any non-Epidemic file |

Music without `audio_chain.music_brief` comes from `audio_chain.bgm_path`. A brief with no cached file is an
`EngineError` in the mixer; `ensure_sounds` runs first.

A `bgm_path` file is Epidemic Sound when a sidecar or a sound lock says so and the hash matches. See
[Path-supplied Epidemic music](#path-supplied-epidemic-music). Otherwise the mix marks it `local-test`.

`profiles/geoverum/core.toml` leaves `require_source` unset by default (Epidemic Sound is preferred when resolving `audio_chain.music_brief`, while `bgm_path` and cached local files remain allowed unless `require_source = "epidemic"` is enabled in `[audio]`).

## Cache

Root: `<VIDEO_ENGINE_CACHE>/sound/` (default `.cache/sound/`, gitignored).

- `files/<blake3>.<ext>`: audio file, content-addressed.
- `files/<blake3>.json`: sidecar `{type, kind | brief, file, duration_ms, provenance}`.
- `index/sfx-<kind>.json`, `index/music-<brief key>.json`: `{source: file name}`.
- Provenance: `{source, id, title, artist, credit}`; `source` is `epidemic` or `local-test`.
- Brief key: blake3 of the validated `music_brief` (mood lowercased and trimmed, duration rounded).
- A hit needs the index line, a non-empty file and the sidecar; it touches all three (LRU recency).
- `video_engine.cache.prune()` (LRU by size) covers `sound/`; an evicted file is a miss. Lookups and stores lease the
  index file, the audio file and the sidecar ([BUILD.md, Cache leases](BUILD.md#cache-leases)); eviction keeps leased files.
- Files a project sound lock still names are pinned and are not evicted. See [Pins](#pins).
- `video_engine.sound.cache.prune(dry_run=False, cache_dir=None)` (also run by `engine cache --prune`, paths
  reported as `sound/...`) removes index lines without a file, orphan files and sidecars,
  and `.tmp` files, except leased files (`skipped_leased`; a writer leases its `.tmp` file) and pinned files
  (`skipped_pinned`). It runs under the cache root's exclusive lock and reclaims lease files of dead processes
  (`reclaimed_leases`).

## Pins

A sound lock in `out/build_manifest.json` (or `out/draft/build_manifest.json`) stores `cache:` paths. LRU eviction
used to delete those files while the lock still named them, so the next build stopped and asked for `--relock`.

`video_engine.sound.pins` records each such file in `<cache>/sound/pins.json`, one owner per project manifest.
The owner lists `sound/files/<name>` plus the sha256 the lock stored. Eviction skips that audio file, its
`<stem>.json` sidecar, and any `sound/index/*.json` that names the file. `sound/pins.json` is skipped while it
exists. Pinned bytes still count toward `VIDEO_ENGINE_CACHE_MAX_MB`. When every remaining file is pinned or
leased, eviction stops even if the cache is over the cap: only unreferenced files are removed.

The same step copies each pinned file to `<project>/out/locked-sounds/<sha256><ext>`. `manifest.locked_sounds`
reads that copy when the cache file is gone and the bytes still match the lock. The copy is outside the shared
cache, so eviction does not remove it.

Recording:

- `engine build` reads the manifests on disk before it stores anything else, and `manifest.write` replaces the
  owner with the lock it just wrote.
- `engine cache pin --project P` (repeat `--project`) does the same read. `engine cache --prune --project P`
  refreshes pins before it deletes; `--dry-run` still refreshes pins and local copies and does not delete.
- A project that has not been built or pinned since this behaviour landed is not yet protected. Run
  `engine cache pin --project P` once while the cache files are still present. The command copies them into
  `out/locked-sounds/` as well. After that, a missing cache file is read from the copy and does not need
  `--relock`. If both the cache file and the copy are already gone, `--relock` is still required.

Release:

- The next eviction re-reads each owner's manifest. The owner is dropped when the manifest is missing,
  unreadable, or no longer lists that path and sha256. A file with no owners left is evictable.
- `engine cache unpin --project P` drops every owner of P immediately. The files under `out/locked-sounds/`
  stay, and the lock still accepts them by hash. Delete that directory to drop the fallback. The lock then
  needs the cache file or `--relock`.
- `--relock` keeps the previous pins until the new manifest is written, then replaces them with the files the
  new lock names.

API (`video_engine.sound.cache`): `resolve(kind, profile) -> ResolvedSound`, `resolve_music(brief, profile) ->
ResolvedSound | None`, `ResolvedSound(path, provenance)`, `missing(tl, profile)`, `put(...)`, `lookup(...)`,
`append_credits(project_dir, provenances)`.

## Request and response

`video_engine.sound.request.ensure_sounds(tl, project_dir, resume=False, resume_cmd=None)`:

- Missing items (`cache.missing`), only with a profile `[sound]`: with `require_source`, every SFX kind of the
  timeline and the music brief without a cached file from that source; without it, the music brief when no source
  has a file for it. Requested source: `require_source`, else `epidemic`.
- Nothing missing: returns `[]`.
- Missing items and no answer: writes `<project>/out/sound_request/` and raises `AwaitingAgent` (exit code 3).
- `resume=True` without `response.json`: `EngineError`.
- Valid `response.json` for the current request: imports every file into the cache, removes the bundle, returns
  the imported `ResolvedSound`s.

Build: `engine build P` calls `ensure_sounds` after pre-flight and before the mix plan (exit 3 with the bundle);
`engine build P --resume` (no `--ai-qa` needed) imports the answer and continues. After the mix plan the build
appends the credit lines of every mixed Epidemic Sound file to `credits.txt`; A02 runs on the final mix.

Sound-only CLI: `python -m video_engine.sound PROJECT [--resume] [--credits]` runs `ensure_sounds`, prints the resolved files,
and with `--credits` appends credit lines to `<project>/credits.txt`. Exit codes: 0 done, 1 error, 3 waiting.

Bundle (`out/sound_request/`, gitignored):

- `request.json`: `{request_id, required_source, items: [{slot, type: sfx|music, kind | brief}]}`.
- `request.md`: the task, the items and the resume command.
- `schema.json`: schema of `response.json`.
- `files/`: downloads go here.
- `response.json` (written by the agent): `{request_id, items: [{slot, file, source, id, title, artist,
  duration_ms}]}`.

Response rules (any violation: `EngineError`, nothing imported):

- `request_id` equals the request's; every slot answered exactly once; no other keys.
- `source` equals `required_source`; `id` and `title` non-empty.
- `file` is relative, inside the bundle, extension `.wav .mp3 .flac .ogg .m4a`.
- The measured duration is within 250 ms of `duration_ms`.
- SFX `artist` may be empty; the credit then names "Epidemic Sound".

## Answering a request with Epidemic Sound

Connector: MCP namespace `user-epidemic-sound`. Helpers: `video_engine/sound/epidemic.py`.

1. Read `request.json`.
2. SFX item: `SearchSoundEffects(epidemic.sfx_search_args(kind))`, then `epidemic.pick_sfx(epidemic.sfx_candidates(result), kind)`,
   then `DownloadSoundEffect({id, options: {fileType: "WAV"}})`.
3. Music item: `SearchRecordings(epidemic.music_search_args(brief))`, then
   `epidemic.pick_music(epidemic.music_candidates(result), brief)`, then
   `DownloadRecording({id, options: {fileType: "MP3", stemType: "FULL"}})`.
4. An error containing `RETRY_LATER`: wait `epidemic.retry_after(error)` seconds, retry, at most
   `epidemic.MAX_RETRIES` times.
5. `epidemic.asset_url(result)` is a signed URL. Download it into `files/` without printing, logging or storing
   it; log `epidemic.redact(url)` only.
6. Write `response.json` with `epidemic.response_item(slot, "files/<name>", candidate)` per item and
   `epidemic.write_response(bundle, request_id, items)`.
7. Run the resume command from `request.md`.

## Credits

Epidemic Sound files are credited in `credits.txt` under the header `Audio (Epidemic Sound)`, one line per file:

```
- <id> — <artist> — "<title>" — https://www.epidemicsound.com — Epidemic Sound licence
```

`append_credits` adds the header once and skips lines already present. Local-test files have no credit line.

## Path-supplied Epidemic music

`audio_chain.bgm_path` (and a manual SFX `asset`) is not a cache entry, so it has no cache sidecar. The mix
reads provenance in this order (`video_engine.sound.provenance.for_file`):

1. The first sidecar next to the audio file that has `"source": "epidemic"`. Paths, in order: `<stem>.json`,
   `<stem>.licence.json`, `<filename>.json`, `<filename>.licence.json` (`song.mp3` -> `song.json`,
   `song.licence.json`, `song.mp3.json`, `song.mp3.licence.json`).
2. A `sounds` entry in `out/build_manifest.json` or `out/draft/build_manifest.json` whose `sha256` equals the
   file and whose `provenance.source` is `epidemic`.

Required on an epidemic claim: `id` (the track id, non-empty) and `sha256` (hex digest of the file bytes).
`title` and `artist` are optional (`id` and `Epidemic Sound` fill the gaps). `credit` is optional; when it is
absent the credit line is the same one `append_credits` writes. Example beside `assets/bed.mp3`:

```json
{
  "source": "epidemic",
  "id": "5d1e8414-6c0a-3e7b-a992-e2fc2bb2ceb1",
  "title": "Rhythmia 1",
  "artist": "August Wilhelmsson",
  "sha256": "<sha256 of assets/bed.mp3>"
}
```

A claim whose `sha256` does not match the file is an error. When the sidecar and the lock both name a track
and the ids differ, that is an error. No claim leaves the file as project audio (`local-test` in a `[sound]`
mix, `project-file` when A02 looks at `bgm_path` directly). A verified epidemic file passes A02 when
`require_source` is `epidemic`, the same as a cache hit.

## Manual cues and rumble

`audio_chain.sfx` places extra cues (docs/TIMELINE.md). Kinds are `pop`, `tick`, `whoosh`, `click`, `beep`,
`counter_tick`, `impact`, `riser`, `rumble`. `rumble` is a low tone (local test: 45 Hz, low-passed, 1.4 s;
Epidemic search term `deep rumble`, at most 4 s). It has no default render-event mapping. A profile that maps
an event to `rumble` needs `[sound.kinds.rumble]`.

## Gate A02 (audio source)

`video_engine.gates.audio_source.a02_audio_source(tl, mix, require=None)`; `require` defaults to the profile's
`[audio] require_source`.

- No requirement: `skip`.
- Every music and SFX file in the mix (or `bgm_path` when there is no mix) has the required source: `pass`.
- Any other source (`local-test`, `project-file`): warn by default, listing each file. A path file whose
  sidecar or sound lock verifies `source=epidemic` plus the track id and the file hash counts as `epidemic`.

In the build: `audio_source.build_findings(tl, mix)` runs with the delivery gates A01 and S01 on the staged file; no finding
is emitted without `require_source`. Because A02 is in `LINT_GATE_IDS`, a mismatch warns by default and blocks the build (exit 1, nothing published, report in `out/build_report.failed.json`) when `A02` is listed in `gates.require`.
