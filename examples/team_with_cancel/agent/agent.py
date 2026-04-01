# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TeamAgent setup with cancellation support."""

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
    """Create a content team with cancellation support.

    This system demonstrates TeamAgent cancellation in coordinate mode:
    - Leader coordinates research and writing tasks
    - Cancellation can occur during:
      * Leader thinking (LLM streaming)
      * Leader tool execution
      * Member execution (both leader and member checkpoints)

    The team preserves partial progress:
    - Leader's partial responses are saved
    - Member's partial responses are recorded in team memory
    - Cancellation records help maintain context for resuming

    Tools have delays (2-3 seconds) to simulate slow operations,
    giving enough time to cancel during execution.
    """

    model = _create_model()

    # Researcher agent - expert at finding information
    researcher = LlmAgent(
        name="researcher",
        model=model,
        description="Research expert who can search and gather information",
        instruction=RESEARCHER_INSTRUCTION,
        tools=[FunctionTool(search_web)],
    )

    # Writer agent - expert at creating engaging content
    writer = LlmAgent(
        name="writer",
        model=model,
        description="Writing expert who creates high-quality content",
        instruction=WRITER_INSTRUCTION,
        tools=[FunctionTool(check_grammar)],
    )

    # Content team with coordinate mode and cancellation support
    content_team = TeamAgent(
        name="content_team_with_cancel",
        model=model,
        members=[researcher, writer],
        instruction=LEADER_INSTRUCTION,
        share_member_interactions=True,
        tools=[FunctionTool(get_current_date)],
        # Enable history for multi-turn conversation support
        add_history_to_leader=True,
        num_history_runs=3,
    )

    return content_team


root_agent = create_team()
