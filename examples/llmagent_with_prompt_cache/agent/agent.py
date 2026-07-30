# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Weather agent factory for three prompt-cache providers.

Each factory function shows exactly how to wire ``PromptCacheConfig`` for one
provider family:

* ``create_anthropic_agent`` – Anthropic / Claude.
  Uses explicit ``cache_control`` breakpoints (``tools`` + ``system``) to
  mark the stable prefix.  The provider stamps the cache automatically;
  ``cache_creation_input_tokens`` is reported on the first turn and
  ``cache_read_input_tokens`` on subsequent turns.

* ``create_openai_agent`` – Any OpenAI-compatible endpoint.
  Provider-managed prefix caching: no breakpoints needed; the provider
  caches a common prefix automatically.  Use ``cache_key`` to pin
  requests to the same backend cache slot.

* ``create_litellm_agent`` – LiteLLM router (``provider/model`` naming).
  LiteLLM forwards the request to the matching provider; cache semantics
  follow the underlying provider (OpenAI-managed for ``openai/…``).
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.models import AnthropicModel
from trpc_agent_sdk.models import LiteLLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .config import is_anthropic_model
from .prompts import INSTRUCTION
from .tools import get_weather_forecast
from .tools import get_weather_report


def _tools() -> list:
    return [FunctionTool(get_weather_report), FunctionTool(get_weather_forecast)]


# ---------------------------------------------------------------------------
# Provider 1 – Anthropic / Claude
# ---------------------------------------------------------------------------


def create_anthropic_agent() -> LlmAgent:
    """Anthropic / Claude: explicit ``cache_control`` breakpoints.

    How it works
    ------------
    ``PromptCacheConfig(breakpoints=["tools", "system"])`` tells the SDK to
    stamp ``cache_control: {type: ephemeral}`` on the last tool definition
    *and* on the system message before sending to the Anthropic API.
    Anthropic then caches up to that point.

    What to expect in the output
    -----------------------------
    * Turn 1  – ``cache_creation_input_tokens`` is non-zero (cache written).
    * Turn 2+ – ``cache_read_input_tokens`` is non-zero (cache hit).

    Required env vars  (uncomment Anthropic section in .env)
    -----------------------------------------------
    TRPC_AGENT_API_KEY   = sk-ant-…
    TRPC_AGENT_BASE_URL  = https://api.anthropic.com
    TRPC_AGENT_MODEL_NAME= claude-3-5-sonnet-20241022
    """
    api_key, base_url, model_name = get_model_config()
    cache_config = PromptCacheConfig(
        enabled=True,
        ttl='1h',
        breakpoints=['tools', 'system', 'messages'],
    )
    model = AnthropicModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        prompt_cache_config=cache_config,
    )
    return LlmAgent(
        name='weather_concierge',
        description='Weather concierge – Anthropic prompt cache demo.',
        model=model,
        instruction=INSTRUCTION,
        tools=_tools(),
    )


# ---------------------------------------------------------------------------
# Provider 2 – OpenAI-compatible endpoint
# ---------------------------------------------------------------------------


def create_openai_agent() -> LlmAgent:
    """OpenAI-compatible endpoint: provider-managed prefix caching.

    How it works
    ------------
    No ``breakpoints`` are needed.  OpenAI (and compatible proxies that
    support it) automatically cache a common prefix based on the start of
    the messages array.  ``cache_key`` is forwarded as ``prompt_cache_key``
    in the request body – some proxies use it to route sticky traffic to
    the same cached backend.

    What to expect in the output
    -----------------------------
    * ``cache_read_input_tokens`` becomes non-zero after the prefix is warm
      (typically from the 2nd–4th request; proxy-dependent).
    * ``cache_creation_input_tokens`` is only reported by Anthropic; it will
      be ``None`` here.

    Required env vars  (uncomment OpenAI section in .env)
    -------------------------------------------
    TRPC_AGENT_API_KEY   = <your key>
    TRPC_AGENT_BASE_URL  = <openai-compatible base url>
    TRPC_AGENT_MODEL_NAME= <model name, e.g. gpt-4o or glm-5>
    """
    api_key, base_url, model_name = get_model_config()
    cache_config = PromptCacheConfig(
        enabled=True,
        ttl='24h',
        prompt_cache_key='weather-concierge-v1',
    )
    model = OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        prompt_cache_config=cache_config,
    )
    return LlmAgent(
        name='weather_concierge',
        description='Weather concierge – OpenAI-compatible prompt cache demo.',
        model=model,
        instruction=INSTRUCTION,
        tools=_tools(),
    )


# ---------------------------------------------------------------------------
# Provider 3 – LiteLLM router
# ---------------------------------------------------------------------------


def create_litellm_agent() -> LlmAgent:
    """LiteLLM router: ``provider/model`` naming, cache via underlying provider.

    How it works
    ------------
    LiteLLM inspects the ``provider/`` prefix to decide which backend SDK to
    use.  For ``openai/…`` the SDK sends an OpenAI-compatible request and
    ``PromptCacheConfig`` flows through as ``extra_body`` (``prompt_cache_key``
    / ``prompt_cache_retention``).

    Note: if the model name starts with ``anthropic/`` the cache family
    switches automatically to ``cache_control`` breakpoints.

    What to expect in the output
    -----------------------------
    Same as the OpenAI-compatible path for ``openai/…`` model names.

    Required env vars  (uncomment LiteLLM section in .env)
    --------------------------------------------
    TRPC_AGENT_API_KEY   = <your key>
    TRPC_AGENT_BASE_URL  = <openai-compatible base url, e.g. .../llmproxy/v1>
    TRPC_AGENT_MODEL_NAME= openai/<model>   ← provider prefix required
    """
    api_key, base_url, model_name = get_model_config()
    if '/' not in model_name:
        raise ValueError(f"LiteLLM model_name must include a provider prefix, e.g. "
                         f"'openai/{model_name}'.  Got: '{model_name}'")
    if is_anthropic_model(model_name):
        cache_config = PromptCacheConfig(
            enabled=True,
            ttl='1h',
            breakpoints=['tools', 'system'],
        )
    else:
        cache_config = PromptCacheConfig(
            enabled=True,
            ttl='24h',
            prompt_cache_key='weather-concierge-v1',
        )
    model = LiteLLMModel(
        model_name=model_name,
        api_key=api_key,
        api_base=base_url,
        prompt_cache_config=cache_config,
    )
    return LlmAgent(
        name='weather_concierge',
        description='Weather concierge – LiteLLM prompt cache demo.',
        model=model,
        instruction=INSTRUCTION,
        tools=_tools(),
    )


# ---------------------------------------------------------------------------
# Auto-detect factory (used by the legacy run_agent.py)
# ---------------------------------------------------------------------------


def create_agent() -> LlmAgent:
    """Auto-detect provider from model name and delegate to the right factory.

    Selection order
    ---------------
    1. model_name contains ``/`` (``provider/model`` format) → :func:`create_litellm_agent`
    2. model_name starts with ``claude`` → :func:`create_anthropic_agent`
    3. Anything else → :func:`create_openai_agent`
    """
    _, _, model_name = get_model_config()
    if '/' in model_name:
        return create_litellm_agent()
    if is_anthropic_model(model_name):
        return create_anthropic_agent()
    return create_openai_agent()


root_agent = create_agent()
