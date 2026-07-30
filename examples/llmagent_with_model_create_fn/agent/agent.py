# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


async def create_model(custom_data: dict) -> LLMModel:
    """Model creation function"""
    api_key, url, model_name = get_model_config()

    # Print custom_data to show it's received
    print(f"📦 Model creation function received custom_data: {custom_data}")

    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a weather query agent using model creation function."""

    # Create tool
    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent",
        description=
        "A professional weather query assistant that can provide real-time weather and forecast information.",
        model=create_model,  # Pass the model creation function
        instruction=INSTRUCTION,
        tools=[weather_tool],
        generate_content_config=GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            max_output_tokens=1500,
        ),
    )


root_agent = create_agent()
