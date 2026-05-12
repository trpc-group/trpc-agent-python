# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Adapters for OpenAI-compatible model providers."""

from __future__ import annotations

from typing import Optional

from ._base import DefaultOpenAIAdapter
from ._base import OpenAIAdapter
from ._base import ToolPromptTextFilterMixin
from ._deepseek import DeepSeekAdapter
from ._hunyuan import HunyuanHy3PreviewAdapter


def get_openai_adapter(model_name: str, base_url: Optional[str] = None) -> OpenAIAdapter:
    """Return the provider adapter for an OpenAI-compatible model."""
    model_name_lower = model_name.lower()
    if model_name_lower == "hy3-preview":
        return HunyuanHy3PreviewAdapter(model_name=model_name, base_url=base_url)
    if model_name_lower.startswith("deepseek-"):
        return DeepSeekAdapter(model_name=model_name, base_url=base_url)
    return DefaultOpenAIAdapter(model_name=model_name, base_url=base_url)


__all__ = [
    "DefaultOpenAIAdapter",
    "DeepSeekAdapter",
    "HunyuanHy3PreviewAdapter",
    "OpenAIAdapter",
    "ToolPromptTextFilterMixin",
    "get_openai_adapter",
]
