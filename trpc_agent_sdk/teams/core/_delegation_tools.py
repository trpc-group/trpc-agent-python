# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Delegation tools for TeamAgent.

This module provides factory functions that create delegation tools for the
team leader. These tools return DelegationSignal Pydantic models that are
detected by TeamAgent to trigger member execution.
"""

from __future__ import annotations

from typing import List

from trpc_agent_sdk.tools import FunctionTool

from ._delegation_signal import DelegationSignal

# Tool name for delegation tool
DELEGATE_TOOL_NAME = "delegate_to_member"


def create_delegate_to_member_tool(member_names: List[str]) -> FunctionTool:
    """Create a delegation tool for delegating to a specific team member.

    This tool returns a DelegationSignal Pydantic model. The framework
    serializes it as the function response. TeamAgent detects this signal
    after leader agent completes and handles member execution.

    Args:
        member_names: List of available member agent names.

    Returns:
        FunctionTool configured for single-member delegation.
    """
    members_list = ", ".join(member_names)

    def delegate_to_member(member_name: str, task: str) -> DelegationSignal:
        """Delegate a task to a specific team member.

        Use this tool to assign a task to one of your team members. The member
        will execute the task and return their response.

        Args:
            member_name: Name of the member to delegate to.
                        Available members: {members}
            task: The task description for the member to execute.
                 Be specific about what you want the member to do.

        Returns:
            The member's response after completing the task.
        """
        return DelegationSignal(
            action="delegate_to_member",
            member_name=member_name,
            task=task,
        )

    # Format docstring with actual member names
    delegate_to_member.__doc__ = delegate_to_member.__doc__.format(  # type: ignore
        members=members_list)

    return FunctionTool(func=delegate_to_member)
