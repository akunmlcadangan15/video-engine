# Build pipeline

What `engine build` does, in order, and how it stays repeatable.

**What this document answers**

- The stages from captions to publish, and when a build publishes or fails.
- What `build_manifest.json` and `build_report.json` contain.
- How the worker budget, stream copy and concat, and cache leases work.
- What a final build on Modal changes in the published report, and what stays the same.

1. Captions: pinned timeline words;
     else BLAKE3-cached Grok STT words (`grok-voice-transcribe-2.0`, 16 kHz mono WAV, word timestamps) + `caption_fixes`, pinned into `master_timeline.json` (fresh copy of the file, so in-memory gate fixes are not written).
2. Pre-flight (layers 1-3, caption placement, profile style gates).
3. Sounds (profile `[sound]`), after pre-flight, before the mix plan and the segment render: `sound.request.ensure_sounds` requests missing files (bundle `out/sound_request/`, exit 3; `--resume` imports the answer);
     after the plan the Epidemic Sound credit lines of the mixed files are appended to `credits.txt` (docs/SOUND.md).
4. Build manifest `out/build_manifest.json` written (see [Build manifest](#build-manifest)).
   Segments in parallel within the worker budget (see [Resources](#resources)): native pool `native_workers` x 2 x264 threads (`X264_REPEATABLE`, so the bitstream repeats when the CPU is busy);
     HyperFrames segments in a pool of `hf_parallel` (see [Resources](#resources); output order is segment order), `hf_workers` capture workers each (`VIDEO_ENGINE_HF_WORKERS` overrides), screenshot capture on SwiftShader (`--no-browser-gpu`, one Chrome raster thread), default capture profile `-q standard --crf 14` (JPEG q100 screenshots, x264 medium intermediate; profile `[render] hf_quality` / `hf_crf` and `HF_QUALITY` / `HF_CRF` are part of the segment cache key; Geoverum uses `high` at CRF 8);
     loudnorm pass 1 runs concurrently.
   Every segment is checked for its exact frame count.
   Results cached by a BLAKE3 hash of sources + config.
5. `.ass` with placement overrides.
6. Three cached passes:
   - composite (cache kind `composite`): segment join (Stream-copy and concat rules) + transition clips + caption burn + profile watermark overlay (PNG drawn with Pillow), exact frame count, no audio, `mux_threads` x264 threads with `X264_REPEATABLE`;
   - master audio (cache kind `audio`, AAC m4a): mix (ducking, loudnorm pass 2) and the A01 corrective gain passes, on the audio file alone.
     AAC bitrate is `[render] aac_bitrate` when set, else `[audio] aac_bitrate`, else 192 kbps (allowed 64-320). The same value is used on the corrective gain passes. It is part of the audio cache key.
     The mix is cut to exactly duration x 48000 samples after ducking and before loudnorm, and again after it, so every render of the same inputs has identical PCM.
     With a profile `[sound]` the mix is voiceover + music (gain, fades, keyframed duck under speech) + each SFX cue (trimmed to `max_s`, at its `gain_db`, delayed to `time_s`) summed without normalisation before the same loudnorm and limiter, so the final file meets the profile loudness target;
   - mux: stream copy of the composite video and the master audio (no re-encode).
   Each cache key is the ffmpeg command with every input path replaced by its sha256 (`manifest.cmd_key`): an audio-only change re-runs only the audio pass and the mux;
     a visual or caption change re-runs the composite and the mux.
   Transition clips are redrawn from both neighbouring segments on every build and written bit-exact, so the composite key follows both neighbours.
   Every stage key also holds the engine source that shapes its product (docs/RUST.md, Cache-key rules).
7. Post-render pixel QA, A01 loudness gate, profile S01 silence gate, profile A02 audio source gate, all on the staged file.
   The mux writes into a unique staging dir `out/.final.staging-*/` on the output's filesystem; it is removed on every exit path.
8. Publish, only when no stage raised and no kernel gate (error) reported fail: `os.replace` of the staged file onto `out/final.mp4`, then of `build_report.json` (written through a temp file in `out/`);
     a stale `build_report.failed.json` is removed.
   Any failure leaves `out/final.mp4` and `out/build_report.json` byte-identical and writes `out/build_report.failed.json`: the full report with `"ok": false`, `"final": null` and `error` after a gate fail, or `ok`, `final`, `error` and `timings` after a stage error.
   Library API: `build.build()` raises `build.BuildFailed` (an `EngineError` carrying `result` and `report`) on a kernel fail, or with `raise_on_fail=False` returns a `BuildResult` with `ok=False`, `final=None`, `failed` (the fail lines) and `report`;
     stage errors raise their exception.
9. `build_report.json`: `ok`, `build_id` (same as the manifest), `final_sha256`, profile (`name`, `blake3` of the merged profile), segments (mode, cache hit, seconds), preflight findings, `preflight_fixes`, QA, loudness (with `measured_lra`), captions (`events`, `per_10s`, `mean_s`), timings, `resources` (`budget` and per stage `wall_s`, `cpu_s`, `peak_rss_mb` with per-tool peaks), `stage_keys`, `preflight_subtimings` (`layer2_dom`: seconds summed over HyperFrames segments of `build`, `browser_load`, `r63_rects`, `snapshots`, `bg_screenshots`, `glyph_masks`, `r64_numpy`; `probes` and `cache_hits` of the DOM probe cache, cache kind `dom`), `gates` (see [GATES.md](GATES.md)), `sound` with a profile `[sound]` (`music` provenance, `music_file`, `ducking`, `sfx` provenance per asset id, `cues`), `cache` (the build's lease session: `leased` file count, `evicted`, `skipped_leased`, `reclaimed_leases`; paths `<cache root>/<path>`), and `sfx_events` when the scenes have sound cues: one `{kind: "counter_tick", frame (absolute), seg, figure}` per frame on which a counter's shown text changes.

## Publish with warnings

Lint gates (see [GATES.md](GATES.md)) do not block a publish. `out/final.mp4` is published during any build that has no kernel fail and no stage error, even when lint findings are present; the warnings stay in the published `build_report.json`.
`build_report.json` is always full: `gates` records `{required, segment_intents, fails, warns, waived}` (plus `waivers` when active) while auto-repaired findings are listed in top-level `preflight_fixes`.
A build with a kernel fail or a stage error publishes nothing and leaves the previous `final.mp4` untouched (see step 8).

## Proxy render quality

Policy and rendering cost are independent. `engine build P --mode draft` keeps the profile's delivery settings. For a cheap iteration use:

```bash
engine-build P --mode draft --quality proxy
```

`proxy` keeps the source dimensions, fps, coordinates and narration timing. It overrides only build-local render settings: HyperFrames draft/CRF 28, native intermediate and final x264 ultrafast/CRF 28, tune none, 8-bit output, no VBV cap, map JPEG quality 85. It is not a colour/quality proof or a delivery export. `delivery` (the default) means the existing profile settings, not a new fixed preset.

Proxy requires draft mode, confines output to `P/out/draft/`, does not pin captions into the master timeline, and rejects `--repin`. Pin words separately when needed. Proxy requires the sound files to be cached already (`python -m video_engine.sound P`, then `--resume` after the response). It does not import a sound handoff. It does append missing Epidemic Sound credit lines to `credits.txt`, the same write the delivery build uses. It does not bypass licence, decode, frame-count, audio or explicit-moving freeze checks. The source timeline/profile remain unchanged; resolved values are recorded as `quality`, `render_quality` and `source_profile` in report/manifest. Effective render configuration changes visual cache keys; audio may be reused when its inputs are unchanged. Existing per-segment/DOM caches and separate audio/composite passes remain in use; this is not a new full dependency scheduler.

## Build manifest

`out/build_manifest.json`, written before any segment renders: `build_id` (random hex, also in `build_report.json`), `timeline` (sha256), `profile` (`name`, `blake3`), `inputs` (every referenced file), `fonts`, `sounds` (music with `brief_key` and provenance, SFX per kind), `toolchain` (ffmpeg version, x264 args, vendor pins, engine version), `seeds`, `resources`, `stage_keys` (composite and audio cache keys).
File entries are `{path, sha256}`; `path` is relative to the project, `cache:<rel>` under the cache root, `engine:<rel>` under the engine checkout, else absolute.
A later build reuses the locked sound files (sha256 checked) without asking the sound resolver.
  Those cache files are pinned so LRU eviction does not delete them, and a copy is kept at `out/locked-sounds/<sha256><ext>`.
  A locked file that changed, or that is gone from both the cache and `out/locked-sounds/`, is a stage error naming `--relock`;
  a lock that does not cover the timeline (new SFX kind, changed music brief) is ignored.
The same inputs give the same final file bytes.

## Resources

One budget `workers` (`VIDEO_ENGINE_WORKERS` > profile `[resources] workers` > auto = clamp(min(CPU count, RAM GB // 1.8), 2, 64)) caps all stages: `native_workers = max(1, workers // 4)` (2 x264 threads each, `X264_REPEATABLE`), `hf_workers = max(1, workers // 4)`, `mux_threads = clamp(workers - 2, 1, 6)`.
`hf_segments` = 2 when `2 x hf_workers x 2 <= workers`, else 1. A build renders `hf_parallel` HyperFrames segments at once: `hf_segments` when `min(native segments, native_workers) x 2 + hf_segments x hf_workers x 2 <= workers`, else 1. On 8 workers: 2 for a timeline without native segments, 1 otherwise.
Each stage's wall time, CPU time and peak RSS (engine plus children, with per-tool peaks) go to `build_report.json` -> `resources`.

## Stream-copy and concat rules

- One eligibility function (`video_engine/streams.py`) decides every stream copy and every concat-demuxer join.
  Files are joinable when they have the same stream kinds in the same order and every compared parameter is equal.
- Video parameters compared: `codec_name`, `codec_tag_string`, `profile`, `level`, `width`, `height`, `pix_fmt`, `sample_aspect_ratio`, `r_frame_rate`, `avg_frame_rate`, `time_base`, `field_order`, `color_range`, `color_space`, `color_transfer`, `color_primaries`, `chroma_location`, `bits_per_raw_sample`, `has_b_frames`, `refs`, `extradata_hash` (MD5 of the SPS/PPS), `rotation`.
- Audio parameters compared: `codec_name`, `codec_tag_string`, `profile`, `sample_rate`, `channels`, `channel_layout`, `sample_fmt`, `bits_per_raw_sample`, `time_base`, `extradata_hash`.
- Engine segment format: the video stream of a 2-frame encode with `config.X264` at the timeline width, height and fps.
  A native source is stream-copied only when its video stream equals the engine segment format;
    the copy keeps the source edit list and carries `"copy": "copy-edit-list"` in its segment key.
- Every segment file (native copy, native encode, camera, HyperFrames) is checked after rendering: decoded frame count equal to the segment frame count, `start_time` 0 and duration `frames / fps`, each within half a frame.
  A native copy that fails the parameter or timing check is re-encoded;
    HyperFrames output passes through unchanged only when it is H.264 yuv420p at the timeline size with `r_frame_rate` = `avg_frame_rate` = fps and passes the timing check, else it is re-encoded;
    any other failure is a stage error.
- Final mux: joinable segments are joined with the concat demuxer;
    other segments are decoded as separate inputs, retimed to `1/fps` with `setpts=N` and joined with the concat filter.
  `build_report.json` -> `join` holds `method` (`concat demuxer` or `concat filter`) and `differences` (one `<segment file>: v0.<parameter>: a != b` entry per mismatch against the first segment).
- The output of the final mux and of the gain pass is checked for frame count, start and duration;
    the gain pass also fails when its copied video stream differs from its input in any compared parameter.


## Cache leases

- Every engine command (`cli.main`) and every `build.build()` call runs in one lease session (`video_engine.cache.session()`, re-entrant; the outermost exit releases).
- Inside a session, each cache file the process resolves or writes (words, segments, maps, their temp files, sound files, sidecars and index files, local test tones) is leased by `cache.acquire(path)` before the process checks it exists;
    the lease holds until the session exits on success, failure, exception or AwaitingAgent.
- Lease file: `<cache root>/.leases/<pid>-<start>.json` = `{"pid", "start", "paths": [paths relative to the cache root]}`; `start` is field 22 of `/proc/<pid>/stat`.
  Written atomically under a shared `flock` on `<cache root>/.lock`.
- Eviction holds the exclusive lock while it scans and deletes: LRU `cache.prune()` (after every `put_file` and sound `put`), `cache.clear()` (`--no-cache`, `clean --cache`), `engine cache --prune` and `sound.cache.prune()`.
  Each keeps every leased file and reports it (`skipped_leased`).
- A lease file whose pid is gone or runs a process with another start time is deleted by the next eviction (`reclaimed_leases`); its files become evictable.
- LRU eviction also keeps the entry being stored, so an entry larger than `VIDEO_ENGINE_CACHE_MAX_MB` stays in the cache until a later eviction.
- LRU eviction also keeps sound files pinned by a project lock (`<cache>/sound/pins.json`; [SOUND.md, Pins](SOUND.md#pins)), reported as `skipped_pinned`. A pinned file is not deleted to get under the cap. Pins are released when the manifest no longer names the file, when the manifest is removed (on the next eviction), or by `engine cache unpin --project P`. A copy under `<project>/out/locked-sounds/` is what the lock reads if the cache file is already gone.
- Outside a session `acquire` does nothing and eviction treats every file as unleased.

## Remote final build

`scripts/engine-build-modal PROJECT` runs this same pipeline (`engine build`, release mode, delivery quality) inside one ephemeral Modal container. Flags, the upload closure, cost controls and troubleshooting are in [Remote final build](ENVIRONMENT.md#remote-final-build). Draft and proxy builds stay on `scripts/engine-build`.

```bash
scripts/engine-build-modal PROJECT --dry-run
scripts/engine-build-modal PROJECT --cpu 8 --memory 16384 --timeout 10800
scripts/engine-build-modal PROJECT --fetch RUN_ID
```

`--cpu N` and `--memory MB` are the container's `(request, limit)` tuples, both ends equal to the flag. The default is CPU only (`--gpu none`); `--gpu L4` is the only GPU. Disk size is omitted unless `--disk` or `VIDEO_ENGINE_MODAL_DISK_MB` is set (524288–3145728 MiB); otherwise the container uses Modal's default disk. An out-of-range value exits 2 before upload. `min_containers=0`, `scaledown_window=2`, `single_use_containers` and `retries=0` keep the container from lingering or retrying. `--timeout` (default 10800 seconds) is a hard stop. The run directory on the volume is deleted after a successful fetch unless `--keep-volume` is set. Content-addressed input blobs remain so an unchanged file is not uploaded again.

Locally, before the upload: `engine validate`, `engine preflight`, then the closure (timeline files, the sound lock in `out/build_manifest.json` and `out/locked-sounds/`, caption inputs, profile files). The timeline file is not rewritten. Library roots are remapped with `VIDEO_ENGINE_*` inside the container.

The remote process is `engine build /work/project`. It runs the same gates and the same QA as a local final build. After it returns, `out/build_report.json` (or `out/build_report.failed.json`) and `out/build_manifest.json` gain a `remote` object, then the outputs are split into chunks and downloaded. Each chunk and the assembled file are checked with sha256; the destination is replaced with `os.replace`.

`remote` records the container uname (`host`), `image_id` / `image_digest`, the CPU request and limit and the observed CPU count, the memory request and limit and peak cgroup RSS, wall time, the timeout, the toolchain versions `engine doctor` reported in the container, any mismatch against the local doctor snapshot, and the request-based cost products (`cpu_request_core_seconds`, `memory_request_gib_seconds`, `gpu`). `PROJECT/out/modal_fetch.json` is a local receipt written after that verification. It is not an input to the next build.

### Known differences

- The container is Linux x64. A build on macOS is already not byte-identical to a Linux build (see [Platforms](ENVIRONMENT.md#platforms)); a remote build follows that rule. The same gates and the same QA thresholds run either way.
- FFmpeg in the image is the static GPL build pinned by `vendor/ffmpeg-static.json` (8.1, libx264, the filters the doctor checks). A local FFmpeg 8.x matches the self-test on major number. Patch level and the x264 build can still change frame bytes and `final_sha256`.
- Node, HyperFrames, puppeteer-core, three and chrome-headless-shell in the image are the vendor tree (`vendor/VERSIONS.json`). `engine doctor` reports that copy. A HyperFrames line that says pinned or vendor is the image layout; the container does not carry a second install. The self-test compares versions, not that label.
- `final.mp4` is the encoder output. The fetch does not transcode it. `build_report.json` and `build_manifest.json` are not byte-identical to a local report because of the `remote` object.
- The remote segment cache starts empty. Locked sound files are part of the closure, so a covered lock is reused. A lock whose file is missing locally fails before the upload.
- A symlink is uploaded as the target's bytes and materialised as a regular file. Relative layout is unchanged.
- Scratch is `/work/shm` on the container disk, not the caller's tmpfs.
- The pipeline does not use a GPU. Requesting one does not change the encode.
- Unpinned captions and unresolved sounds are not fetched in the container. Pin words and resolve sounds locally first.
