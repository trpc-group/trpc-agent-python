# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Tools for the agent. """

from langchain_core.tools import tool
from trpc_agent.agents import langgraph_tool_node


@tool
@langgraph_tool_node
def execute_database_operation(operation: str, database: str, details: dict) -> str:
    """Execute a database operation that requires approval.

    Args:
        operation: The type of operation ('delete', 'update', 'create')
        database: The database name
        details: Additional operation details
    """
    return f"Database operation '{operation}' on '{database}' executed successfully with details: {details}"
