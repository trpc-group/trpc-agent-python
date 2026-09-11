# Tencent is pleased to support the open source community by making
# contributions to the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Integrate Session Compact with the native SessionService lifecycle."""

from __future__ import annotations

from typing_extensions import override

from trpc_agent_sdk.abc import CompactSummarizerManagerABC
from trpc_agent_sdk.abc import CompactTrigger
from trpc_agent_sdk.abc import RequestABC
from trpc_agent_sdk.abc import ResponseABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest

from ..._session import Session
from ._auto_compact import AdvancedAutoCompactSummarizer
from ._formats import parse_session_memory_state
from ._formats import SESSION_MEMORY_STATE_KEY
from ._compaction_memory_extractor import SessionMemoryExtractor
from ._history_snip import HistorySnipHandler
from ._micro_compact import MicroCompactHandler
from ._tool_result_budget import ToolResultBudgetHandler
from ._auto_compact import AdvancedAutoCompactSummarizerHandler


class AdvancedAutoCompactSummarizerManager(CompactSummarizerManagerABC):
    """Coordinate Advanced Compact state without wrapping a SessionService."""

    def __init__(
        self,
        summarizer: AdvancedAutoCompactSummarizer,
        compact_trigger: CompactTrigger = CompactTrigger.BEFORE_MODEL,
    ) -> None:
        """Store configuration until Runner supplies the Agent."""
        super().__init__(summarizer, compact_trigger=compact_trigger)
        self._tool_result_budget_handler = ToolResultBudgetHandler()
        self._history_snip_handler = HistorySnipHandler()
        self._micro_compact_handler = MicroCompactHandler()
        self._advanced_auto_compact_summarizer_handler = AdvancedAutoCompactSummarizerHandler()

    def get_session_memory_extractor(self) -> SessionMemoryExtractor:
        """Get the session memory extractor."""
        return self.summarizer.session_memory_extractor

    @override
    async def create_session_summary(
        self,
        session: Session,
        force: bool = False,
        ctx: InvocationContext | None = None,
    ) -> None:
        """Compact persisted Events when configured for end-of-turn execution."""
        if self.compact_trigger != CompactTrigger.AFTER_TURN:
            return
        if ctx is None:
            raise ValueError("Invocation context is required for advanced compaction")
        session_memory_extractor = self.get_session_memory_extractor()
        if session_memory_extractor is not None:
            await session_memory_extractor.extract_if_needed(
                ctx,
                force=False,
            )
        if force or await self.summarizer.should_summarize(session):
            await self.summarizer.create_session_summary(
                session,
                ctx=ctx,
                store_historical_events=True,
            )

    @override
    async def create_session_summary_before_model(
        self,
        request: RequestABC,
        ctx: InvocationContext,
        force: bool = False,
    ) -> ResponseABC | None:
        """Run the advanced request pipeline immediately before model generation."""
        if self.compact_trigger != CompactTrigger.BEFORE_MODEL:
            return None
        if not isinstance(request, LlmRequest):
            raise TypeError("Advanced compaction requires an LlmRequest")
        await self._tool_result_budget_handler.handle(ctx, request)
        await self._history_snip_handler.handle(ctx, request)
        await self._micro_compact_handler.handle(ctx, request)
        return await self._advanced_auto_compact_summarizer_handler.handle(
            ctx,
            request,
            force=force,
        )

    async def get_session_summary(self, session: Session) -> str | None:
        """Read compact Session Memory through the existing summary API."""
        parsed = parse_session_memory_state(session.state.get(SESSION_MEMORY_STATE_KEY))
        if parsed is not None:
            return parsed[0].to_markdown()
        return None
