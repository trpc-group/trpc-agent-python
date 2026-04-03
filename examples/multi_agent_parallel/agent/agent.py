#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
This is an example of a Parallel Agent for parallel content review.、

Main Process: Quality Review + Security Review.

Agents pass information to each other using output_key.
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import ParallelAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a Parallel Review Agent"""

    model = _create_model()

    # Quality Review Agent
    quality_reviewer = LlmAgent(
        name="quality_reviewer",
        model=model,
        description="Review content quality",
        instruction="""Review content quality: clarity, accuracy, readability.
Provide quality score (1-10) and brief feedback.  Please output in markdown format.""",
        output_key="quality_review",
    )

    # Security Review Agent
    security_reviewer = LlmAgent(
        name="security_reviewer",
        model=model,
        description="Review content security",
        instruction="""Review security concerns: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.  Please output in markdown format.""",
        output_key="security_review",
    )

    # Create a Parallel Agent
    return ParallelAgent(
        name="review_panel",
        description="Parallel review: quality + security",
        sub_agents=[quality_reviewer, security_reviewer],
    )


root_agent = create_agent()
