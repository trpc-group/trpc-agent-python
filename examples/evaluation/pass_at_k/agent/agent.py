# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Weather agent for pass@k example."""

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
        "Hangzhou": {
            "temperature": 20,
            "condition": "Light rain"
        },
    }
    result = weather_data.get(city, {"temperature": 20, "condition": "Unknown"})
    return {"city": city, **result}


def create_agent() -> LlmAgent:
    """Create the weather agent."""
    return LlmAgent(
        name="weather_agent",
        description="Weather query assistant",
        model=_create_model(),
        instruction="You are a weather assistant, use get_weather to query the weather of a city and briefly answer.",
        tools=[FunctionTool(get_weather)],
    )


root_agent = create_agent()
