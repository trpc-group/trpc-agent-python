#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool


# =============================================================================
# 1. Create a test toolset
# =============================================================================
async def sports(name: str) -> str:
    """play sports

    Args:
        name: sport name

    Returns:
        sport name and time
    """
    await asyncio.sleep(3)
    return f"{name} takes 3s"


async def watch_tv(tv: str):
    """watch tv

    Args:
        tv: tv channel

    Returns:
        The content of the TV
    """
    await asyncio.sleep(5)
    return f"{tv} is broadcasting News Network"


async def listen_music(music: str):
    """Listening to music

    Args:
        music: music name

    Returns:
        The content of the music
    """
    await asyncio.sleep(6)
    return f"{music} is playing light music"


class HobbyToolSet(BaseToolSet):
    """Hobby Toolkit, mainly describing sports, watching TV and listening to music"""

    def __init__(self):
        super().__init__()
        self.name = "hobby_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """Initialize the Toolkit"""
        super().initialize()
        self.tools = [
            FunctionTool(sports),
            FunctionTool(watch_tv),
            FunctionTool(listen_music),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        return self.tools


# =============================================================================
# 2. Create Agent
# =============================================================================


def _get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL,
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = _get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create an agent related to hobbies"""

    model = _create_model()

    # Get the registered toolkit
    hobby_toolset = HobbyToolSet()

    # Initialize the Toolkit
    if hobby_toolset:
        hobby_toolset.initialize()

    return LlmAgent(
        name="hobby_toolset_agent",
        description="A helper demonstrating the use of the ToolSet for hobbies",
        model=model,
        tools=[hobby_toolset],
        parallel_tool_calls=False,
        instruction="""
You are a virtual person who loves life, select the appropriate tool based on the user's interest to obtain interest information, and provide friendly replies.
**Your task:**
- If there is content related to running or sports in the conversation, you must call the sports tool, if no motion parameters are provided, the default is running
- If there is content related to TV or tv in the conversation, you must call the watch_tv tool, if no tv parameters are provided, the default is cctv
- If there is content related to music or music in the conversation, you must call the listen_music tool, if no music parameters are provided, the default is QQ music
""",
    )


root_agent = create_agent()
