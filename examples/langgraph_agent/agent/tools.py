# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the LangGraph agent. """

from langchain_core.tools import tool
from trpc_agent_sdk.agents import langgraph_tool_node


@tool
@langgraph_tool_node
def calculate(operation: str, a: float, b: float) -> str:
    """Perform basic mathematical operations.

    Args:
        operation: Operation type ('add', 'subtract', 'multiply', 'divide')
        a: First number
        b: Second number

    Returns:
        Calculation result as a string
    """
    try:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                return "Error: Cannot divide by zero"
            result = a / b
        else:
            return f"Error: Unknown operation '{operation}'"

        return f"Calculation result: {a} {operation} {b} = {result}"
    except Exception as e:  # pylint: disable=broad-except
        return f"Calculation error: {str(e)}"
