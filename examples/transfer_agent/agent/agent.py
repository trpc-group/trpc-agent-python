# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Transfer Agent example with KnotAgent as custom agent"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TransferAgent
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config


def create_agent() -> TransferAgent:
    """Create a TransferAgent with KnotAgent as the target agent.

    KnotAgent's knot_model is optional; only KNOT_API_URL and KNOT_API_KEY are required.
    """

    knot_agent = LlmAgent(
        name="knot-assistant",
        description="A Knot API agent for information queries and assistance",
        model=model,
    )

    api_key, base_url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)

    data_analyst = LlmAgent(
        name="data_analyst",
        model=model,
        description="Performs data analysis and generates insights from data",
        instruction=("You are a data analyst. When you receive data or statistics, "
                     "analyze it and just provide a sheet of data, it can only be in tabular form."),
    )

    transfer_agent = TransferAgent(
        knot_agent,
        sub_agents=[data_analyst],
        model=model,
        transfer_instruction=
        ("1. If the result contains data, statistics, or weather information (temperature, weather conditions, etc.), "
         "   transfer to data_analyst for analysis."),
    )
    return transfer_agent


root_agent = create_agent()
