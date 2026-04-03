# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Comprehensive Streaming Tool Test Agent.

This agent tests all streaming tool scenarios:
1. Sync function -> StreamingFunctionTool
2. Async function -> StreamingFunctionTool
3. FunctionTool -> StreamingFunctionTool
4. Custom BaseTool with is_streaming=True
5. ToolSet containing streaming tools
6. Mixed configuration: ToolSet + FunctionTool + StreamingFunctionTool
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import AnthropicModel
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import StreamingFunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import CustomStreamingWriteTool
from .tools import StreamingFileToolSet
from .tools import append_file_streaming_tool
from .tools import async_write_file
from .tools import get_file_info
from .tools import save_document
from .tools import write_file


def _create_openai_model() -> LLMModel:
    """Create an OpenAI model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def _create_anthropic_model() -> LLMModel:
    """Create an Anthropic Claude model."""
    api_key, url, model_name = get_model_config()
    model = AnthropicModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a comprehensive streaming tool test agent.

    This agent is configured with all types of tools to test:
    - Stream tool call event propagation
    - Various tool conversion methods
    - ToolSet streaming support
    - Custom BaseTool streaming
    - Mixed tool configuration

    Tools configured:
    1. write_file_streaming - Sync function wrapped in StreamingFunctionTool
    2. async_write_file_streaming - Async function wrapped in StreamingFunctionTool
    3. append_file_streaming_tool - FunctionTool converted to StreamingFunctionTool
    4. custom_write - Custom BaseTool with is_streaming=True
    5. StreamingFileToolSet - ToolSet containing streaming tools
    6. save_document - StreamingFunctionTool wrapping a plain function
    7. get_file_info_tool - Non-streaming FunctionTool for comparison
    """

    # Test 1: Sync function -> StreamingFunctionTool
    write_file_streaming = StreamingFunctionTool(write_file)

    # Test 2: Async function -> StreamingFunctionTool
    async_write_file_streaming = StreamingFunctionTool(async_write_file)

    # Test 3: FunctionTool -> StreamingFunctionTool (already converted in tools.py)
    # append_file_streaming_tool is imported directly

    # Test 4: Custom BaseTool with is_streaming=True
    custom_streaming_tool = CustomStreamingWriteTool()

    # Test 5: ToolSet containing streaming tools
    streaming_toolset = StreamingFileToolSet()

    # Test 6: StreamingFunctionTool wrapping a plain function (imported)

    # Non-streaming tool for comparison
    get_file_info_tool = FunctionTool(get_file_info)

    # Create agent with ALL tool types mixed together
    # This tests: ToolSet + FunctionTool + StreamingFunctionTool configuration
    return LlmAgent(
        name="streaming_tool_test_agent",
        description="A comprehensive test agent for all streaming tool scenarios.",
        model=_create_openai_model(),
        # model=_create_anthropic_model(),
        instruction=INSTRUCTION,
        tools=[
            # StreamingFunctionTool from sync function
            write_file_streaming,
            # StreamingFunctionTool from async function
            async_write_file_streaming,
            # StreamingFunctionTool from FunctionTool
            append_file_streaming_tool,
            # Custom BaseTool with is_streaming=True
            custom_streaming_tool,
            # ToolSet containing streaming tools
            streaming_toolset,
            # StreamingFunctionTool wrapping a plain function
            save_document,
            # Non-streaming FunctionTool for comparison
            get_file_info_tool,
        ],
        # Streaming is auto-detected via is_streaming property at runtime
        generate_content_config=GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=4000,
        ),
    )


root_agent = create_agent()
