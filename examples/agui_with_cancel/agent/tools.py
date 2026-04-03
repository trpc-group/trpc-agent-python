# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

import asyncio


async def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city.

    This function simulates a slow API call (2 seconds) to demonstrate
    cancellation during tool execution.
    """
    print(f"[Tool executing: fetching weather for {city}...]", flush=True)
    await asyncio.sleep(2)
    print(f"[Tool executing: weather for {city} fetched]", flush=True)

    weather_data = {
        "Beijing": {
            "city": "Beijing",
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%"
        },
        "Shanghai": {
            "city": "Shanghai",
            "temperature": "20°C",
            "condition": "Sunny",
            "humidity": "80%"
        },
    }
    result = weather_data.get(city, {
        "city": city,
        "temperature": "Unknown",
        "condition": "Data not available",
        "humidity": "Unknown"
    })
    print(f"[Tool completed: got result for {city}]", flush=True)
    return result
