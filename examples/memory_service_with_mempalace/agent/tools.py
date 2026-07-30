# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """


def get_weather_report(city: str) -> dict:
    """Retrieves the current weather report for a specified city.

    Returns:
        dict: A dictionary containing the weather information with a 'status' key ('success' or 'error')
              and a 'report' key with the weather details if successful, or an 'error_message' if
              an error occurred.
    """
    if city.lower() == "london":
        return {
            "status":
            "success",
            "report": ("The current weather in London is cloudy with a temperature of "
                       "18 degrees Celsius and a chance of rain."),
        }
    elif city.lower() == "paris":
        return {
            "status": "success",
            "report": "The weather in Paris is sunny with a temperature of 25 degrees Celsius.",
        }
    else:
        return {"status": "error", "error_message": f"Weather information for '{city}' is not available."}
