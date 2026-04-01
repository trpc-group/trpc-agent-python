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
from trpc_agent_sdk.tools import FunctionTool

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


# ============================================================================
# Tool Functions
# ============================================================================


def get_user_interests_analysis(interests: List[str]) -> dict:
    """Analyze user interests and return relevant suggestions"""
    # Simulate interest analysis API call
    interest_analysis = {
        "programming": {
            "personality": "Logical thinking",
            "activities": ["Programming marathon", "Open source project", "Technical conference"]
        },
        "fitness": {
            "personality": "Self-discipline",
            "activities": ["Gym", "Outdoor activity", "Marathon"]
        },
    }

    analysis_result = {"personality_traits": [], "recommended_activities": []}

    for interest in interests:
        if interest in interest_analysis:
            analysis_result["personality_traits"].append(interest_analysis[interest]["personality"])
            analysis_result["recommended_activities"].extend(interest_analysis[interest]["activities"])

    return analysis_result


def calculate_profile_score(age: int, interests: List[str], location: str) -> int:
    """Calculate the completeness score of user profiles"""
    score = 5  # Base score

    # Age Score
    if 18 <= age <= 60:
        score += 2
    else:
        score += 1

    # Interest Quantity Score
    if len(interests) >= 3:
        score += 2
    elif len(interests) >= 1:
        score += 1

    # Location Information Score
    if location:
        score += 1

    return min(score, 10)  # Max 10 points


# ============================================================================
# Agent Definition
# ============================================================================


def _get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL,
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name


def create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = _get_model_config()
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
        description="A professional user profile analysis assistant, providing personalized personality analysis and activity suggestions",
        model=create_model(),
        instruction="""
You are a professional user profile analysis assistant, able to analyze user information and provide personalized suggestions.

**Your task:**
- Analyze user information and interests
- Use tools to obtain interest analysis and scoring
- Provide structured analysis results
""",
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
        model=create_model(),
        # Deepseek currently only supports the configuration of response_format as json_object,
        # so it is necessary to explicitly specify the expected JSON output format for the LLM in the prompt.
        instruction="""You are a professional user profile analysis assistant, able to directly analyze user information and output JSON format results.

**Your task:**
- Analyze user information and interests
- Infer personality traits and recommended activities based on user information
- Directly output JSON format results conforming to the UserProfileOutput schema

**Output requirements:**
- Must strictly output JSON according to the UserProfileOutput schema
- Do not use any tools, analyze based on user information directly
- Ensure that all required fields have reasonable values

**UserProfileOutput structure:**
{
    "user_name": "User name",
    "age_group": "young|adult|senior",
    "personality_traits": ["Personality trait 1", "Personality trait 2"],
    "recommended_activities": ["Recommended activity 1", "Recommended activity 2"],
    "profile_score": 1-10 score,
    "summary": "Analysis summary"
}
""",
        tools=[],  # Do not use any tools
        input_schema=UserProfileInput,  # Expected structured JSON input
        output_schema=UserProfileOutput,  # Provide structured JSON output
        output_key="direct_profile_analysis",  # Save the result to the session state
    )
