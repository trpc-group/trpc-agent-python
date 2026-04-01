# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent transfer tool for TRPC Agent framework.

This module provides the transfer_to_agent tool that allows LLM agents to
transfer control to other agents in the agent hierarchy.
"""

from __future__ import annotations

from trpc_agent_sdk.context import InvocationContext


def transfer_to_agent(agent_name: str, tool_context: InvocationContext) -> dict:
    """Transfer the question to another agent.

    This tool hands off control to another agent when it's more suitable to
    answer the user's question according to the agent's description.

    Args:
        agent_name: The agent name to transfer to.
        tool_context: The invocation context containing the current agent and session.

    Returns:
        dict: Empty result dict (transfer is handled via actions)
    """
    # Set the transfer action on the event actions
    tool_context.actions.transfer_to_agent = agent_name

    # Return empty dict as tools should typically return JSON-serializable output
    return {"transferred_to": agent_name}
