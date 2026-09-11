# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import Any
from typing_extensions import override

from trpc_agent_sdk.abc import CompactTrigger
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import FilterType

from ._manager import AdvancedAutoCompactSummarizerManager
from ._utils import INTERNAL_COMPACTION_METADATA_KEY


class AdvancedAutoCompactSummarizerFilter(BaseFilter):
    """Advanced auto compact summarizer filter."""

    def __init__(self) -> None:
        """Initialize the advanced auto compact summarizer filter."""
        super().__init__()
        self.name = "advanced_auto_compact_summarizer_filter"
        self.type = FilterType.MODEL

    def get_summarizer_manager(self, ctx: InvocationContext) -> AdvancedAutoCompactSummarizerManager:
        """Get the summarizer."""
        session_service = ctx.session_service
        if session_service is None:
            raise ValueError("Session service is not set")
        summarizer_manager = getattr(session_service, "summarizer_manager", None)
        if summarizer_manager is None or not isinstance(summarizer_manager, AdvancedAutoCompactSummarizerManager):
            raise ValueError("Summarizer manager is not an AdvancedAutoCompactSummarizerManager")
        return summarizer_manager

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Run the advanced auto compact summarizer filter."""
        if ctx.get_metadata(INTERNAL_COMPACTION_METADATA_KEY, False):
            return None
        invocation_ctx: InvocationContext = get_invocation_ctx()
        summarizer_manager = self.get_summarizer_manager(invocation_ctx)
        result = await summarizer_manager.create_session_summary_before_model(
            req,
            invocation_ctx,
        )
        if not result:
            return None
        invocation_ctx.end_invocation = True
        rsp.rsp = result
        rsp.is_continue = False
        rsp.error = None
        session_memory_extractor = summarizer_manager.get_session_memory_extractor()
        if session_memory_extractor is not None:
            await session_memory_extractor.extract_if_needed(
                invocation_ctx,
                force=False,
            )
        return

    @override
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Run the advanced auto compact summarizer filter."""
        if ctx.get_metadata(INTERNAL_COMPACTION_METADATA_KEY, False):
            return None
        invocation_ctx: InvocationContext = get_invocation_ctx()
        summarizer_manager = self.get_summarizer_manager(invocation_ctx)
        if summarizer_manager.compact_trigger != CompactTrigger.BEFORE_MODEL:
            return None
        session_memory_extractor = summarizer_manager.get_session_memory_extractor()
        if session_memory_extractor is not None:
            await session_memory_extractor.extract_if_needed(
                invocation_ctx,
                force=False,
            )
