# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Weather agent for callbacks example."""

from typing import Any, Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def get_weather(city: str) -> Dict[str, Any]:
    """查询指定城市当前天气。"""
    data = {
        "上海": {"temperature": 18, "condition": "多云"},
        "北京": {"temperature": 15, "condition": "晴"},
    }
    result = data.get(city, {"temperature": 20, "condition": "未知"})
    return {"city": city, **result}

def get_dingdan(user: str) -> Dict[str, Any]:
    """查询指定用户的订单价格，如果用户想要退货的话"""
    data = {
        "ljj": {"电脑订单价格": 2000, "显卡订单价格": 24000},
    }
    result = data.get(user, {"**订单价格": -1})
    return {"user": user, **result}


def create_agent() -> LlmAgent:
    api_key, url, model_name = get_model_config()
    # 定义基础工具
    toolweather = FunctionTool(get_weather)
    tooldingdan = FunctionTool(get_dingdan)

    # 定义智能体
    return LlmAgent(
        name="agent-zero",
        description="天气，订单查询助手",
        model=OpenAIModel(
            model_name=model_name, 
            api_key=api_key, 
            base_url=url
        ),
        instruction="你是一个综合智能体，适合用于查询订单和查询天气",
        tools=[tooldingdan, toolweather],
    )


root_agent = create_agent()
