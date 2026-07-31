# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the agent path — model and sandbox runtime selection.

A **real model is the default**. The fake model exists because issue #92 requires a dry-run mode
that exercises the parse -> sandbox -> persist chain without an API key, but it is a mode you ask
for, never what you get by accident: with no model configured and no ``--dry-run``, this raises
rather than silently degrading to a model that does not think.
"""
from __future__ import annotations

import os

from trpc_agent_sdk.models import LLMModel

from .model import FakeReviewModel

#: Any OpenAI-compatible endpoint works — a hosted API, a gateway, or a local Ollama/vLLM server.
DEFAULT_MODEL_NAME = "gpt-4o-mini"

_NO_MODEL_HELP = (
    "No model configured. Set TRPC_AGENT_API_KEY (with MODEL_NAME / TRPC_AGENT_BASE_URL for a "
    "non-default endpoint), or pass --dry-run to use the fake model.\n"
    "  hosted:  TRPC_AGENT_API_KEY=sk-...  MODEL_NAME=gpt-4o-mini\n"
    "  ollama:  TRPC_AGENT_API_KEY=ollama  TRPC_AGENT_BASE_URL=http://localhost:11434/v1  "
    "MODEL_NAME=qwen2.5:7b")


def get_model(force_fake: bool = False) -> LLMModel:
    """The configured real model; the fake model only when explicitly requested."""
    if force_fake:
        return FakeReviewModel(model_name="fake-review-1")

    api_key = os.getenv("TRPC_AGENT_API_KEY")
    if not api_key:
        raise RuntimeError(_NO_MODEL_HELP)

    from trpc_agent_sdk.models import OpenAIModel
    return OpenAIModel(
        model_name=os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME),
        api_key=api_key,
        base_url=os.getenv("TRPC_AGENT_BASE_URL"),
    )


def get_runtime_kind() -> str:
    """Workspace runtime for the skill sandbox: ``container`` (default) or ``local`` (dev fallback)."""
    return os.getenv("REVIEW_RUNTIME", "container")
