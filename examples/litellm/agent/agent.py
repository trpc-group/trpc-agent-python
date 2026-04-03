# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Usage: LiteLLMModel(provider/model) -> LlmAgent(model=...)."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LiteLLMModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


def _model(model_name: str | None = None):
    api_key, base_url, default_name = get_model_config()
    name = model_name if model_name is not None else default_name
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["api_base"] = base_url
    return LiteLLMModel(model_name=name, **kwargs)


def create_agent(model_name: str | None = None) -> LlmAgent:
    return LlmAgent(
        name="weather_agent",
        model=_model(model_name),
        instruction=INSTRUCTION,
        tools=[FunctionTool(get_weather_report)],
    )


root_agent = create_agent()
