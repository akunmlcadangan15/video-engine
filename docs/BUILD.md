# Build pipeline

What `engine build` does, in order, and how it stays repeatable.

**What this document answers**

- The stages from captions to publish, and when a build publishes or fails.
- What `build_manifest.json` and `build_report.json` contain.
- How the worker budget, stream copy and concat, and cache leases work.

1. Captions: pinned timeline words;
     else BLAKE3-cached Grok STT words (`grok-voice-transcribe-2.0`, 16 kHz mono WAV, word timestamps) + `caption_fixes`, pinned into `master_timeline.json` (fresh copy of the file, so in-memory gate fixes are not written).
2. Pre-flight (layers 1-3, caption placement, profile style gates).
3. Sounds (profile `[sound]`), after pre-flight, before the mix plan and the segment render: `sound.request.ensure_sounds` requests missing files (bundle `out/sound_request/`, exit 3; `--resume` imports the answer);
     after the plan the Epidemic Sound credit lines of the mixed files are appended to `credits.txt` (docs/SOUND.md).
4. Build manifest `out/build_manifest.json` written (see [Build manifest](#build-manifest)).
   Segments in parallel within the worker budget (see [Resources](#resources)): native pool `native_workers` x 2 x264 threads;
     HyperFrames segments in a pool of `hf_parallel` (see [Resources](#resources); output order is segment order), `hf_workers` capture workers each (`VIDEO_ENGINE_HF_WORKERS` overrides), default capture profile `-q standard --crf 14` (JPEG q100 screenshots, x264 medium intermediate; profile `[render] hf_quality` / `hf_crf` and `HF_QUALITY` / `HF_CRF` are part of the segment cache key; Geoverum uses `high` at CRF 8);
     loudnorm pass 1 runs concurrently.
   Every segment is checked for its exact frame count.
   Results cached by a BLAKE3 hash of sources + config.
5. `.ass` with placement overrides.
6. Three cached passes:
   - composite (cache kind `composite`): segment join (Stream-copy and concat rules) + transition clips + caption burn + profile watermark overlay (PNG drawn with Pillow), exact frame count, no audio, `mux_threads` x264 threads;
   - master audio (cache kind `audio`, AAC m4a): mix (ducking, loudnorm pass 2) and the A01 corrective gain passes, on the audio file alone.
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

Proxy requires draft mode, confines output to `P/out/draft/`, does not pin captions into the master timeline, and rejects `--repin`. Pin words separately when needed. Proxy also requires sounds and their credit lines to be prepared beforehand: `python -m video_engine.sound P --credits` (and `--resume` after an agent response). It never imports a sound handoff or appends source credits during the preview build; correctly prepared cached/locked sounds still render normally. It does not bypass licence, decode, frame-count, audio or explicit-moving freeze checks. The source timeline/profile remain unchanged; resolved values are recorded as `quality`, `render_quality` and `source_profile` in report/manifest. Effective render configuration changes visual cache keys; audio may be reused when its inputs are unchanged. Existing per-segment/DOM caches and separate audio/composite passes remain in use; this is not a new full dependency scheduler.

## Build manifest

`out/build_manifest.json`, written before any segment renders: `build_id` (random hex, also in `build_report.json`), `timeline` (sha256), `profile` (`name`, `blake3`), `inputs` (every referenced file), `fonts`, `sounds` (music with `brief_key` and provenance, SFX per kind), `toolchain` (ffmpeg version, x264 args, vendor pins, engine version), `seeds`, `resources`, `stage_keys` (composite and audio cache keys).
File entries are `{path, sha256}`; `path` is relative to the project, `cache:<rel>` under the cache root, `engine:<rel>` under the engine checkout, else absolute.
A later build reuses the locked sound files (sha256 checked) without asking the sound resolver;
  a locked file that changed or vanished is a stage error naming `--relock`;
  a lock that does not cover the timeline (new SFX kind, changed music brief) is ignored.
The same inputs give the same final file bytes.

## Resources

One budget `workers` (`VIDEO_ENGINE_WORKERS` > profile `[resources] workers` > auto = clamp(min(CPU count, RAM GB // 1.8), 2, 64)) caps all stages: `native_workers = max(1, workers // 4)` (2 x264 threads each), `hf_workers = max(1, workers // 4)`, `mux_threads = clamp(workers - 2, 1, 6)`.
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
- Outside a session `acquire` does nothing and eviction treats every file as unleased.
