# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import os

from claude_agent_sdk.types import ClaudeAgentOptions
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_current_date

_EXAMPLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a travel planner agent to help user to plan their travel"""

    # Create tools
    current_date_tool = FunctionTool(get_current_date)

    # Initialize Claude Proxy Server
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082, claude_models={"all": _create_model()})

    return ClaudeAgent(
        name="travel_planner",
        description="a travel planner assistant that can help user to plan their travel",
        model=_create_model(),
        # Use state variables for template replacement - Demonstration of the {var} syntax
        instruction=INSTRUCTION,
        tools=[current_date_tool],
        # Configure Generation Parameters
        generate_content_config=GenerateContentConfig(
            temperature=0.3,  # Reduce randomness for more deterministic responses
            top_p=0.9,
            max_output_tokens=1500,
        ),
        claude_agent_options=ClaudeAgentOptions(
            cwd=_EXAMPLE_DIR,
            # setting_sources is the way of claude agent to get the skills from the user and the project
            # user is the way of claude agent to get the skills from path: ~/.claude/skills
            # project is the way of claude agent to get the skills from path: cwd/.claude/skills
            setting_sources=["user", "project"],
            # Skill Tool is the way of claude agent to use the skills,must be allowed
            allowed_tools=["Skill"],
        ),
        # Enable Planner to Enhance Reasoning Capabilities (Commented Out by Default)
        # Uncomment the line below to equip the model with reasoning capabilities,
        # allowing it to perform inference before generating responses
        # planner=PlanReActPlanner(),
    )


root_agent = create_agent()
