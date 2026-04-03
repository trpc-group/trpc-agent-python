# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the LangGraph agent. """

from langchain_core.tools import tool
from trpc_agent_sdk.agents import langgraph_tool_node


@tool
@langgraph_tool_node
def calculate(operation: str, a: float, b: float) -> str:
    """Perform basic mathematical operations.

    This function simulates a slow calculation (2 seconds) to demonstrate
    cancellation during tool execution in LangGraph.

    Args:
        operation: Operation type ('add', 'subtract', 'multiply', 'divide')
        a: First number
        b: Second number

    Returns:
        Calculation result as a string
    """
    # Simulate slow calculation - this gives us time to cancel
    print(f"[Tool executing: calculating {a} {operation} {b}...]", flush=True)

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

        print(f"[Tool completed: result = {result}]", flush=True)
        return f"Calculation result: {a} {operation} {b} = {result}"
    except Exception as e:  # pylint: disable=broad-except
        return f"Calculation error: {str(e)}"


@tool
@langgraph_tool_node
def analyze_data(data_type: str, sample_size: int) -> str:
    """Analyze data and generate statistical report.

    This function simulates a longer analysis (3 seconds) to demonstrate
    cancellation during extended tool execution.

    Args:
        data_type: Type of data to analyze ('sales', 'user_behavior', 'performance')
        sample_size: Number of data points to analyze

    Returns:
        Analysis report as a string
    """
    print(f"[Tool executing: analyzing {sample_size} {data_type} data points...]", flush=True)

    # Simulate data analysis result
    report = f"""
Data Analysis Report:
- Data Type: {data_type}
- Sample Size: {sample_size}
- Mean: 42.5
- Median: 40.0
- Std Dev: 15.3
- Key Insight: Data shows positive trend
"""
    print(f"[Tool completed: analysis done]", flush=True)
    return report.strip()
