# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File operations agent"""

import os
import tempfile

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.file_tools import BashTool
from trpc_agent_sdk.tools.file_tools import EditTool
from trpc_agent_sdk.tools.file_tools import GlobTool
from trpc_agent_sdk.tools.file_tools import GrepTool
from trpc_agent_sdk.tools.file_tools import ReadTool
from trpc_agent_sdk.tools.file_tools import WriteTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(work_dir: str | None = None):
    """Create a file operations agent to demonstrate file operation tools.

    Args:
        work_dir: Working directory for file operations. If None, uses system temp directory.

    Returns:
        Configured LlmAgent instance
    """
    # Create working directory if not provided
    if work_dir is None:
        system_temp = tempfile.gettempdir()
        work_dir = os.path.join(system_temp, "file_tools_demo")
        os.makedirs(work_dir, exist_ok=True)

    # Create individual tools with working directory
    read_tool = ReadTool(cwd=work_dir)  # Read file contents
    write_tool = WriteTool(cwd=work_dir)  # Write or append to files
    edit_tool = EditTool(cwd=work_dir)  # Replace text blocks in files
    grep_tool = GrepTool(cwd=work_dir)  # Search for patterns using regex
    bash_tool = BashTool(cwd=work_dir)  # Execute shell commands
    glob_tool = GlobTool(cwd=work_dir)  # Find files matching glob patterns

    return LlmAgent(
        name="file_assistant",
        description="File operations assistant with file operation tools",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[read_tool, write_tool, edit_tool, grep_tool, bash_tool, glob_tool],
    )


root_agent = create_agent()
