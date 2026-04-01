# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Tools for the agent. """


def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city.

    Args:
        city: The city name to query weather for

    Returns:
        A dictionary containing weather information for the city
    """
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
        "Guangzhou": {
            "city": "Guangzhou",
            "temperature": "32C",
            "condition": "Thunderstorm",
            "humidity": "85%"
        },
    }
    return weather_data.get(city, {
        "city": city,
        "temperature": "Unknown",
        "condition": "Data not available",
        "humidity": "Unknown"
    })
