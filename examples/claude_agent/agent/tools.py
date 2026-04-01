# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """


def get_weather(city: str) -> dict:
    """Get weather information for the specified city.

    Args:
        city: The name of the city to get weather for.

    Returns:
        A dictionary containing weather information.
    """
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
    return weather_data.get(city, {
        "city": city,
        "temperature": "Unknown",
        "condition": "Data not available",
        "humidity": "Unknown"
    })
