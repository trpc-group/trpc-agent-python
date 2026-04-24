# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent module for WebSearchTool"""

import httpx
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import WebSearchTool

from .config import get_google_cse_config
from .config import get_http_proxy
from .config import get_model_config
from .prompts import GOOGLE_INSTRUCTION
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the LLM model used by every demo agent."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_ddg_agent() -> LlmAgent:
    """Build an agent backed by the keyless DuckDuckGo provider.
    """
    web_search = WebSearchTool(
        provider="duckduckgo",
        results_num=3,
        snippet_len=300,
        title_len=80,
        timeout=10.0,
    )
    return LlmAgent(
        name="ddg_research_assistant",
        description="Web research assistant powered by DuckDuckGo Instant Answers.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )


def create_ddg_raw_agent() -> LlmAgent:
    """Build a DDG-backed agent that disables URL deduplication.
    """
    web_search = WebSearchTool(
        provider="duckduckgo",
        results_num=5,
        snippet_len=300,
        title_len=80,
        timeout=10.0,
        dedup_urls=False,
    )
    return LlmAgent(
        name="ddg_raw_research_assistant",
        description=("Web research assistant powered by DuckDuckGo Instant Answers "
                     "with URL deduplication disabled (raw provider hits)."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_search],
    )


_GOOGLE_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_shared_google_http_client() -> httpx.AsyncClient:
    """Lazily build (and remember) the shared httpx.AsyncClient."""
    global _GOOGLE_HTTP_CLIENT
    if _GOOGLE_HTTP_CLIENT is None:
        _GOOGLE_HTTP_CLIENT = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _GOOGLE_HTTP_CLIENT


async def aclose_shared_google_http_client() -> None:
    """Close the shared httpx.AsyncClient, if one was created.
    """
    global _GOOGLE_HTTP_CLIENT
    if _GOOGLE_HTTP_CLIENT is not None:
        await _GOOGLE_HTTP_CLIENT.aclose()
        _GOOGLE_HTTP_CLIENT = None


def create_google_agent() -> LlmAgent:
    """Build an agent backed by Google Custom Search (real web search)."""
    api_key, engine_id = get_google_cse_config()
    web_search = WebSearchTool(
        provider="google",
        api_key=api_key,
        engine_id=engine_id,
        user_agent="trpc-agent-python-websearch-demo/1.0 (+google-cse)",
        proxy=get_http_proxy(),
        lang="en",
        http_client=_get_shared_google_http_client(),
        results_num=3,
        snippet_len=240,
        title_len=80,
        timeout=15.0,
        dedup_urls=True,
        google_extra_params={"safe": "active"},
    )
    return LlmAgent(
        name="google_research_assistant",
        description="Web research assistant powered by Google Custom Search (SafeSearch on).",
        model=_create_model(),
        instruction=GOOGLE_INSTRUCTION,
        tools=[web_search],
    )


def create_google_raw_agent() -> LlmAgent:
    """Build a recency-biased Google agent that disables URL dedup."""
    api_key, engine_id = get_google_cse_config()
    web_search = WebSearchTool(
        provider="google",
        api_key=api_key,
        engine_id=engine_id,
        user_agent="trpc-agent-python-websearch-demo/1.0 (+google-cse-raw)",
        proxy=get_http_proxy(),
        lang="en",
        http_client=_get_shared_google_http_client(),
        results_num=5,
        snippet_len=320,
        title_len=100,
        timeout=20.0,
        dedup_urls=False,
        google_extra_params={"dateRestrict": "m6"},
    )
    return LlmAgent(
        name="google_raw_research_assistant",
        description=("Web research assistant powered by Google Custom Search "
                     "with URL deduplication disabled and a 6-month recency bias."),
        model=_create_model(),
        instruction=GOOGLE_INSTRUCTION,
        tools=[web_search],
    )


ddg_agent = create_ddg_agent()
ddg_raw_agent = create_ddg_raw_agent()
google_agent = create_google_agent()
google_raw_agent = create_google_raw_agent()
root_agent = ddg_agent
