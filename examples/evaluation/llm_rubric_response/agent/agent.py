# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Simple agent for llm_rubric_response evaluator demo."""

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
    """Query the current weather of a specified city."""
    weather_data = {
        "Beijing": {
            "temperature": 15,
            "condition": "Sunny"
        },
        "Shanghai": {
            "temperature": 18,
            "condition": "Cloudy"
        },
        "Shenzhen": {
            "temperature": 25,
            "condition": "Sunny"
        },
    }
    result = weather_data.get(city, {"temperature": 20, "condition": "Unknown"})
    return {"city": city, **result}


def create_agent() -> LlmAgent:
    """Create the agent for llm_rubric_response demo."""
    return LlmAgent(
        name="llm_rubric_response_agent",
        description="Simple question-answering assistant, can query weather",
        model=_create_model(),
        instruction=("你是问答助手。用户问天气时用 get_weather 查询后回答。"
                     "回答须包含明确结论（如温度、天气状况），且与用户问题直接相关。"),
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
