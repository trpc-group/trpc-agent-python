# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Transfer Agent example with KnotAgent as custom agent"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TransferAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import DATA_ANALYST_INSTRUCTION
from .prompts import TRANSFER_INSTRUCTION
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> TransferAgent:
    """Create a TransferAgent with KnotAgent as the target agent.

    KnotAgent's knot_model is optional; only KNOT_API_URL and KNOT_API_KEY are required.
    """

    model = _create_model()

    knot_agent = LlmAgent(
        name="knot-assistant",
        description="A Knot API agent for information queries and assistance",
        model=model,
        tools=[FunctionTool(get_weather_report)],
    )

    data_analyst = LlmAgent(
        name="data_analyst",
        model=model,
        description="Performs data analysis and generates insights from data",
        instruction=DATA_ANALYST_INSTRUCTION,
    )

    transfer_agent = TransferAgent(
        knot_agent,
        sub_agents=[data_analyst],
        model=model,
        transfer_instruction=TRANSFER_INSTRUCTION,
    )
    return transfer_agent


root_agent = create_agent()
