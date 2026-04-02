# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent definition module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.ag_ui import get_agui_http_req
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


async def before_agent_callback(context: InvocationContext):
    """Simple callback example for reading AG-UI HTTP request."""
    request = get_agui_http_req(context)
    logger.info(f"AG-UI request: method={request.method} path={request.url.path} http_hdrs={request.headers}")
    return None


def create_agent() -> LlmAgent:
    """Create a weather query agent for AG-UI service."""

    # Create tools
    weather_tool = FunctionTool(get_weather)

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather information.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
        before_agent_callback=before_agent_callback,
        generate_content_config=GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            max_output_tokens=1500,
        ),
    )


root_agent = create_agent()
