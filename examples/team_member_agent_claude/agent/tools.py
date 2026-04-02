# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the Claude member team agents """


def get_weather(city: str) -> str:
    """Get the weather of a city.

    Args:
        city: The city name to get weather for.

    Returns:
        Weather information for the city.
    """
    weather_data = {
        "beijing": "Beijing: Sunny, 25C, humidity 45%",
        "shanghai": "Shanghai: Cloudy, 28C, humidity 65%",
        "shenzhen": "Shenzhen: Rainy, 30C, humidity 80%",
    }
    return weather_data.get(city.lower(), f"{city}: Partly cloudy, 22C, humidity 55%")
