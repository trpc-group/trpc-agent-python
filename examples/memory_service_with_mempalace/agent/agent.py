# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import load_memory_tool
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import HttpOptions

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model



def create_agent() -> LlmAgent:
    """ Create an agent"""
    generate_content_config = GenerateContentConfig(
        http_options=HttpOptions(extra_body={"chat_template_kwargs": {
                                     "enable_thinking": False
                                 }}),
    ) 
    agent = LlmAgent(
        name="assistant",
        description="A helpful assistant for conversation",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        tools=[FunctionTool(get_weather_report), load_memory_tool],
        generate_content_config=generate_content_config,
    )
    return agent


root_agent = create_agent()
