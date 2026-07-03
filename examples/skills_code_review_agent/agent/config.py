# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Configuration for the agent path — model selection and defaults."""
from __future__ import annotations

import os

from trpc_agent_sdk.models import LLMModel

from .model import FakeReviewModel


def get_model() -> LLMModel:
    """Return the fake model by default (no API key); a real OpenAI model if one is configured."""
    api_key = os.getenv("TRPC_AGENT_API_KEY")
    if api_key:
        from trpc_agent_sdk.models import OpenAIModel

        return OpenAIModel(
            model_name=os.getenv("MODEL_NAME", "gpt-4o-mini"),
            api_key=api_key,
            base_url=os.getenv("TRPC_AGENT_BASE_URL"),
        )
    return FakeReviewModel(model_name="fake-review-1")
