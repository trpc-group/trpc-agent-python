"""LLM configuration loaded from ``.env`` (Phase 7).

All configuration is sourced from environment variables (a ``.env`` file
is auto-loaded via ``python-dotenv`` from the project root). Missing or
empty values fall back to safe defaults so the agent stays runnable with
no model configured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Default location: <project_root>/.env  (llm/ lives under agent/llm).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_ENV = _PROJECT_ROOT / ".env"


@dataclass
class LlmConfig:
    """Resolved LLM settings."""

    enabled: bool
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip())


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def load_llm_config(env_path: str | None = None) -> LlmConfig:
    """Read LLM_* settings from the environment (auto-loading ``.env``).

    ``env_path`` lets callers point at an explicit ``.env`` (e.g. via the
    ``--llm-env`` CLI flag); otherwise the project-root ``.env`` is used.
    """
    dotenv_path = _DEFAULT_ENV if env_path is None else os.path.expanduser(env_path)
    # override=False so an already-exported real env var wins over the file.
    load_dotenv(dotenv_path=dotenv_path, override=False)

    return LlmConfig(
        enabled=_truthy(os.getenv("LLM_ENABLED")),
        provider=(os.getenv("LLM_PROVIDER") or "openai").strip(),
        api_key=(os.getenv("LLM_API_KEY") or "").strip(),
        base_url=(os.getenv("LLM_BASE_URL") or "").strip(),
        model=(os.getenv("LLM_MODEL") or "gpt-4o-mini").strip(),
        temperature=float(os.getenv("LLM_TEMPERATURE") or "0.1"),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS") or "1024"),
        timeout=int(os.getenv("LLM_TIMEOUT") or "30"),
    )
