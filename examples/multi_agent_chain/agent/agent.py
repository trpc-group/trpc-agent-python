# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
This is an example of a Chain Agent for a sequentially executed document processing pipeline.

The main process is: Document Extraction → Content Translation → Format Optimization.

In this process, data is passed between Agents using the output_key.
"""

from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a chained Agent for document processing"""

    model = _create_model()

    # Step 1: Content Extraction Agent
    extractor_agent = LlmAgent(
        name="content_extractor",
        model=model,
        description="Extract key information from input text",
        instruction="Extract key information from the input text and structure it clearly. "
        "Focus on main points, features, and target audience. Please output in markdown format",
        output_key="extracted_content",  # Save the output to a state variable
    )

    # Step 2: Translation Agent, using the output of the previous Agent
    translator_agent = LlmAgent(
        name="translator",
        model=model,
        description="Translate content to English with professional formatting",
        instruction=
        """Translate the following extracted content to English while maintaining the original meaning and structure:

{extracted_content}

Provide a natural, professional English translation with proper formatting:
- Use clear headings and organized sections
- Apply professional document structure
- Include bullet points where appropriate
- Ensure readability and professional presentation
- Please output in markdown format""",
        output_key="translated_content",  # Save the output to a state variable
    )

    # Building a Chained Agent - Deterministic Sequential Execution.
    # Chain Agents always execute in the order specified by the sub_agents list,
    # regardless of input.State is passed between agents using output_key to enable data pipeline processing.
    return ChainAgent(
        name="document_processor",
        description="Sequential document processing: extract → translate",
        sub_agents=[extractor_agent, translator_agent],
    )


root_agent = create_agent()
