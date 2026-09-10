# Tencent is pleased to support the open source community by making
# contributions to the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Integrate Session Compact with the native SessionService lifecycle."""

from __future__ import annotations

from typing import Any
from typing import TYPE_CHECKING

from ._base_manager import BaseSessionCompactManager
from ._formats import parse_session_memory_state
from ._formats import SESSION_MEMORY_STATE_KEY

if TYPE_CHECKING:
    from trpc_agent_sdk.abc import SessionServiceABC
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.sessions import Session

from ._autocompact import LegacySummaryGenerator
from ._config import AdvancedCompactConfig
from ._runtime import SessionCompactRuntime
from ._session_memory import SessionMemoryExtractor
from ._session_memory import SessionMemoryGenerator


class AdvancedSessionCompactManager(BaseSessionCompactManager):
    """Coordinate Advanced Compact state without wrapping a SessionService."""

    def __init__(
        self,
        config: AdvancedCompactConfig,
        *,
        summary_generator: "LegacySummaryGenerator | None" = None,
        compact_model: Any | None = None,
        session_memory_generator: "SessionMemoryGenerator | None" = None,
        session_memory_model: Any | None = None,
    ) -> None:
        """Store configuration until Runner supplies the Agent."""
        self._config = config
        self._summary_generator = summary_generator
        self._compact_model = compact_model
        self._session_memory_generator = session_memory_generator
        self._session_memory_model = session_memory_model
        self._runtime: SessionCompactRuntime | None = None
        self._session_memory_extractor: SessionMemoryExtractor | None = None
        self._session_service: SessionServiceABC | None = None

    def setup(self, agent: Any) -> None:
        """Initialize the runtime and install all compression callbacks."""
        if self._session_service is None:
            raise RuntimeError("Session Compact manager must be bound to a SessionService first")
        if self._runtime is not None:
            return
        from ._autocompact import setup_autocompact
        from ._history_snip import setup_history_snip
        from ._microcompact import setup_microcompact
        from ._tool_result_budget import setup_tool_result_budget

        runtime = SessionCompactRuntime.create(self._config)
        extractor = SessionMemoryExtractor(
            runtime,
            self._session_memory_generator,
            model=self._session_memory_model,
        )
        setup_tool_result_budget(agent, runtime)
        setup_history_snip(agent, runtime)
        setup_microcompact(agent, runtime)
        autocompact = setup_autocompact(
            agent,
            runtime,
            self._summary_generator,
            model=self._compact_model,
        )
        autocompact.attach_session_memory_extractor(extractor)
        extractor.attach_session_service(self._session_service)
        self._runtime = runtime
        self._session_memory_extractor = extractor

    @property
    def runtime(self) -> "SessionCompactRuntime":
        """Return the runtime shared by all compact stages."""
        if self._runtime is None:
            raise RuntimeError("Session Compact manager has not been initialized by Runner")
        return self._runtime

    @property
    def session_memory_extractor(self) -> "SessionMemoryExtractor":
        """Return the post-turn Session Memory extractor."""
        if self._session_memory_extractor is None:
            raise RuntimeError("Session Compact manager has not been initialized by Runner")
        return self._session_memory_extractor

    def set_session_service(
        self,
        session_service: "SessionServiceABC",
        force: bool = False,
    ) -> None:
        """Bind the manager to the original persistence service."""
        if self._session_service is not None and self._session_service is not session_service and not force:
            raise ValueError("AdvancedSessionCompactManager is already bound to another SessionService")
        session_config = getattr(session_service, "session_config", None)
        if session_config is None or not getattr(session_config, "store_historical_events", False):
            raise ValueError("Advanced Session Compact requires "
                             "SessionServiceConfig(store_historical_events=True)")
        self._session_service = session_service
        if self._session_memory_extractor is not None:
            self._session_memory_extractor.attach_session_service(session_service)

    async def create_session_summary(
        self,
        session: "Session",
        force: bool = False,
        ctx: "InvocationContext | None" = None,
    ) -> None:
        """Use the native post-turn hook to update persistent Session Memory."""
        if ctx is not None and self._session_memory_extractor is not None:
            await self._session_memory_extractor.extract_if_needed(
                session,
                ctx,
                force=force,
            )

    async def get_session_summary(self, session: "Session") -> str | None:
        """Read compact Session Memory through the existing summary API."""
        parsed = parse_session_memory_state(session.state.get(SESSION_MEMORY_STATE_KEY))
        if parsed is not None:
            return parsed[0].to_markdown()
        return None

    async def close(self) -> None:
        """Release Compact resources owned by the manager."""
