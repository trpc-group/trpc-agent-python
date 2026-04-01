---
name: weather-tools
description: Weather information query tools including current weather, forecast, and location search.
---

Tools:
- get_current_weather
- get_weather_forecast
- search_city_by_name

Overview

This skill provides weather-related query tools. Once this skill is loaded,
you will gain access to three powerful weather tools:

1. **get_current_weather**: Query current weather conditions for any city
2. **get_weather_forecast**: Get 3-day weather forecast
3. **search_city_by_name**: Search for city information by name

These tools are dynamically loaded when you load this skill, and they will
not consume tokens in the LLM context until the skill is actually loaded.

Usage Pattern

1. First, call `skill_load` to load this skill:
   ```
   skill_load(skill="weather-tools", include_all_docs=False)
   ```

2. After loading, you can use the weather tools directly:
   ```
   get_current_weather(city="Beijing")
   get_weather_forecast(city="Shanghai", days=3)
   search_city_by_name(name="New York")
   ```

Benefits

- **Token Efficiency**: Weather tools are only available after loading this skill,
  saving context tokens when not needed.
- **Dynamic Loading**: Tools are registered on-demand based on user needs.
- **Organized Tooling**: Related tools are grouped together as a skill.

Examples

Example 1: Query current weather
   ```
   # First load the skill
   skill_load(skill="weather-tools")

   # Then use the tool
   get_current_weather(city="Tokyo")
   ```

Example 2: Get weather forecast
   ```
   # First load the skill
   skill_load(skill="weather-tools")

   # Then use the tool
   get_weather_forecast(city="London", days=3)
   ```

Example 3: Search for a city
   ```
   # First load the skill
   skill_load(skill="weather-tools")

   # Then use the tool
   search_city_by_name(name="Paris")
   ```

Example 4: Ask someone name information

   ```
   # First load the skill
   skill_load(skill="weather-tools")

   # Then use the tool ask_name_information do not use search_city_by_name
   ask_name_information(name="Alice", country="China")
   ```
