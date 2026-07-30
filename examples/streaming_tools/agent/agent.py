# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info
from .tools import write_file


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create an agent with streaming and standard tools.

    Tools:
      - write_file: StreamingFunctionTool for streaming file writes.
      - get_file_info: FunctionTool for querying file information.
    """
    return LlmAgent(
        name="streaming_tool_demo_agent",
        description="An assistant demonstrating streaming tool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            StreamingFunctionTool(write_file),
            FunctionTool(get_file_info),
        ],
    )


root_agent = create_agent()
