# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TeamAgent setup demonstrating coordinate mode."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import LEADER_INSTRUCTION
from .prompts import RESEARCHER_INSTRUCTION
from .prompts import WRITER_INSTRUCTION
from .tools import check_grammar
from .tools import get_current_date
from .tools import search_web


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_team():
    """Create a content team with researcher and writer.

    This system demonstrates TeamAgent coordinate mode:
    - Leader coordinates research and writing tasks
    - Researcher gathers information using search_web tool
    - Writer creates content using check_grammar tool
    """

    model = _create_model()

    # Researcher agent - expert at finding information
    researcher = LlmAgent(
        name="researcher",
        model=model,
        description="Research expert",
        instruction=RESEARCHER_INSTRUCTION,
        tools=[FunctionTool(search_web)],
    )

    # Writer agent - expert at creating engaging content
    writer = LlmAgent(
        name="writer",
        model=model,
        description="Writing expert",
        instruction=WRITER_INSTRUCTION,
        tools=[FunctionTool(check_grammar)],
    )

    # Content team with coordinate mode
    content_team = TeamAgent(
        name="content_team",
        model=model,
        members=[researcher, writer],
        instruction=LEADER_INSTRUCTION,
        share_member_interactions=True,
        tools=[FunctionTool(get_current_date)],
    )

    return content_team


root_agent = create_team()
