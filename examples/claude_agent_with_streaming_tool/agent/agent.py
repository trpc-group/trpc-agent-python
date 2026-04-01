# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""ClaudeAgent with streaming tool call demo.

This example demonstrates selective streaming tool support in ClaudeAgent,
which now aligns with LlmAgent behavior:
- Only tools wrapped with StreamingFunctionTool receive streaming events
- Regular FunctionTool tools do NOT receive streaming events
"""

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import StreamingFunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_file_info
from .tools import write_file


def _create_model() -> OpenAIModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> ClaudeAgent:
    """Create a ClaudeAgent with selective streaming tool call support.

    This agent demonstrates:
    - StreamingFunctionTool: write_file - receives streaming argument events
    - Regular FunctionTool: get_file_info - does NOT receive streaming events

    This behavior now aligns with LlmAgent, where only tools with
    is_streaming=True property receive real-time argument updates.
    """
    # Create streaming tool - arguments will be streamed in real-time
    # StreamingFunctionTool has is_streaming=True
    write_file_tool = StreamingFunctionTool(write_file)

    # Create regular (non-streaming) tool - arguments arrive only when complete
    # FunctionTool has is_streaming=False (default)
    get_file_info_tool = FunctionTool(get_file_info)

    return ClaudeAgent(
        name="claude_streaming_file_writer",
        description="A file writing assistant using ClaudeAgent with selective streaming tool support.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            write_file_tool,  # Streaming: will show real-time argument updates
            get_file_info_tool,  # Non-streaming: arguments arrive only when complete
        ],
        enable_session=True,
    )


root_agent = create_agent()
