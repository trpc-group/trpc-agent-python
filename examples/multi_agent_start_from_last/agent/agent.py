# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Multi-agent setup demonstrating start_from_last_agent feature."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import COORDINATOR_INSTRUCTION
from .prompts import SALES_INSTRUCTION
from .prompts import TECHNICAL_INSTRUCTION
from .tools import check_device_status
from .tools import get_product_info


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a multi-agent customer service system.

    This system demonstrates the start_from_last_agent feature:
    - Coordinator routes initial requests to specialists
    - With start_from_last_agent=True, follow-up questions go directly
      to the last active specialist instead of back to the coordinator
    """

    model = _create_model()

    # Sales consultant sub-agent
    sales_agent = LlmAgent(
        name="sales_consultant",
        model=model,
        description="Sales consultant for product information and pricing",
        instruction=SALES_INSTRUCTION,
        tools=[FunctionTool(get_product_info)],
        output_key="sales_result",
    )

    # Technical support sub-agent
    technical_agent = LlmAgent(
        name="technical_support",
        model=model,
        description="Technical support specialist for troubleshooting",
        instruction=TECHNICAL_INSTRUCTION,
        tools=[FunctionTool(check_device_status)],
        output_key="technical_result",
    )

    # Coordinator (root agent)
    coordinator = LlmAgent(
        name="coordinator",
        model=model,
        description="Customer service coordinator that routes inquiries",
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[sales_agent, technical_agent],
        output_key="coordinator_result",
    )

    return coordinator


root_agent = create_agent()
