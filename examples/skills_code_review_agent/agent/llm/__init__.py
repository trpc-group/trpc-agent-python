"""LLM integration for the Code Review Agent (Phase 7, optional).

Real model calls are OFF by default. Configuration lives in ``.env``
(see ``.env.example``). When ``LLM_ENABLED`` is false or no API key is
present, :func:`get_llm_client` returns a :class:`FakeLlm` that is a safe
no-op — the deterministic parse -> sandbox -> persist chain (and
``dry-run``) keep working with **zero model dependency**, so tests without
a real API key still exercise parsing, the sandbox, and the storage layer.
"""
from .client import FakeLlm, LlmClient, RealLlm, get_llm_client
from .config import LlmConfig, load_llm_config
from .triage import LlmTriage

__all__ = [
    "LlmConfig",
    "load_llm_config",
    "LlmClient",
    "FakeLlm",
    "RealLlm",
    "get_llm_client",
    "LlmTriage",
]
