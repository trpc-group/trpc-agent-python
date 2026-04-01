# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """


def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city"""
    # Simulate weather API invocation
    weather_data = {
        "Beijing": {
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%"
        },
        "Shanghai": {
            "temperature": "28°C",
            "condition": "Cloudy",
            "humidity": "70%"
        },
        "Guangzhou": {
            "temperature": "32°C",
            "condition": "Thunderstorm",
            "humidity": "85%"
        },
    }
    return weather_data.get(city, {"temperature": "Unknown", "condition": "Data not available", "humidity": "Unknown"})


def get_weather_forecast(city: str, days: int = 3) -> list:
    """Get the multi-day weather forecast for the specified city"""
    # Simulated forecast data
    return [
        {
            "date": "2024-01-01",
            "temperature": "25°C",
            "condition": "Sunny"
        },
        {
            "date": "2024-01-02",
            "temperature": "23°C",
            "condition": "Cloudy"
        },
        {
            "date": "2024-01-03",
            "temperature": "20°C",
            "condition": "Light rain"
        },
    ][:days]
