# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Minimal agent for trace mode example (in trace mode the agent is not run)."""

from typing import Any
from typing import Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def get_weather(city: str) -> Dict[str, Any]:
    """查询指定城市当前天气。"""
    return {"city": city, "temperature": 18, "condition": "多云"}


def create_agent() -> LlmAgent:
    api_key, url, model_name = get_model_config()
    return LlmAgent(
        name="weather_agent",
        description="天气助手",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=url),
        instruction="用 get_weather 查天气并简要回答。",
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
