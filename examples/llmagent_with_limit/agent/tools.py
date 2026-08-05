# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Weather tools for the run-limit example."""


def get_weather_report(city: str) -> dict[str, str]:
    """Return simulated current weather for a city."""
    weather_data = {
        "Beijing": {
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%",
        },
        "Shanghai": {
            "temperature": "28°C",
            "condition": "Cloudy",
            "humidity": "70%",
        },
    }
    return weather_data.get(
        city,
        {
            "temperature": "Unknown",
            "condition": "Data not available",
            "humidity": "Unknown",
        },
    )


def get_weather_forecast(city: str, days: int = 3) -> list[dict[str, str]]:
    """Return a simulated multi-day weather forecast for a city."""
    forecast = [
        {
            "date": "2024-01-01",
            "city": city,
            "temperature": "25°C",
            "condition": "Sunny",
        },
        {
            "date": "2024-01-02",
            "city": city,
            "temperature": "23°C",
            "condition": "Cloudy",
        },
        {
            "date": "2024-01-03",
            "city": city,
            "temperature": "20°C",
            "condition": "Light rain",
        },
    ]
    return forecast[:days]
