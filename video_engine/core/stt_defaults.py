"""Speech transcription defaults shared by schema and provider configuration."""
STT_DEFAULTS = {"provider": "xai", "url": "https://api.x.ai/v1/stt", "model": "grok-voice-transcribe-2.0",
                "api_key_env": "XAI_API_KEY", "timeout_s": 300}
