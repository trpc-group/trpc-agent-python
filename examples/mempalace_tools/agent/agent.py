# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.mempalace_tool import MempalaceAddDrawerTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceDiaryReadTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceDiaryWriteTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGAddTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGInvalidateTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGQueryTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceKGTimelineTool
from trpc_agent_sdk.tools.mempalace_tool import MempalaceSearchTool

from .config import get_mempalace_config
from .config import get_model_config
from .prompts import build_instruction


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """Create an agent with MemPalace tools."""
    mempalace_config = get_mempalace_config()
    palace_path = mempalace_config["palace_path"]
    kg_path = mempalace_config["kg_path"]
    tools = [
        MempalaceSearchTool(palace_path=palace_path),
        MempalaceAddDrawerTool(palace_path=palace_path),
        MempalaceDiaryWriteTool(palace_path=palace_path),
        MempalaceDiaryReadTool(palace_path=palace_path),
        MempalaceKGQueryTool(palace_path=palace_path, kg_path=kg_path),
        MempalaceKGAddTool(palace_path=palace_path, kg_path=kg_path),
        MempalaceKGInvalidateTool(palace_path=palace_path, kg_path=kg_path),
        MempalaceKGTimelineTool(palace_path=palace_path, kg_path=kg_path),
    ]

    return LlmAgent(
        name="personal_assistant",
        description="A personal assistant that remembers user preferences and past interactions",
        model=_create_model(),
        instruction=build_instruction(
            wing=mempalace_config["wing"],
            room=mempalace_config["room"],
        ),
        tools=tools,
    )


root_agent = create_agent()
