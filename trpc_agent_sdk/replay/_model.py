# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic LLM stand-ins used by the replay harness."""
from __future__ import annotations

from collections import deque
from typing import AsyncGenerator
from typing import Optional

from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class ReplaySummaryModel:
    """Deterministic model used to exercise the real summarizer pipeline.

    Implements the protocol expected by :class:`SessionSummarizer` — i.e.
    ``name`` attribute and ``generate_async(request, stream, ctx)`` returning
    an async iterator of :class:`LlmResponse`.
    """

    name = "replay-summary-model"

    def __init__(self) -> None:
        self._responses: deque[str] = deque()

    def enqueue(self, text: str) -> None:
        """Queue one deterministic summary response."""
        self._responses.append(text)

    def queue_size(self) -> int:
        """Return the number of queued responses."""
        return len(self._responses)

    async def generate_async(
        self,
        _request: LlmRequest,
        stream: bool = False,
        ctx: Optional[object] = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Yield the queued response through the model interface."""
        del stream, ctx
        if not self._responses:
            raise RuntimeError("No replay summary response was queued")
        text = self._responses.popleft()
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=text)]))