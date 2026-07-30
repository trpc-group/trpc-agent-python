# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TeamAgent setup demonstrating parallel execution mode.

This example shows how to enable parallel_execution=True so that when the
leader delegates to multiple members in a single turn, they execute concurrently.
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import COMPETITOR_ANALYST_INSTRUCTION
from .prompts import LEADER_INSTRUCTION
from .prompts import MARKET_ANALYST_INSTRUCTION
from .prompts import RISK_ANALYST_INSTRUCTION
from .tools import analyze_competitor
from .tools import analyze_market_trends
from .tools import analyze_risks
from .tools import get_current_date


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_team():
    """Create an analysis team with parallel execution enabled.

    This system demonstrates TeamAgent parallel execution mode:
    - Leader delegates to multiple analysts simultaneously
    - All analysts execute in parallel (via asyncio.gather)
    - Results are collected and synthesized by the leader

    Key setting: parallel_execution=True
    """

    model = _create_model()

    # Market analyst - expert at market trends
    market_analyst = LlmAgent(
        name="market_analyst",
        model=model,
        description="Market trends analysis expert",
        instruction=MARKET_ANALYST_INSTRUCTION,
        tools=[FunctionTool(analyze_market_trends)],
    )

    # Competitor analyst - expert at competitive analysis
    competitor_analyst = LlmAgent(
        name="competitor_analyst",
        model=model,
        description="Competitor analysis expert",
        instruction=COMPETITOR_ANALYST_INSTRUCTION,
        tools=[FunctionTool(analyze_competitor)],
    )

    # Risk analyst - expert at risk assessment
    risk_analyst = LlmAgent(
        name="risk_analyst",
        model=model,
        description="Risk assessment expert",
        instruction=RISK_ANALYST_INSTRUCTION,
        tools=[FunctionTool(analyze_risks)],
    )

    # Analysis team with PARALLEL EXECUTION enabled
    # When leader delegates to multiple members in one turn,
    # they will execute concurrently instead of sequentially
    analysis_team = TeamAgent(
        name="analysis_team",
        model=model,
        members=[market_analyst, competitor_analyst, risk_analyst],
        instruction=LEADER_INSTRUCTION,
        parallel_execution=True,  # Enable parallel execution!
        share_member_interactions=True,
        tools=[FunctionTool(get_current_date)],
    )

    return analysis_team


root_agent = create_team()
