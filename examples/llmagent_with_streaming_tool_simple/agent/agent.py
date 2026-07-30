# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent configuration for streaming tool demo."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import AnthropicModel
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import StreamingFunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import write_file


def _create_openai_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def _create_anthropic_model() -> LLMModel:
    """Create an Anthropic Claude model."""
    api_key, url, model_name = get_model_config()
    model = AnthropicModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a file writing agent to demonstrate streaming tool capabilities.

    This agent uses StreamingFunctionTool to enable real-time streaming of
    tool call arguments. Streaming events are consumed through Runner.run_async().
    """

    # Create streaming tool - no callback needed
    # StreamingFunctionTool is a marker class that enables streaming for this tool
    # Streaming events are consumed through Runner.run_async() using event.is_streaming_tool_call()
    write_file_tool = StreamingFunctionTool(write_file)

    return LlmAgent(
        name="streaming_file_writer",
        description="A file writing assistant that demonstrates streaming tool call arguments.",
        model=_create_openai_model(),
        # model=_create_anthropic_model(),
        instruction=INSTRUCTION,
        tools=[write_file_tool],
        # Streaming is auto-detected via is_streaming property on StreamingFunctionTool
        generate_content_config=GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=2000,
        ),
    )


root_agent = create_agent()
