"""AI providers for `engine draft`, `engine director` and `--ai-qa`.

Providers: `gemini` (default), `agent` (file handoff, `agent.py`), and every `[providers.NAME]` entry of the AI
config file (OpenAI-compatible, `openai_compat.py`). Gemini falls back to `[gemini] fallback_provider` (default
`deepseek` when that provider is configured) for any request Gemini cannot answer.
Selection precedence: --provider flag > env VIDEO_ENGINE_AI_PROVIDER > `default` in the AI config file > gemini.
AI config file: env VIDEO_ENGINE_AI_CONFIG, else <engine root>/ai_providers.toml.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .. import config
from ..util import EngineError
from .base import AIResult, AwaitingAgent, Image, Media, Part, Provider, Text  # noqa: F401

DEFAULT = "gemini"
BUILTIN = ("gemini", "agent")
ENV_PROVIDER = "VIDEO_ENGINE_AI_PROVIDER"
ENV_CONFIG = "VIDEO_ENGINE_AI_CONFIG"


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
    name = flag or os.environ.get(ENV_PROVIDER) or cfg.get("default") or DEFAULT
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
            fallback_name = "deepseek" if "deepseek" in providers else None
        fallback = _openai_provider(fallback_name, cfg, post) if fallback_name else None
        return GeminiProvider(client=client, models=models, fallback=fallback)
    if name == "agent":
        from .agent import MODEL_LABEL, AgentProvider
        if root is None:
            raise EngineError("agent provider needs a bundle root")
        return AgentProvider(root, resume_cmd, resume, cfg.get("agent", {}).get("model_label", MODEL_LABEL))
    return _openai_provider(name, cfg, post)
