# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Simple QA agent for llm_judge_tools example."""

from typing import Any
from typing import Dict

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
        "北京": {
            "temperature": 15,
            "condition": "Sunny"
        },
        "上海": {
            "temperature": 18,
            "condition": "多云"
        },
        "Shenzhen": {
            "temperature": 25,
            "condition": "Sunny"
        },
    }
    result = weather_data.get(city, {"temperature": 20, "condition": "未知"})
    return {"city": city, **result}


def create_agent() -> LlmAgent:
    """Create the agent for llm_judge_tools demo."""
    return LlmAgent(
        name="llm_judge_tools_agent",
        description="简单问答助手，可查天气",
        model=_create_model(),
        instruction=("你是问答助手。用户问天气时用 get_weather 查询后简洁回答，"
                     "例如只回答温度与天气状况。"),
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
