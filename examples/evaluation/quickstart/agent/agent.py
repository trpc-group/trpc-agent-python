# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Weather agent: current weather, forecast, AQI, UV index."""

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
        "北京": {"temperature": 15, "condition": "晴", "humidity": 45, "wind_speed": 10},
        "上海": {"temperature": 18, "condition": "多云", "humidity": 60, "wind_speed": 15},
        "深圳": {"temperature": 25, "condition": "晴", "humidity": 70, "wind_speed": 8},
        "杭州": {"temperature": 20, "condition": "小雨", "humidity": 85, "wind_speed": 12},
    }
    result = weather_data.get(
        city, {"temperature": 20, "condition": "未知", "humidity": 50, "wind_speed": 10}
    )
    return {"city": city, **result}


def get_weather_forecast(city: str, days: int = 3) -> Dict[str, Any]:
    """查询指定城市未来几日天气预报。"""
    return {
        "city": city,
        "forecast": [{"date": "today", "temperature": "20°C", "condition": "晴"}] * days,
    }


def get_air_quality(city: str) -> Dict[str, Any]:
    """查询指定城市空气质量。"""
    aqi_data = {"北京": 85, "上海": 72, "深圳": 65, "杭州": 90, "广州": 78}
    aqi = aqi_data.get(city, 75)
    level = "优" if aqi <= 50 else "良" if aqi <= 100 else "轻度污染"
    return {"city": city, "aqi": aqi, "level": level}


def get_uv_index(city: str) -> Dict[str, Any]:
    """查询指定城市紫外线指数。"""
    uv_data = {"北京": 5, "上海": 6, "深圳": 8, "杭州": 4, "广州": 7}
    uv = uv_data.get(city, 5)
    suggestion = "注意防晒" if uv >= 6 else "适宜户外"
    return {"city": city, "uv_index": uv, "suggestion": suggestion}


def create_agent() -> LlmAgent:
    """Create the weather agent."""
    return LlmAgent(
        name="weather_agent",
        description="天气查询助手，可查当前天气、预报、空气质量、紫外线指数",
        model=_create_model(),
        instruction=(
            "你是天气助手。用 get_weather 查当前天气，get_weather_forecast 查预报，"
            "get_air_quality 查空气质量，get_uv_index 查紫外线。"
            "用户问多类信息时依次调用相应工具。"
        ),
        tools=[
            FunctionTool(get_weather),
            FunctionTool(get_weather_forecast),
            FunctionTool(get_air_quality),
            FunctionTool(get_uv_index),
        ],
    )


root_agent = create_agent()
