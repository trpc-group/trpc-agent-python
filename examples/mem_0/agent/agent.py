# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from mem0 import AsyncMemory
from mem0 import AsyncMemoryClient
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.mem0_tool import SaveMemoryTool
from trpc_agent_sdk.tools.mem0_tool import SearchMemoryTool

from .config import get_mem0_platform_config
from .config import get_memory_config
from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(use_mem0_platform: bool = False) -> LlmAgent:
    """ Create an agent"""
    if use_mem0_platform:
        mem0_platform_config = get_mem0_platform_config()
        mem0_client = AsyncMemoryClient(api_key=mem0_platform_config['api_key'], host=mem0_platform_config['host'])
        search_memory_tool = SearchMemoryTool(client=mem0_client)
        save_memory_tool = SaveMemoryTool(client=mem0_client)
    else:
        memory_config = get_memory_config()
        mem0_client = AsyncMemory(config=memory_config)
        search_memory_tool = SearchMemoryTool(client=mem0_client)
        save_memory_tool = SaveMemoryTool(client=mem0_client)
    return LlmAgent(
        name="personal_assistant",
        description="A personal assistant that remembers user preferences and past interactions",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[search_memory_tool, save_memory_tool],
    )


root_agent = create_agent(use_mem0_platform=False)
