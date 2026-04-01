# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the agent."""

from trpc_agent_sdk.tools import register_tool


@register_tool("get_current_weather")
def get_current_weather(city: str, unit: str = "celsius") -> dict:
    """Get the current weather information for a specified city."""
    return {
        "city": city,
        "temperature": 22 if unit == "celsius" else 72,
        "unit": unit,
        "condition": "Partly Cloudy",
    }


@register_tool("get_weather_forecast")
def get_weather_forecast(city: str, days: int = 3) -> dict:
    """Get the weather forecast for a specified city for the next few days."""
    weather_forecast = {
        "city": city,
        "forecast_days": [{
            "date": "2026-01-15",
            "temperature": 22,
            "condition": "Partly Cloudy",
        } for _ in range(days)],
    }
    return weather_forecast


@register_tool("search_city_by_name")
def search_city_by_name(name: str) -> dict:
    """Search for city information by city name."""
    city_database = {
        "Beijing": {
            "name": "Beijing",
            "country": "China",
            "latitude": 39.9042,
            "longitude": 116.4074,
            "timezone": "Asia/Shanghai",
            "population": 21540000
        },
        "Shanghai": {
            "name": "Shanghai",
            "country": "China",
            "latitude": 31.2304,
            "longitude": 121.4737,
            "timezone": "Asia/Shanghai",
            "population": 24280000
        },
        "New York": {
            "name": "New York",
            "country": "USA",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "timezone": "America/New_York",
            "population": 8336817
        }
    }

    return city_database.get(name, {
        "name": name,
        "country": "Unknown",
        "latitude": 0.0,
        "longitude": 0.0,
        "timezone": "Unknown",
        "population": 0,
    })


def ask_name_information(name: str, country: str = "China") -> dict:
    """Ask for a person's name information."""
    return {
        "name": name,
        "age": 20,
        "gender": "male",
        "country": country,
    }
