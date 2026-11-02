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

`profiles/geoverum.toml` sets `require_source = "epidemic"`. A geoverum project needs `audio_chain.music_brief`; music
from `bgm_path` fails A02.

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
- `video_engine.sound.cache.prune(dry_run=False, cache_dir=None)` (also run by `engine cache --prune`, paths
  reported as `sound/...`) removes index lines without a file, orphan files and sidecars,
  and `.tmp` files, except leased files (`skipped_leased`; a writer leases its `.tmp` file). It runs under the
  cache root's exclusive lock and reclaims lease files of dead processes (`reclaimed_leases`).

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

## Gate A02 (audio source)

`video_engine.gates.audio_source.a02_audio_source(tl, mix, require=None)`; `require` defaults to the profile's
`[audio] require_source`.

- No requirement: `skip`.
- Every music and SFX file in the mix (or `bgm_path` when there is no mix) has the required source: `pass`.
- Any other source (`local-test`, `project-file`): `fail`, listing each file.

In the build: `audio_source.build_findings(tl, mix)` with the delivery gates A01 and S01 on the staged file; no finding
without `require_source`; a fail fails the build (exit 1, nothing published, report in `out/build_report.failed.json`).
