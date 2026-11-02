"""Private tool fixtures shared by construction benchmarks."""

from __future__ import annotations

from typing import List

from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import FunctionTool


def add_numbers(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


def multiply_numbers(a: int, b: int) -> int:
    """Multiply two integers and return the product."""
    return a * b


def get_weather(city: str) -> str:
    """Return a fixed weather string for a city."""
    return f"sunny in {city}"


def get_time(city: str) -> str:
    """Return a fixed time string for a city."""
    return f"12:00 in {city}"


def get_news(topic: str) -> str:
    """Return a fixed news string for a topic."""
    return f"no news about {topic}"


def make_add_tool() -> List[BaseTool]:
    return [FunctionTool(add_numbers)]


def make_tools() -> List[BaseTool]:
    return [
        FunctionTool(add_numbers),
        FunctionTool(multiply_numbers),
        FunctionTool(get_weather),
        FunctionTool(get_time),
        FunctionTool(get_news),
    ]
