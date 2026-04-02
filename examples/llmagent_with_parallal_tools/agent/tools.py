# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

import asyncio
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool


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
