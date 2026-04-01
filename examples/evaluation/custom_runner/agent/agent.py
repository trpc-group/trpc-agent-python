# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Weather agent for custom runner example."""

from typing import Any
from typing import Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def get_weather(city: str) -> Dict[str, Any]:
    """查询指定城市当前天气。"""
    data = {
        "上海": {
            "temperature": 18,
            "condition": "多云"
        },
        "北京": {
            "temperature": 15,
            "condition":"Sunny"
        },
    }
    result = data.get(city, {"temperature": 20, "condition": "未知"})
    return {"city": city, **result}


def create_agent() -> LlmAgent:
    api_key, url, model_name = get_model_config()
    return LlmAgent(
        name="weather_agent",
        description="天气查询助手",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=url),
        instruction="你是天气助手，用 get_weather 查询城市天气并简要回答。",
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
