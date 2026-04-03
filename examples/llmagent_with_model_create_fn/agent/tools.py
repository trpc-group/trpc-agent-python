# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """


def get_weather_report(city: str) -> dict:
    """get weather information for the specified city"""
    # Simulate weather API invocation
    weather_data = {
        "Beijing": {
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%"
        },
    }
    return weather_data.get(city, {"temperature": "Unknown", "condition": "Data not available", "humidity": "Unknown"})
