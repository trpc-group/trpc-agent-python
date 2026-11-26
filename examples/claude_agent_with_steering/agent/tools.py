# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Weather tool used by the Claude steering example. """

import asyncio


async def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city.

    The delay leaves time for a steering message to arrive while the tool is
    running.
    """
    print(f"[Tool executing: fetching weather for {city}...]", flush=True)
    await asyncio.sleep(2)
    print(f"[Tool completed: got result for {city}]", flush=True)

    weather_data = {
        "Beijing": {
            "city": "Beijing",
            "temperature": "25C",
            "condition": "Sunny",
            "humidity": "60%",
        },
        "Shanghai": {
            "city": "Shanghai",
            "temperature": "20C",
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
