# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import AgentTool
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import get_tool

from .config import get_model_config
from .function_tool import calculate
from .function_tool import get_postal_code
from .function_tool import get_weather
from .langchain_tool import tavily_search
from .prompts import FUNCTION_TOOL_INSTRUCTION
from .prompts import LANGCHAIN_TOOL_INSTRUCTION
from .prompts import MAIN_INSTRUCTION
from .prompts import TOOLSET_INSTRUCTION
from .prompts import TRANSLATOR_INSTRUCTION
from .toolset import WeatherToolSet


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def _create_translation_agent(model: LLMModel):
    """Create a professional translation agent"""
    return LlmAgent(
        name="translator",
        model=model,
        description="A professional text translation tool",
        instruction=TRANSLATOR_INSTRUCTION,
    )


def create_agent_tool_agent():
    """Create a main agent that can invoke translation tools"""

    model = _create_model()

    # Create a translation agent
    translator = _create_translation_agent(model)

    # Wrap the translation agent as an AgentTool
    translator_tool = AgentTool(agent=translator)

    return LlmAgent(
        name="content_processor",
        model=model,
        description="A content processing assistant that can invoke translation tools to handle multilingual content",
        instruction=MAIN_INSTRUCTION,
        tools=[translator_tool],
    )


def create_function_tool_agent():
    """Create an agent with function tools"""

    model = _create_model()

    # Create tools directly
    weather_tool = FunctionTool(get_weather)
    calculate_tool = FunctionTool(calculate)
    postal_code_tool = FunctionTool(get_postal_code)

    # Get tools from registry
    session_tool = get_tool("get_session_info")

    return LlmAgent(
        name="function_tool_demo_agent",
        description=
        "A assistant that can query weather information and get session information. Please select the appropriate tool based on the user's request.",
        model=model,
        instruction=FUNCTION_TOOL_INSTRUCTION,
        tools=[weather_tool, calculate_tool, session_tool, postal_code_tool],
    )


def create_langchain_tool_agent() -> LlmAgent:
    """Create an agent with LangChain tool"""

    model = _create_model()

    tavily_tool = FunctionTool(tavily_search)

    return LlmAgent(
        name="langchain_tool_agent",
        description="An assistant integrated with LangChain Tavily search tool",
        model=model,
        instruction=LANGCHAIN_TOOL_INSTRUCTION,
        tools=[tavily_tool],
    )


def create_toolset_agent():
    """Create an agent with ToolSet"""

    model = _create_model()
    # 获取注册的工具集
    weather_toolset = WeatherToolSet()

    # 初始化工具集
    if weather_toolset:
        weather_toolset.initialize()
    return LlmAgent(
        name="toolset_agent",
        description="An assistant demonstrating the usage of ToolSet",
        model=model,
        instruction=TOOLSET_INSTRUCTION,
        tools=[weather_toolset],
    )
