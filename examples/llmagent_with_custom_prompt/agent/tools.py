# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """


def get_weather_report(city: str) -> dict:
    """Get weather information for the specified city"""
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
    }
    return weather_data.get(city, {"temperature": "Unknown", "condition": "Data not available", "humidity": "Unknown"})


def translate_text(text: str, target_language: str) -> str:
    """Translate text to the target language (simulated)"""
    translations = {
        ("hello", "chinese"): "你好",
        ("thank you", "chinese"): "谢谢",
        ("hello", "english"): "hello",
        ("thank you", "english"): "thank you",
    }
    key = (text.lower(), target_language.lower())
    return translations.get(key, f"[Translated '{text}' to {target_language}]")
