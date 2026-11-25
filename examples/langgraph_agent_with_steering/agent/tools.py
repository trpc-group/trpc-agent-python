# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Weather tool used by the LangGraph steering example. """

import time

from langchain_core.tools import tool
from trpc_agent_sdk.agents import langgraph_tool_node


@tool
@langgraph_tool_node
def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city.

    The one-second delay leaves time for a steering message to arrive while
    the tool is running.
    """
    print(f"[Tool executing: fetching weather for {city}...]", flush=True)
    time.sleep(1)
    print(f"[Tool completed: got result for {city}]", flush=True)

    weather_data = {
        "Beijing": {
            "city": "Beijing",
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%",
        },
        "Shanghai": {
            "city": "Shanghai",
            "temperature": "20°C",
            "condition": "Sunny",
            "humidity": "80%",
        },
    }
    return weather_data.get(city, {
        "city": city,
        "temperature": "Unknown",
        "condition": "Data not available",
        "humidity": "Unknown",
    })
