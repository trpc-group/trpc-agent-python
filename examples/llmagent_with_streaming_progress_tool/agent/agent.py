# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent that uses StreamingProgressTool for a long-running task."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import StreamingProgressTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import crawl_site


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Build the agent. ``crawl_site`` is wrapped in
    ``StreamingProgressTool(skip_summarization=True)`` so that:

    1. Every ``yield`` becomes a partial Event the caller renders live.
    2. The last ``yield`` is also the final ``function_response`` event –
       persisted to the session as the canonical record of this turn.
    3. ``skip_summarization=True`` makes :class:`LlmAgent` exit the
       conversation loop immediately after the tool returns, so the LLM
       is **not** asked to re-summarise the streamed output (which the
       user has already seen).
    """
    crawl_tool = StreamingProgressTool(crawl_site, skip_summarization=True)

    return LlmAgent(
        name="streaming_crawler",
        description="Crawls a site step-by-step and streams progress to the user.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[crawl_tool],
        generate_content_config=GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=1000,
        ),
    )


root_agent = create_agent()
