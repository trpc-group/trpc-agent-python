# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import CompactSummarizerABC
from trpc_agent_sdk.abc import CompactSummarizerManagerABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest


class BaseCompactSummarizerHandler(ABC):
    """Base compact summarizer handler."""

    def get_summarizer(self, ctx: InvocationContext) -> CompactSummarizerABC:
        """Get the summarizer."""
        session_service = ctx.session_service
        if session_service is None:
            raise ValueError("Session service is not set")
        summarizer_manager = getattr(session_service, "summarizer_manager", None)
        if summarizer_manager is None or not isinstance(summarizer_manager, CompactSummarizerManagerABC):
            raise ValueError("Summarizer manager is not an CompactSummarizerManagerABC")
        return summarizer_manager.summarizer

    @abstractmethod
    async def handle(self, ctx: InvocationContext, req: LlmRequest):
        """Handle the compact summarizer."""
        pass


class BaseTokenEstimator(ABC):
    """Define the replaceable token estimator interface."""

    @abstractmethod
    def estimate_payload_tokens(self, payload: Any) -> int:
        """Estimate tokens for any JSON-compatible payload."""


class BaseModelContextWindowResolver(ABC):
    """Define the model-identifier context-window resolver interface."""

    @abstractmethod
    def resolve_context_window_tokens(self, model: Any) -> Optional[int]:
        """Return the model context window, or None when unknown."""
