#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import register_tool


# =============================================================================
# 1. Directly package functions to create tools
# =============================================================================
async def get_weather(city: str) -> dict:
    """Get weather information for the given city

    Args:
        city: City name, like "Beijing", "Shanghai" etc.

    Returns:
        A dictionary containing weather information, including temperature, weather condition and humidity
    """
    # Simulate weather API call
    weather_data = {
        "Beijing": {
            "temperature": "15°C",
            "condition": "Sunny",
            "humidity": "45%"
        },
        "Shanghai": {
            "temperature": "18°C",
            "condition": "Cloudy",
            "humidity": "65%"
        },
        "Shenzhen": {
            "temperature": "25°C",
            "condition": "Light rain",
            "humidity": "80%"
        },
    }

    if city in weather_data:
        return {"status": "success", "city": city, **weather_data[city], "last_updated": "2024-01-01T12:00:00Z"}
    else:
        return {
            "status": "error",
            "error_message": f"Not supported to query weather information for {city}",
            "supported_cities": list(weather_data.keys()),
        }


async def calculate(operation: str, a: float, b: float) -> float:
    """Perform basic mathematical calculations.

    Args:
        operation: The operation to perform (add, subtract, multiply, divide)
        a: First number
        b: Second number

    Returns:
        The result of the calculation
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }

    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")

    result = operations[operation](a, b)
    return result


class City(BaseModel):
    """Address information for weather query."""
    city: str = Field(..., description="City name")


class Address(BaseModel):
    """Address information for weather query."""
    city: City = Field(..., description="City name")
    province: str = Field(..., description="Province name")


class PostalCodeInfo(BaseModel):
    """Address information for weather query."""
    city: str = Field(..., description="City name")
    postal_code: str = Field(..., description="Postal code")


def get_postal_code(addr: Address) -> PostalCodeInfo:
    """Get postal code for the given address."""
    cities = {
        "Guangdong": {
            "Shenzhen": "518000",
            "Guangzhou": "518001",
            "Zhuhai": "518002",
        },
        "Jiangsu": {
            "Nanjing": "320000",
            "Suzhou": "320001",
        }
    }
    return PostalCodeInfo(city=addr.city.city, postal_code=cities.get(addr.province, {}).get(addr.city.city, "Unknown"))


# =============================================================================
# 2. Use decorator to register tools
# =============================================================================


@register_tool("get_session_info")
async def get_session_info(tool_context: InvocationContext) -> dict:
    """Get current session information

    Args:
        tool_context: Execution context (auto-injected)

    Returns:
        Basic information about the current session
    """
    # This should be the business logic, like:
    # - Async database query user information
    # - Call external API to get user status etc.
    # Here we directly get the session information through tool_context.session

    session = tool_context.session
    return {
        "status": "success",
        "session_id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
    }
