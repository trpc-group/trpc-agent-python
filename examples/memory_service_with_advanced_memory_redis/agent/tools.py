"""Tools for the Advanced Memory Redis example."""


def get_weather_report(city: str) -> dict:
    """Return a small deterministic weather report for a city."""
    if city.lower() == "london":
        return {
            "status": "success",
            "report": (
                "The current weather in London is cloudy with a temperature of "
                "18 degrees Celsius and a chance of rain."
            ),
        }
    if city.lower() == "paris":
        return {
            "status": "success",
            "report": "The weather in Paris is sunny with a temperature of 25 degrees Celsius.",
        }
    return {
        "status": "error",
        "error_message": f"Weather information for '{city}' is not available.",
    }
