# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TimelineFilterMode
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(timeline_mode: TimelineFilterMode = TimelineFilterMode.ALL) -> LlmAgent:
    """Create an agent with specific timeline filter mode.

    Args:
        timeline_mode: The timeline filter mode to use.
                       ALL means full conversation history (default).
                       INVOCATION means invocation-scoped history.
    """
    agent = LlmAgent(
        name="timeline_demo",
        description="Agent demonstrating timeline filtering",
        model=_create_model(),
        instruction=INSTRUCTION,
        message_timeline_filter_mode=timeline_mode,
    )
    return agent


root_agent = create_agent()
