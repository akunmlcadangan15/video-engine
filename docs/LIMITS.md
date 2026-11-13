# Limits and not implemented

- Platforms: Linux x64 and macOS Apple Silicon (arm64) are equal, supported platforms (see [ENVIRONMENT.md](ENVIRONMENT.md#platforms)). Intel Macs, Linux arm64 and Windows are not supported by the toolchain installer.
- A grade on FFmpeg 9 shifts luma by about +0.25 code values (of 255): its swscale converts 8-bit YUV to the 16-bit RGB the grade works in at lower precision than FFmpeg 7 and 8. The pinned Modal image (FFmpeg 8) and the macOS CI job (FFmpeg 7) are not affected.
- Output across platforms has the same spec and passes the same gates, but is not bit-identical (different ffmpeg and Chrome builds); the cache is not shared between platforms.
- Engine upload: not implemented; the Projects Manager bot handles Google Drive delivery.
- AI QA: covered by mock tests; not part of the default build.
- OpenAI-compatible providers: covered by mock tests; none is registered in the shipped `ai_providers.toml` (no vendor default, no implicit fallback).
- Speech-to-text uses the xAI REST endpoint (`XAI_API_KEY`, 300 s timeout, retries on 429/503/transport errors).
  Pinned `captions` remove transcription from builds; `engine words --retranscribe` fetches fresh words, and `caption_fixes` must then cover the new variants.
- Gates: 3-level model `error` / `fix` / `lint`. By default, the 8 kernel gates (`T02`, `M01`, `W01`, `L22`, `R38`, `A01`, `G01`, `M03`) and non-advisory technical gates outside `LINT_GATE_IDS` (`F01`, and `AS04` when raised to `fail`) block a release publish; the 51 lint gates in `LINT_GATE_IDS` warn and publish with warnings unless listed in `require`.
  `build_report.json` always holds the full `{required, segment_intents, fails, warns, waived}` gate summary alongside top-level `preflight_fixes`. `engine gates` shows KERNEL, LINT, or ERROR.
