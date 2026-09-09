# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module for the model retry example."""

from typing import Any

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .config import get_model_retry_config
from .prompts import INSTRUCTION
from .tools import get_weather_report

def _extract_some_field(response_data: dict[str, Any]) -> dict[str, Any] | None:
    """Allowlist Venus tracing metadata from an OpenAI-compatible response."""
    marker = response_data.get("some_field")
    if not isinstance(marker, dict):
        return None
    some_value = marker.get("some_value")
    if not isinstance(some_value, str) or not some_value:
        return None
    return {
        "some_field": {
            "some_value": some_value,
        },
    }

def _create_model() -> LLMModel:
    """Create an OpenAI-compatible model with SDK-managed retry enabled."""
    api_key, base_url, model_name = get_model_config()
    retry_config = get_model_retry_config()
    print(f"Model retry enabled: {retry_config.model_dump()}")
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        model_retry_config=retry_config,
        response_metadata_extractor=_extract_some_field,
    )


def create_agent() -> LlmAgent:
    """Create a weather agent that uses model-level retry."""
    return LlmAgent(
        name="weather_retry_agent",
        description="A weather assistant with SDK-managed model retry enabled.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(get_weather_report)],
    )


root_agent = create_agent()
