# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
