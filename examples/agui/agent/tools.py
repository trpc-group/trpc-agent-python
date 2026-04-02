# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the agent."""


def get_weather(city: str) -> dict:
    """Get weather information for the specified city.
    
    Args:
        city: The name of the city to query weather for.
        
    Returns:
        A dictionary containing temperature, condition, and humidity.
    """
    # Simulated weather API call
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
