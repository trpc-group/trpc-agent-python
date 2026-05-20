# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM options for the optimizer's prompt rewrite step."""

from __future__ import annotations

from typing import Any
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel


class OptimizeModelOptions(EvalBaseModel):
    """LLM configuration for proposing new prompt candidates."""

    provider_name: str = Field(default="", description="LLM provider name.")
    model_name: str = Field(default="", description="Model name.")
    variant: str = Field(default="", description="OpenAI-compatible variant when provider is openai.")
    base_url: Optional[str] = Field(default=None, description="Custom endpoint URL.")
    api_key: str = Field(default="", description="API key.")
    extra_fields: Optional[dict[str, Any]] = Field(
        default=None,
        description="Extra provider-specific fields.",
    )
    num_samples: Optional[int] = Field(
        default=None,
        description="Number of samples per call.",
    )
    generation_config: Optional[dict[str, Any]] = Field(
        default=None,
        description="Generation params: max_tokens, temperature, stream, etc.",
    )
    weight: float = Field(
        default=1.0,
        description="Weight for aggregation across samples.",
    )
    think: Optional[bool] = Field(
        default=None,
        description="Thinking mode toggle. None: no change; False: disable; True: enable.",
    )
