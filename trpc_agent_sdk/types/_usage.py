# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Usage metadata types for TRPC Agent framework.

This module extends Google GenAI's ``GenerateContentResponseUsageMetadata`` with
prompt-cache token counters that are not present upstream. It is re-exported from
``trpc_agent_sdk.types`` so it shadows the upstream type for all SDK callers.
"""

from __future__ import annotations

from typing import Optional

from google.genai.types import GenerateContentResponseUsageMetadata as _BaseUsageMetadata


class GenerateContentResponseUsageMetadata(_BaseUsageMetadata):
    """Usage metadata extended with prompt-cache token counters.

    Adds two provider-normalized fields on top of the upstream type:

    - ``cache_read_input_tokens``: input tokens served from cache (Anthropic
      ``cache_read_input_tokens`` / OpenAI ``prompt_tokens_details.cached_tokens``).
    - ``cache_creation_input_tokens``: input tokens written to cache (Anthropic
      ``cache_creation_input_tokens``; always ``None``/0 for OpenAI which has no
      separate cache-write step).
    """

    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
