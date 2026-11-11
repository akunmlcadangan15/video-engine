"""AI providers for `engine draft`, `engine director` and `--ai-qa`.

Providers: `agent` (file handoff, `agent.py`), `gemini` (`gemini.py`), and every `[providers.NAME]` entry of the
AI config file (OpenAI-compatible, `openai_compat.py`).
The engine names no vendor default. Selection precedence: --provider flag > env VIDEO_ENGINE_AI_PROVIDER > `default`
in the AI config file > `agent`. With nothing selected the request goes to the agent provider: the AI model (or
person) that is running the engine answers it from the request bundle, so the model in use is the model that drafts.
A fallback provider exists only where the config asks for one (`[gemini] fallback_provider = "NAME"`).
AI config file: env VIDEO_ENGINE_AI_CONFIG, else <engine root>/ai_providers.toml.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .. import config
from ..util import EngineError
from .base import AIResult, AwaitingAgent, Image, Media, Part, Provider, Text  # noqa: F401

SESSION = "agent"                 # no provider selected: the model that runs the engine answers (file handoff)
BUILTIN = ("agent", "gemini")
ENV_PROVIDER = "VIDEO_ENGINE_AI_PROVIDER"
ENV_CONFIG = "VIDEO_ENGINE_AI_CONFIG"
ENV_MODEL = "VIDEO_ENGINE_AI_MODEL"   # agent provider: the name of the model that answers, written to the reports
STT_DEFAULTS = {"provider": "xai", "url": "https://api.x.ai/v1/stt", "model": "grok-voice-transcribe-2.0",
                "api_key_env": "XAI_API_KEY", "timeout_s": 300}


def config_path() -> Path:
    return Path(os.environ.get(ENV_CONFIG) or config.ENGINE_ROOT / "ai_providers.toml")


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return tomllib.loads(p.read_text())
    except tomllib.TOMLDecodeError as e:
        raise EngineError(f"{p}: invalid TOML ({e})") from None


def names(cfg: dict | None = None) -> list[str]:
    cfg = load_config() if cfg is None else cfg
    return [*BUILTIN, *sorted(cfg.get("providers", {}))]


def resolve(flag: str | None, cfg: dict | None = None) -> str:
    cfg = load_config() if cfg is None else cfg
    name = flag or os.environ.get(ENV_PROVIDER) or cfg.get("default") or SESSION
    if name not in names(cfg):
        raise EngineError(f"unknown AI provider '{name}' (known: {', '.join(names(cfg))}; add models in {config_path()})")
    return name


def _openai_provider(name: str, cfg: dict, post=None):
    from .openai_compat import Entry, OpenAICompatProvider
    return OpenAICompatProvider(Entry.from_config(name, cfg["providers"][name]), post=post)


def get(flag: str | None = None, *, root: Path | None = None, resume_cmd: str = "", resume: bool = False,
        client=None, models: list[str] | None = None, post=None) -> Provider:
    """Provider instance. `root`, `resume_cmd`, `resume`: agent bundle root and the command that resumes.
    `client`/`models` (gemini) and `post` (openai_compat, including the Gemini fallback) are injection points for
    tests."""
    cfg = load_config()
    name = resolve(flag, cfg)
    if name == "gemini":
        from .gemini import GeminiProvider
        gemini_cfg, providers = cfg.get("gemini", {}), cfg.get("providers", {})
        if "fallback_provider" in gemini_cfg:
            fallback_name = gemini_cfg["fallback_provider"]
            if fallback_name not in providers:
                known = ", ".join(sorted(providers)) or "none"
                raise EngineError(f"AI provider 'gemini': fallback_provider '{fallback_name}' is not a "
                                  f"[providers.NAME] entry (known: {known})")
        else:
            fallback_name = None      # never implicit: a request leaves for another service only when configured
        fallback = _openai_provider(fallback_name, cfg, post) if fallback_name else None
        cfg_models = models or gemini_cfg.get("models")
        cfg_qa_models = gemini_cfg.get("qa_models") or cfg_models
        return GeminiProvider(client=client, models=cfg_models, qa_models=cfg_qa_models, fallback=fallback)
    if name == "agent":
        from .agent import MODEL_LABEL, AgentProvider
        if root is None:
            raise EngineError("agent provider needs a bundle root")
        label = os.environ.get(ENV_MODEL) or cfg.get("agent", {}).get("model_label") or MODEL_LABEL
        return AgentProvider(root, resume_cmd, resume, label)
    return _openai_provider(name, cfg, post)


def stt_config(cfg: dict | None = None) -> dict:
    """STT configuration: [stt] in ai_providers.toml over STT_DEFAULTS (the one place that names them)."""
    cfg = load_config() if cfg is None else cfg
    st = cfg.get("stt", {})
    if not isinstance(st, dict):
        raise EngineError(f"{config_path()}: [stt] must be a table")
    unknown = sorted(set(st) - set(STT_DEFAULTS))
    if unknown:
        raise EngineError(f"{config_path()}: unknown [stt] key(s) {', '.join(unknown)} "
                          f"(known: {', '.join(STT_DEFAULTS)})")
    out = {**STT_DEFAULTS, **st}
    try:
        out["timeout_s"] = int(out["timeout_s"])
    except (TypeError, ValueError):
        raise EngineError(f"{config_path()}: [stt] timeout_s must be a whole number of seconds") from None
    return out
