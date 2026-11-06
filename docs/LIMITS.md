# Limits and not implemented

- Platforms: Linux x64 and macOS Apple Silicon (arm64) are equal, supported platforms (see [ENVIRONMENT.md](ENVIRONMENT.md#platforms)). Intel Macs, Linux arm64 and Windows are not supported by the toolchain installer.
- Output across platforms has the same spec and passes the same gates, but is not bit-identical (different ffmpeg and Chrome builds); the cache is not shared between platforms.
- Engine upload: not implemented; the Projects Manager bot handles Google Drive delivery.
- AI QA: covered by mock tests; not part of the default build.
- OpenAI-compatible providers: covered by mock tests; `deepseek` is registered in `ai_providers.toml` as the Gemini fallback.
- Speech-to-text uses the xAI REST endpoint (`XAI_API_KEY`, 300 s timeout, retries on 429/503/transport errors).
  Pinned `captions` remove transcription from builds; `engine words --retranscribe` fetches fresh words, and `caption_fixes` must then cover the new variants.
- Gates: 3-level model `error` / `fix` / `lint`. Only the 8 kernel gates (`T02`, `M01`, `W01`, `L22`, `R38`, `A01`, `G01`, `M03`) block a publish; lint gates warn and publish with warnings.
  `build_report.json` always holds the full `{errors, fixes, lints}` gate grouping. `engine gates` shows KERNEL vs LINT.
