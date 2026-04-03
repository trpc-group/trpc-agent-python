#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool

# =============================================================================
# 1. Weather ToolSet
# =============================================================================


class WeatherToolSet(BaseToolSet):
    """Weather ToolSet, including all weather query tools"""

    def __init__(self):
        super().__init__()
        self.name = "weather_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """Initialize the ToolSet, create all weather related tools"""
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),
            FunctionTool(self.get_weather_forecast),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Dynamically return available tools based on user permissions"""
        if not invocation_context:
            return self.tools[:1]  # No context, only return basic functionality

        # Filter tools based on user type
        user_type = invocation_context.session.state.get("user_type", "basic")

        if user_type == "vip":
            return self.tools  # VIP users can use all tools
        else:
            return self.tools[:1]  # Basic users can only view current weather

    @override
    async def close(self) -> None:
        """Clean up resources"""
        # Here we can add cleanup logic, like closing database connections
        pass

    # Tool methods
    async def get_current_weather(self, city: str) -> dict:
        """Get the current weather for the specified city

        Args:
            city: City name, e.g. "Beijing", "Shanghai" etc.

        Returns:
            Current weather information
        """
        # Simulate weather data
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
            return {"status": "success", "city": city, **weather_data[city], "timestamp": "2024-01-01T12:00:00Z"}
        else:
            return {
                "status": "error",
                "error_message": f"Not supported to query weather information for {city}",
                "supported_cities": list(weather_data.keys()),
            }

    async def get_weather_forecast(self, city: str, days: int = 3) -> dict:
        """Get the weather forecast for the specified city

        Args:
            city: City name
            days: Forecast days, default 3 days

        Returns:
            Weather forecast information
        """
        # Simulate forecast data
        return {
            "status":
            "success",
            "city":
            city,
            "forecast_days":
            days,
            "forecast": [{
                "date": f"2024-01-{i+1:02d}",
                "temperature": f"{20+i}°C",
                "condition": "Sunny"
            } for i in range(days)],
        }
