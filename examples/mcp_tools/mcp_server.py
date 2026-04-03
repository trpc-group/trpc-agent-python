#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" MCP server providing weather and calculation tools. """

import logging

from mcp.server import FastMCP

app = FastMCP("simple-tools")


@app.tool()
async def get_weather(location: str) -> str:
    """Get weather information for the specified location.

    Args:
        location: Name of the location.

    Returns:
        A string describing the weather.
    """
    weather_info = {
        "Beijing": "Sunny, 15°C, humidity 45%",
        "Shanghai": "Cloudy, 18°C, humidity 65%",
        "Shenzhen": "Light rain, 25°C, humidity 80%",
    }
    return weather_info.get(location, f"Weather data for {location} is not available")


@app.tool()
async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic math operations.

    Args:
        operation: Operation type (add, subtract, multiply, divide).
        a: First number.
        b: Second number.

    Returns:
        The calculation result.
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y,
    }
    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")
    if operation == "divide" and b == 0:
        raise ValueError("Division by zero is not allowed")
    return operations[operation](a, b)


if __name__ == "__main__":
    # Reduce MCP framework logs to error-only to keep demo output clean.
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("mcp").setLevel(logging.ERROR)
    logging.getLogger("mcp.server").setLevel(logging.ERROR)

    # Uncomment ONE of the following lines to select the transport mode:
    app.run(transport="stdio")
    # app.run(transport="sse")
    # app.run(transport="streamable-http")
