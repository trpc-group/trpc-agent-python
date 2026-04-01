# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

from typing import Any
from typing import Dict

def get_weather(city: str) -> Dict[str, Any]:
    """Query the current weather of a specified city."""
    weather_data = {
        "Beijing": {
            "temperature": 15,
            "condition": "Sunny",
            "humidity": 45,
            "wind_speed": 10
        },
        "Shanghai": {
            "temperature": 18,
            "condition": "Cloudy",
            "humidity": 60,
            "wind_speed": 15
        },
        "Shenzhen": {
            "temperature": 25,
            "condition": "Sunny",
            "humidity": 70,
            "wind_speed": 8
        },
        "Hangzhou": {
            "temperature": 20,
            "condition": "Light rain",
            "humidity": 85,
            "wind_speed": 12
        },
    }
    result = weather_data.get(city, {"temperature": 20, "condition": "Unknown", "humidity": 50, "wind_speed": 10})
    return {"city": city, **result}


def get_weather_forecast(city: str, days: int = 3) -> Dict[str, Any]:
    """Query the weather forecast for a specified city for the next few days."""
    return {
        "city": city,
        "forecast": [{
            "date": "today",
            "temperature": "20°C",
            "condition": "Sunny"
        }] * days,
    }


def get_air_quality(city: str) -> Dict[str, Any]:
    """Query the air quality of a specified city."""
    aqi_data = {"Beijing": 85, "Shanghai": 72, "Shenzhen": 65, "Hangzhou": 90, "Guangzhou": 78}
    aqi = aqi_data.get(city, 75)
    level = "Good" if aqi <= 50 else "Fair" if aqi <= 100 else "Moderate"
    return {"city": city, "aqi": aqi, "level": level}


def get_uv_index(city: str) -> Dict[str, Any]:
    """Query the UV index of a specified city."""
    uv_data = {"Beijing": 5, "Shanghai": 6, "Shenzhen": 8, "Hangzhou": 4, "Guangzhou": 7}
    uv = uv_data.get(city, 5)
    suggestion = "Wear sunscreen" if uv >= 6 else "Outdoor suitable"
    return {"city": city, "uv_index": uv, "suggestion": suggestion}
