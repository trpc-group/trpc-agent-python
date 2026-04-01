# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

import asyncio


async def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city.

    This function simulates a slow API call (3 seconds) to demonstrate
    cancellation during tool execution.
    """
    # Simulate slow API call - this gives us time to cancel
    print(f"[Tool executing: fetching weather for {city}...]", flush=True)
    await asyncio.sleep(2)
    print(f"[Tool executing: weather for {city} fetched]", flush=True)

    # Simulate weather API response
    weather_data = {
        "Beijing": {
            "city": "Beijing",
            "temperature": "25C",
            "condition": "Sunny",
            "humidity": "60%"
        },
        "Shanghai": {
            "city": "Shanghai",
            "temperature": "28C",
            "condition": "Cloudy",
            "humidity": "70%"
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
