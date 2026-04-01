# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Dynamic toolset creation for client-side tools."""

import asyncio
from typing import List
from typing import Optional

from ag_ui.core import Tool as AGUITool

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet

from ._client_proxy_tool import ClientProxyTool


class ClientProxyToolset(BaseToolSet):
    """Dynamic toolset that creates proxy tools from AG-UI tool definitions.

    This toolset is created for each run based on the tools provided in
    the RunAgentInput, allowing dynamic tool availability per request.
    """

    def __init__(self, ag_ui_tools: List[AGUITool], event_queue: asyncio.Queue):
        """Initialize the client proxy toolset.

        Args:
            ag_ui_tools: List of AG-UI tool definitions
            event_queue: Queue to emit AG-UI events
        """
        super().__init__()
        self.ag_ui_tools = ag_ui_tools
        self.event_queue = event_queue

        logger.info("Initialized ClientProxyToolset with %s tools (all long-running)", len(ag_ui_tools))

    async def get_tools(self, context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Get all proxy tools for this toolset.

        Creates fresh ClientProxyTool instances for each AG-UI tool definition
        with the current event queue reference.

        Args:
            context: Optional context for tool filtering (unused currently)

        Returns:
            List of ClientProxyTool instances
        """
        # Create fresh proxy tools each time to avoid stale queue references
        proxy_tools = []

        for ag_ui_tool in self.ag_ui_tools:
            try:
                proxy_tool = ClientProxyTool(ag_ui_tool=ag_ui_tool, event_queue=self.event_queue)
                proxy_tools.append(proxy_tool)
                logger.debug("Created proxy tool for '%s' (long-running)", ag_ui_tool.name)

            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Failed to create proxy tool for '%s': %s", ag_ui_tool.name, ex)
                # Continue with other tools rather than failing completely

        return proxy_tools

    async def close(self) -> None:
        """Clean up resources held by the toolset."""
        logger.info("Closing ClientProxyToolset")

    def __repr__(self) -> str:
        """String representation of the toolset."""
        tool_names = [tool.name for tool in self.ag_ui_tools]
        return f"ClientProxyToolset(tools={tool_names}, all_long_running=True)"
