# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Book finder agent: local library, bookstore, online retailers."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import find_local_bookstore
from .tools import order_online
from .tools import search_local_library


def _create_model() -> OpenAIModel:
    """Create model from config."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create the book finder agent."""
    return LlmAgent(
        name="agent",
        description="专业的书籍查找助手，可查询本地图书馆、书店和在线零售商",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(search_local_library),
            FunctionTool(find_local_bookstore),
            FunctionTool(order_online),
        ],
        generate_content_config=GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            max_output_tokens=1500,
        ),
    )


root_agent = create_agent()
