#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Simple MCP server for the graph example (stdio transport).

Provides two tools:
  - get_weather: returns mock weather for a location
  - calculate: basic arithmetic (add, subtract, multiply, divide)
"""
from mcp.server import FastMCP

app = FastMCP("graph-example-tools")


@app.tool()
async def get_weather(location: str) -> str:
    """Get weather information for a location.

    Args:
        location: The location name.

    Returns:
        Weather description string.
    """
    weather_data = {
        "Beijing": "Sunny, 15°C, humidity 45%",
        "Shanghai": "Cloudy, 18°C, humidity 65%",
        "Seattle": "Rainy, 12°C, humidity 78%",
    }
    return weather_data.get(location, f"Weather for {location}: sunny, 20°C")


@app.tool()
async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic arithmetic.

    Args:
        operation: One of add, subtract, multiply, divide.
        a: First operand.
        b: Second operand.

    Returns:
        Calculation result.
    """
    ops = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }
    if operation not in ops:
        raise ValueError(f"Unsupported operation: {operation}")
    return ops[operation](a, b)


if __name__ == "__main__":
    app.run(transport="stdio")
