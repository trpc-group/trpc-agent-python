#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""User profile analysis agent with input and output schemas"""

import os
from typing import List
from typing import Optional

from pydantic import BaseModel
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import AgentTool
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .prompts import INSTRUCTION_TOOL_WITH_SCHEMA
from .prompts import INSTRUCTION_WITHOUT_TOOLS
from .tools import calculate_profile_score
from .tools import get_user_interests_analysis

# ============================================================================
# Schema Definitions for Structured Data Exchange
# ============================================================================


class UserProfileInput(BaseModel):
    """Input schema for user profile creation."""

    name: str
    age: int
    email: str
    interests: List[str]
    location: Optional[str] = None


class UserProfileOutput(BaseModel):
    """Output schema for user profile analysis."""

    user_name: str
    age_group: str  # "young", "adult", "senior"
    personality_traits: List[str]
    recommended_activities: List[str]
    profile_score: int  # 1-10
    summary: str


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a user profile analysis agent with input and output schemas"""

    # Create tools
    interests_tool = FunctionTool(get_user_interests_analysis)
    score_tool = FunctionTool(calculate_profile_score)

    # Create LlmAgent with input and output schemas
    return LlmAgent(
        name="profile_analyzer",
        description=
        "A professional user profile analysis assistant, providing personalized personality analysis and activity suggestions",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[interests_tool, score_tool],
        input_schema=UserProfileInput,  # Expected structured JSON input
        output_schema=UserProfileOutput,  # Provide structured JSON output
        output_key="profile_analysis",  # Save the result to the session state
    )


def create_agent_without_tools():
    """Create an agent without tools and directly output the user profile analysis in JSON format"""

    return LlmAgent(
        name="direct_profile_analyzer",
        description="A direct user profile analysis assistant that outputs JSON format results",
        model=_create_model(),
        # Deepseek currently only supports the configuration of response_format as json_object,
        # so it is necessary to explicitly specify the expected JSON output format for the LLM in the prompt.
        instruction=INSTRUCTION_WITHOUT_TOOLS,
        tools=[],  # Do not use any tools
        input_schema=UserProfileInput,  # Expected structured JSON input
        output_schema=UserProfileOutput,  # Provide structured JSON output
        output_key="direct_profile_analysis",  # Save the result to the session state
    )


def create_agent_tool_with_schema():
    """Create an agent that can call the user profile analysis tool and return structured analysis result"""

    profile_agent = create_agent()
    profile_tool = AgentTool(agent=profile_agent)

    return LlmAgent(
        name="main_processor",
        description="Main processing Agent, can call the user profile analysis tool",
        model=_create_model(),
        instruction=INSTRUCTION_TOOL_WITH_SCHEMA,
        tools=[profile_tool],
    )
