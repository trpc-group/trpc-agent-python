# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
This is a code example using the combined orchestration pattern of Parallel + Chain Agent.

Main Process: Parallel Analysis → Comprehensive Report
"""

from trpc_agent_sdk.agents import ChainAgent
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
    """Create a Combined Agent: Parallel Analysis + Comprehensive Report"""

    model = _create_model()

    # Quality Analysis Agent
    quality_analyst = LlmAgent(
        name="quality_analyst",
        model=model,
        description="Analyze content quality",
        instruction="""Analyze content quality: clarity, structure, completeness.
Provide quality score (1-10) and brief feedback.""",
        output_key="quality_analysis",
    )

    # Security Analysis Agent
    security_analyst = LlmAgent(
        name="security_analyst",
        model=model,
        description="Analyze security concerns",
        instruction="""Analyze security aspects: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.""",
        output_key="security_analysis",
    )

    # Create the Parallel Analysis Agent
    parallel_analysis_stage = ParallelAgent(
        name="parallel_analysis_team",
        description="Parallel quality and security analysis",
        sub_agents=[quality_analyst, security_analyst],
    )

    # Report Generation Agent
    report_generator = LlmAgent(
        name="report_generator",
        model=model,
        description="Generate comprehensive report",
        instruction="""Generate analysis report based on:

Quality Analysis: {quality_analysis}
Security Analysis: {security_analysis}

Create summary with overall assessment and recommendations.""",
        output_key="final_report",
    )

    # Combination: Parallel Analysis → Comprehensive Report
    return ChainAgent(
        name="analysis_pipeline",
        description="Parallel analysis → integrated report",
        sub_agents=[parallel_analysis_stage, report_generator],
    )


root_agent = create_agent()
