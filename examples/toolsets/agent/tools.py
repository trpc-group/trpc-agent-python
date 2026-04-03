# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool


class WeatherToolSet(BaseToolSet):
    """Weather tool set containing all weather-related tools"""

    def __init__(self):
        super().__init__()
        self.name = "weather_toolset"
        self.tools = []

    @override
    def initialize(self) -> None:
        """Initialize the tool set and create all weather-related tools"""
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),
            FunctionTool(self.get_weather_forecast),
        ]

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Dynamically return available tools based on user permissions"""
        if not invocation_context:
            return self.tools[:1]

        user_type = invocation_context.session.state.get("user_type", "basic")

        if user_type == "vip":
            return self.tools
        else:
            return self.tools[:1]

    @override
    async def close(self) -> None:
        """Clean up resources"""
        pass

    async def get_current_weather(self, city: str) -> dict:
        """Get the current weather for the specified city

        Args:
            city: City name, e.g. "Beijing", "Shanghai"

        Returns:
            Current weather information
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
                "timestamp": "2024-01-01T12:00:00Z",
            }
        else:
            return {
                "status": "error",
                "error_message": f"Weather data for {city} is not available",
                "supported_cities": list(weather_data.keys()),
            }

    async def get_weather_forecast(self, city: str, days: int = 3) -> dict:
        """Get the weather forecast for the specified city

        Args:
            city: City name
            days: Number of forecast days, default is 3

        Returns:
            Weather forecast information
        """
        return {
            "status":
            "success",
            "city":
            city,
            "forecast_days":
            days,
            "forecast": [{
                "date": f"2024-01-{i + 1:02d}",
                "temperature": f"{20 + i}°C",
                "condition": "Sunny",
            } for i in range(days)],
        }
