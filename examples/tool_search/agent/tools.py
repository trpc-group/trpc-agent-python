# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool factories for the real tool-safety agent demo."""

from __future__ import annotations

import uuid

from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import ToolSearchOptions
from trpc_agent_sdk.tools import ToolSearchToolbox
from trpc_agent_sdk.tools import ToolSearchToolSet


async def calculator(expression: str) -> dict:
    """Evaluate a basic arithmetic expression."""
    allowed = set("0123456789.+-*/() ")
    if not expression or any(char not in allowed for char in expression):
        return {"error": "Only basic arithmetic expressions are supported."}
    return {"expression": expression, "result": eval(expression, {"__builtins__": {}}, {})}


async def get_weather(city: str) -> dict:
    """Get the current weather for a city."""
    return {"city": city, "condition": "sunny", "temperature_c": 25}


async def get_forecast(city: str, days: int = 3) -> dict:
    """Get a multi-day weather forecast for a city."""
    return {
        "city": city,
        "days": days,
        "forecast": ["sunny", "cloudy", "sunny"][:days],
    }


async def create_invoice(customer: str, amount: float) -> dict:
    """Create an invoice for a customer."""
    return {
        "invoice_id": f"INV-{uuid.uuid4().hex[:8]}",
        "customer": customer,
        "amount": amount,
    }


async def refund_payment(payment_id: str, reason: str) -> dict:
    """Refund a payment and record the reason."""
    return {
        "payment_id": payment_id,
        "reason": reason,
        "status": "refunded",
    }


def create_tool_search_toolset(options: ToolSearchOptions | None = None) -> ToolSearchToolSet:
    """Create an Agent whose domain tools are loaded on demand."""
    tool_search = ToolSearchToolSet(
        preset_tools=[FunctionTool(calculator)],
        toolboxes=[
            ToolSearchToolbox(
                name="weather",
                description="weather observations and forecasts",
                tools=[
                    FunctionTool(get_weather),
                    FunctionTool(get_forecast),
                ],
            ),
            ToolSearchToolbox(
                name="billing",
                description="invoices, payments, and refunds",
                tools=[
                    FunctionTool(create_invoice),
                    FunctionTool(refund_payment),
                ],
            ),
        ],
        options=options,
    )
    return tool_search
