# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""The base class for compact summarizers."""

from abc import ABC
from abc import abstractmethod
from enum import Enum
from typing import List
from typing import Optional
from typing import Dict
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext

from ._session import SessionABC
from ._response import ResponseABC
from ._request import RequestABC
from ._session_service import SessionServiceABC


class CompactTrigger(str, Enum):
    """Select when session compaction is evaluated."""

    AFTER_TURN = "after_turn"
    BEFORE_MODEL = "before_model"


class CompactSummarizerABC(ABC):
    """The base class for compact summarizers."""

    @abstractmethod
    async def should_summarize(self, session: SessionABC) -> bool:
        """Check if the session should be summarized.

        Args:
            session: The session to check.

        Returns:
            True if the session should be summarized, False otherwise.
        """

    @abstractmethod
    async def create_session_summary_by_events(
            self,
            events: List[ResponseABC],
            session_id: str,
            keep_recent_count: int = 10,
            ctx: Optional["InvocationContext"] = None,
            historical_events: Optional[List[ResponseABC]] = None,
            store_historical_events: bool = False) -> tuple[Optional[str], List[ResponseABC]]:
        """Create a session summary by events.

        Args:
            events: The events to summarize.
            session_id: The session ID.
            keep_recent_count: The number of recent events to keep.
            ctx: The invocation context.
            historical_events: The historical events.
            store_historical_events: Whether to store the historical events.

        Returns:
            A tuple containing the session summary and the historical events.
        """

    @abstractmethod
    async def create_session_summary(self,
                                     session: SessionABC,
                                     ctx: Optional["InvocationContext"] = None,
                                     store_historical_events: bool = False) -> Optional[str]:
        """Create a session summary.

        Args:
            session: The session to summarize.
            ctx: The invocation context.
            store_historical_events: Whether to store the historical events.

        Returns:
            The session summary.
        """

    def get_summary_metadata(self) -> Dict[str, Any]:
        """Get the summary metadata.

        Returns:
            The summary metadata.
        """
        return {}

    async def create_session_summary_by_request(
        self,
        request: RequestABC,
        ctx: Optional["InvocationContext"] = None,
        force: bool = False,
    ) -> Optional[ResponseABC]:
        """Compact one model request before generation.

        The default implementation is intentionally a no-op so existing
        summarizers only implementing end-of-turn compaction remain
        compatible.
        """
        del request, ctx, force
        return None


class CompactSummarizerManagerABC(ABC):
    """Coordinate one CompactSummarizer implementation with a SessionService."""

    def __init__(
        self,
        summarizer: CompactSummarizerABC,
        compact_trigger: CompactTrigger = CompactTrigger.AFTER_TURN,
    ):
        self._summarizer = summarizer
        self._base_service = None
        self._compact_trigger = compact_trigger

    @property
    def summarizer(self) -> CompactSummarizerABC:
        """Get the CompactSummarizer implementation."""
        return self._summarizer

    @property
    def session_service(self) -> SessionServiceABC:
        """Get the base session service."""
        return self._base_service

    @property
    def compact_trigger(self) -> CompactTrigger:
        """Return when this manager evaluates compaction."""
        return self._compact_trigger

    def set_session_service(self, session_service: SessionServiceABC, force: bool = False) -> None:
        """Set the session service to use.

        Args:
            session_service: The session service to use.
            force: Whether to force update even if already set.
        """
        if not self._base_service or force:
            self._base_service = session_service

    def set_summarizer(self, summarizer: CompactSummarizerABC, force: bool = False) -> None:
        """Set the summarizer to use.

        Args:
            summarizer: The summarizer to use
            force: Whether to force update even if already set
        """
        if not self._summarizer or force:
            self._summarizer = summarizer

    @abstractmethod
    async def create_session_summary(
        self,
        session: SessionABC,
        force: bool = False,
        ctx: Optional["InvocationContext"] = None,
    ) -> None:
        """Update compact state through the SessionService post-turn hook."""

    @abstractmethod
    async def get_session_summary(self, session: SessionABC) -> Optional[str]:
        """Return the compact representation exposed as a session summary."""

    async def create_session_summary_before_model(
        self,
        request: RequestABC,
        ctx: "InvocationContext",
        force: bool = False,
    ) -> Optional[ResponseABC]:
        """Run request compaction when configured for the before-model phase."""
        if self._compact_trigger != CompactTrigger.BEFORE_MODEL:
            return None
        return await self._summarizer.create_session_summary_by_request(
            request,
            ctx=ctx,
            force=force,
        )

    async def close(self) -> None:
        """Release resources owned by this manager."""
        return None
