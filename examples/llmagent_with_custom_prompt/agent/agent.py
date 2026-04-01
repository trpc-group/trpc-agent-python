# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import InvocationContext
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import COORDINATOR_INSTRUCTION
from .prompts import CUSTOM_TRANSFER_MESSAGE
from .prompts import TRANSLATION_INSTRUCTION
from .prompts import WEATHER_INSTRUCTION
from .tools import get_weather_report
from .tools import translate_text


def _print_system_instruction(ctx: InvocationContext, req: LlmRequest):
    """before_model_callback: 打印实际发送给 LLM 的 system instruction，
    用于对比框架是否注入了名称和转发指令。"""
    agent_name = ctx.agent.name
    instruction = req.config.system_instruction if req.config else "(empty)"
    print(f"\n{'·' * 60}")
    print(f"📋 [{agent_name}] System Instruction sent to LLM:")
    print(f"{instruction}")
    print(f"{'·' * 60}\n")


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(
    add_name: bool = True,
    transfer_message: str | None = None,
) -> LlmAgent:
    """Create a coordinator agent with configurable prompt injection.

    Args:
        add_name: Whether the framework auto-injects agent name into instruction.
                  True (default): framework adds "You are an agent who's name is [name]."
                  False: no auto-injection, instruction is used as-is.
        transfer_message: Controls transfer instruction injection for sub_agents.
                          None (default): framework auto-injects transfer instructions.
                          "": disables auto-injection entirely.
                          Custom string: replaces framework default with this string.
    """
    model = _create_model()

    weather_assistant = LlmAgent(
        name="WeatherAssistant",
        model=model,
        description="Weather assistant who answers weather questions",
        instruction=WEATHER_INSTRUCTION,
        tools=[FunctionTool(get_weather_report)],
        add_name_to_instruction=add_name,
        before_model_callback=_print_system_instruction,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    translation_assistant = LlmAgent(
        name="TranslationAssistant",
        model=model,
        description="Translation assistant who translates text",
        instruction=TRANSLATION_INSTRUCTION,
        tools=[FunctionTool(translate_text)],
        add_name_to_instruction=add_name,
        before_model_callback=_print_system_instruction,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    coordinator = LlmAgent(
        name="Coordinator",
        model=model,
        description="Customer service coordinator",
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[weather_assistant, translation_assistant],
        add_name_to_instruction=add_name,
        default_transfer_message=transfer_message,
        before_model_callback=_print_system_instruction,
    )

    return coordinator


root_agent = create_agent()
