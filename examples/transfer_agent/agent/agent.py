# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Transfer Agent example with TrpcRemoteA2aAgent as custom agent."""

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TransferAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent

from .config import get_model_config
from .prompts import DATA_ANALYST_INSTRUCTION
from .prompts import TRANSFER_INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> TransferAgent:
    """Create a TransferAgent with a remote A2A agent as the target agent."""

    model = _create_model()

    remote_a2a_base_url = os.getenv("REMOTE_A2A_BASE_URL", "http://127.0.0.1:18081")
    remote_agent = TrpcRemoteA2aAgent(
        name="remote-weather-assistant",
        description="A remote weather agent served over A2A",
        agent_base_url=remote_a2a_base_url,
    )

    data_analyst = LlmAgent(
        name="data_analyst",
        model=model,
        description="Performs data analysis and generates insights from data",
        instruction=DATA_ANALYST_INSTRUCTION,
    )

    transfer_agent = TransferAgent(
        remote_agent,
        sub_agents=[data_analyst],
        model=model,
        transfer_instruction=TRANSFER_INSTRUCTION,
    )
    return transfer_agent


root_agent = create_agent()
