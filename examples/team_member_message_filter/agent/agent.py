# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TeamAgent setup demonstrating member message filter."""

from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.teams import keep_last_member_message
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content

from .config import get_model_config
from .prompts import ANALYST_INSTRUCTION
from .prompts import LEADER_INSTRUCTION
from .tools import calculate_statistics
from .tools import fetch_sales_data
from .tools import generate_trend_analysis


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_team(message_filter: Optional[Union[Callable, Dict[str, Callable]]] = None):
    """Create data analysis team.

    This system demonstrates TeamAgent with member message filter:
    - Leader delegates analysis tasks
    - Analyst performs multi-step data analysis
    - Message filter controls how member messages are aggregated

    Args:
        message_filter: Message filter function, can be:
            - keep_all_member_message (default behavior)
            - keep_last_member_message
            - Custom filter function
            - Dict mapping member names to filter functions
    """

    model = _create_model()

    # Data Analyst - performs multi-step analysis tasks
    analyst = LlmAgent(
        name="analyst",
        model=model,
        description="Data analysis expert, skilled in multi-dimensional data analysis",
        instruction=ANALYST_INSTRUCTION,
        tools=[
            FunctionTool(fetch_sales_data),
            FunctionTool(calculate_statistics),
            FunctionTool(generate_trend_analysis),
        ],
    )

    # Create team with message filter
    return TeamAgent(
        name="analysis_team",
        model=model,
        members=[analyst],
        instruction=LEADER_INSTRUCTION,
        member_message_filter=message_filter,
    )


async def custom_keep_message(messages: List[Content]) -> str:
    """Custom message filter that keeps last message and logs the result.

    Args:
        messages: List of Content messages from member

    Returns:
        Filtered message text
    """
    # Use built-in keep_last_member_message
    message_text = await keep_last_member_message(messages)

    # Log the filtered result (for demonstration)
    print("\n==============================================")
    print(f"Got custom message_text:\n{message_text}")
    print("==============================================\n")

    return message_text


def create_team_with_custom_filter():
    """Create team with custom message filter.

    Returns:
        TeamAgent with custom message filter applied to analyst member
    """
    return create_team(message_filter={
        "analyst": custom_keep_message,
    })


root_agent = create_team_with_custom_filter()
