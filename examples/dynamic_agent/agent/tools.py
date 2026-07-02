# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Workspace tools for the dynamic_agent demo."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trpc_agent_sdk.tools import FunctionTool


def calculator(operation: str, a: float, b: float) -> dict:
    """Perform one basic arithmetic operation (add, subtract, multiply, divide) on two numbers.

    Args:
        operation: The operation to perform (add, subtract, multiply, divide).
        a: First number.
        b: Second number.

    Returns:
        A dictionary with the operands, operation, and result (or error).
    """
    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    elif operation == "divide":
        if b == 0:
            return {
                "operation": operation,
                "a": a,
                "b": b,
                "error": "Division by zero",
            }
        result = a / b
    else:
        return {
            "operation": operation,
            "a": a,
            "b": b,
            "error": f"Unknown operation: {operation!r}",
        }
    return {
        "operation": operation,
        "a": a,
        "b": b,
        "result": result,
    }


def current_time(timezone: str = "") -> dict:
    """Get the current time and date for a timezone (UTC, EST, PST, CST, or local).

    Args:
        timezone: Timezone name (UTC, EST, PST, CST) or leave empty for local.

    Returns:
        Current time, date, and weekday for the requested timezone.
    """
    tz_map = {
        "UTC": ZoneInfo("UTC"),
        "EST": ZoneInfo("America/New_York"),
        "PST": ZoneInfo("America/Los_Angeles"),
        "CST": ZoneInfo("America/Chicago"),
    }
    key = timezone.strip().upper()
    if key and key not in tz_map:
        raise ValueError(f"unsupported timezone {timezone!r}")
    now = datetime.now(tz_map.get(key) if key else None)
    return {
        "timezone": timezone or "local",
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
    }


def word_count(text: str) -> dict:
    """Count the words and characters in a piece of text.

    Args:
        text: The text to analyze.

    Returns:
        Word and character counts for the input text.
    """
    trimmed = text.strip()
    words = len(trimmed.split()) if trimmed else 0
    return {
        "words": words,
        "characters": len(text),
    }


def create_workspace_tools() -> list[FunctionTool]:
    """Return the three workspace tools used by the orchestrator and sub-agents."""
    return [
        FunctionTool(calculator),
        FunctionTool(current_time),
        FunctionTool(word_count),
    ]
