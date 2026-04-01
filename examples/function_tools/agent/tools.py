# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the agent.

Demonstrates two approaches to create FunctionTools:
  1. Wrap plain functions with FunctionTool directly.
  2. Use the @register_tool decorator and retrieve via get_tool.
"""

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import register_tool


# Approach 1: Plain functions wrapped by FunctionTool
async def get_weather(city: str) -> dict:
    """Get weather information for the specified city.

    Args:
        city: City name, e.g. "Beijing", "Shanghai".

    Returns:
        A dict with temperature, condition and humidity.
    """
    weather_data = {
        "Beijing": {
            "temperature": "15°C",
            "condition": "Sunny",
            "humidity": "45%",
        },
        "Shanghai": {
            "temperature": "18°C",
            "condition": "Cloudy",
            "humidity": "65%",
        },
        "Shenzhen": {
            "temperature": "25°C",
            "condition": "Light Rain",
            "humidity": "80%",
        },
    }

    if city in weather_data:
        return {
            "status": "success",
            "city": city,
            **weather_data[city],
            "last_updated": "2024-01-01T12:00:00Z",
        }
    return {
        "status": "error",
        "error_message": f"Weather data for {city} is not available",
        "supported_cities": list(weather_data.keys()),
    }


async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic mathematical calculations.

    Args:
        operation: The operation to perform (add, subtract, multiply, divide).
        a: First number.
        b: Second number.

    Returns:
        The calculation result.
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }

    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")

    return operations[operation](a, b)


# Pydantic models for structured tool input/output


class City(BaseModel):
    """City information."""
    city: str = Field(..., description="City name")


class Address(BaseModel):
    """Address information for postal code query."""
    city: City = Field(..., description="City information")
    province: str = Field(..., description="Province name")


class PostalCodeInfo(BaseModel):
    """Postal code query result."""
    city: str = Field(..., description="City name")
    postal_code: str = Field(..., description="Postal code")


def get_postal_code(addr: Address) -> PostalCodeInfo:
    """Get postal code for the given address.

    Args:
        addr: An Address object containing city and province.

    Returns:
        A PostalCodeInfo object with the postal code.
    """
    cities = {
        "Guangdong": {
            "Shenzhen": "518000",
            "Guangzhou": "518001",
            "Zhuhai": "518002",
        },
        "Jiangsu": {
            "Nanjing": "320000",
            "Suzhou": "320001",
        },
    }
    postal_code = cities.get(addr.province, {}).get(addr.city.city, "Unknown")
    return PostalCodeInfo(city=addr.city.city, postal_code=postal_code)


# Approach 2: Decorator-registered tool (retrieved via get_tool)


@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """Get current session information.

    The ``tool_context`` parameter is auto-injected by the framework;
    callers do not need to supply it.

    Returns:
        Basic information about the current session.
    """
    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }
