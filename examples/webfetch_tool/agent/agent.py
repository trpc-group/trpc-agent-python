# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent module for WebFetchTool"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import WebFetchTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the LLM model used by every demo agent."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_default_fetch_agent() -> LlmAgent:
    """Build the baseline webfetch agent.

    The construction below pins every HTTP-shape knob explicitly so the
    example doubles as inline documentation:

    - ``timeout`` — lower than the 30s default so transient network
      hiccups surface quickly in the demo.
    - ``user_agent`` — identifies the demo on the wire so downstream
      logs can tell example traffic apart from real deployments.
    - ``max_content_length`` — cap returned ``content`` text so the
      demo output stays readable even for long pages.
    - ``max_response_bytes`` — hard byte budget on the raw wire read
      (~1 MiB here) that protects decode / memory budgets before the
      char cap kicks in.
    - ``follow_redirects`` / ``max_redirects`` — keep the redirect
      loop bounded while still handling ``http`` → ``https`` hops.
    - ``block_private_network`` — default-on SSRF boundary; pinned
      here to make the intent explicit.
    """
    web_fetch = WebFetchTool(
        timeout=10.0,
        user_agent="trpc-agent-python-webfetch-example/1.0",
        max_content_length=4000,
        max_response_bytes=1 * 1024 * 1024,
        follow_redirects=True,
        max_redirects=3,
        block_private_network=True,
    )
    return LlmAgent(
        name="default_webfetch_assistant",
        description="Web-reading assistant that fetches a single URL and summarises its textual content.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )


def create_cached_fetch_agent() -> LlmAgent:
    """Build a webfetch agent that enables the in-process LRU cache.

    Cache knobs covered:

    - ``enable_cache=True`` — opt in to the URL → ``FetchResult`` LRU.
    - ``cache_ttl_seconds`` — how long an entry is considered fresh.
    - ``cache_max_bytes`` — total byte budget for the cache; entries
      larger than this limit are silently skipped.

    The demo fetches the same URL twice in a row so the second call
    comes back with ``cached=true`` on the tool response.
    """
    web_fetch = WebFetchTool(
        timeout=10.0,
        user_agent="trpc-agent-python-webfetch-example/1.0",
        max_content_length=4000,
        max_response_bytes=1 * 1024 * 1024,
        enable_cache=True,
        cache_ttl_seconds=120.0,
        cache_max_bytes=2 * 1024 * 1024,
    )
    return LlmAgent(
        name="cached_webfetch_assistant",
        description=("Web-reading assistant with an in-process LRU cache so repeated "
                     "fetches of the same URL skip the network."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )


def create_whitelist_fetch_agent() -> LlmAgent:
    """Build a webfetch agent that only permits a closed set of hosts.

    Configures ``allowed_domains`` so every non-whitelisted URL is
    rejected with ``BLOCKED_URL`` before the HTTP GET is issued. The
    matching is subdomain-aware (``www.`` stripped), so ``python.org``
    also lets ``docs.python.org`` through.
    """
    web_fetch = WebFetchTool(
        timeout=10.0,
        user_agent="trpc-agent-python-webfetch-example/1.0",
        max_content_length=4000,
        allowed_domains=["python.org"],
    )
    return LlmAgent(
        name="whitelist_webfetch_assistant",
        description=("Web-reading assistant restricted to a domain whitelist; "
                     "off-list URLs are rejected with BLOCKED_URL."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )


def create_ssrf_fetch_agent() -> LlmAgent:
    """Build a webfetch agent that focuses on the SSRF guard."""
    web_fetch = WebFetchTool(
        timeout=10.0,
        user_agent="trpc-agent-python-webfetch-example/1.0",
        max_content_length=4000,
        follow_redirects=True,
        max_redirects=3,
        block_private_network=True,
    )
    return LlmAgent(
        name="ssrf_webfetch_assistant",
        description=("Web-reading assistant with an SSRF guard that rejects loopback / private / "
                     "link-local / reserved / multicast / unspecified targets on every hop."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )


def create_blocklist_fetch_agent() -> LlmAgent:
    """Build a webfetch agent that rejects a named set of hosts.

    Configures ``blocked_domains`` so any URL whose host matches
    (subdomain-aware, ``www.`` stripped) is rejected with
    ``BLOCKED_URL``. Blocks are evaluated *before* the allow list, so a
    host present in both lists is still rejected.
    """
    web_fetch = WebFetchTool(
        timeout=10.0,
        user_agent="trpc-agent-python-webfetch-example/1.0",
        max_content_length=4000,
        blocked_domains=["example.com"],
    )
    return LlmAgent(
        name="blocklist_webfetch_assistant",
        description=("Web-reading assistant with a domain blacklist; "
                     "blacklisted URLs are rejected with BLOCKED_URL."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[web_fetch],
    )


default_fetch_agent = create_default_fetch_agent()
cached_fetch_agent = create_cached_fetch_agent()
whitelist_fetch_agent = create_whitelist_fetch_agent()
blocklist_fetch_agent = create_blocklist_fetch_agent()
ssrf_fetch_agent = create_ssrf_fetch_agent()
root_agent = default_fetch_agent
