# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent setup demonstrating TeamAgent as sub_agent with transfer capabilities."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import ANALYST_INSTRUCTION
from .prompts import COORDINATOR_INSTRUCTION
from .prompts import REPORT_AGENT_INSTRUCTION
from .tools import analyze_financial_data
from .tools import generate_report


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_coordinator():
    """Create a coordinator with finance_team and report_agent as sub_agents.

    Architecture:
        coordinator (Root LlmAgent)
        ├── finance_team (TeamAgent)
        │   ├── analyst (LlmAgent member with transfer capability)
        │   └── auditor (LlmAgent member)
        └── report_agent (LlmAgent)

    The analyst can transfer to:
    - coordinator (parent agent)
    - report_agent (sibling agent)
    """

    model = _create_model()

    # Create finance team members
    analyst = LlmAgent(
        name="analyst",
        model=model,
        description="Financial analyst who can analyze data and transfer to other agents",
        instruction=ANALYST_INSTRUCTION,
        tools=[FunctionTool(analyze_financial_data)],
        # Enable transfer to parent and peers
        disallow_transfer_to_parent=False,
        disallow_transfer_to_peers=False,
    )

    # Create finance team (TeamAgent as sub_agent)
    finance_team = TeamAgent(
        name="finance_team",
        model=model,
        members=[analyst],
        instruction="""You are the finance team leader. Delegate financial analysis tasks to the analyst.
        Always transfer to coordinator for further discussion.
        Always transfer to report_agent to report.
        """,
        share_member_interactions=True,
        # Enable transfer to parent and peers
        disallow_transfer_to_parent=False,
        disallow_transfer_to_peers=False,
    )

    # Create report agent (sibling of finance_team)
    report_agent = LlmAgent(
        name="report_agent",
        model=model,
        description="Report generation specialist",
        instruction=REPORT_AGENT_INSTRUCTION,
        tools=[FunctionTool(generate_report)],
    )

    # Create coordinator as root agent with two sub_agents
    coordinator = LlmAgent(
        name="coordinator",
        model=model,
        description="Coordinator between finance team and report agent.",
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[finance_team, report_agent],
        tools=[],
    )

    return coordinator


root_agent = create_coordinator()
