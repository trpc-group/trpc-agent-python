# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Weather agent for pass@k example."""

from typing import Any, Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def _create_model() -> OpenAIModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def get_weather(city: str) -> Dict[str, Any]:
    """查询指定城市当前天气。"""
    weather_data = {
        "北京": {"temperature": 15, "condition": "晴"},
        "上海": {"temperature": 18, "condition": "多云"},
        "深圳": {"temperature": 25, "condition": "晴"},
        "杭州": {"temperature": 20, "condition": "小雨"},
    }
    result = weather_data.get(
        city, {"temperature": 20, "condition": "未知"}
    )
    return {"city": city, **result}


def create_agent() -> LlmAgent:
    """Create the weather agent."""
    return LlmAgent(
        name="weather_agent",
        description="天气查询助手",
        model=_create_model(),
        instruction="你是天气助手，用 get_weather 查询城市天气并简要回答。",
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
