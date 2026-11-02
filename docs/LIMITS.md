# Limits and not implemented

- Platform: Linux x64 only (see [ENVIRONMENT.md](ENVIRONMENT.md)); macOS and Windows are not supported by the toolchain installer.
- Engine upload: not implemented; the Projects Manager bot handles Google Drive delivery.
- AI QA: covered by mock tests; not part of the default build.
- OpenAI-compatible providers: covered by mock tests; `deepseek` is registered in `ai_providers.toml` as the Gemini fallback.
- Speech-to-text uses the xAI REST endpoint (`XAI_API_KEY`, 300 s timeout, retries on 429/503/transport errors).
  Pinned `captions` remove transcription from builds; `engine words --retranscribe` fetches fresh words, and `caption_fixes` must then cover the new variants.
