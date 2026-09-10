# Tencent is pleased to support the open source community by making
# contributions to the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Integrate Session Compact with the native SessionService lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._base_manager import BaseSessionCompactManager
from ._formats import parse_session_memory_state
from ._formats import SESSION_MEMORY_STATE_KEY

if TYPE_CHECKING:
    from trpc_agent_sdk.abc import SessionServiceABC
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.sessions import Session

    from ._runtime import AdvancedMemoryRuntime
    from ._session_memory import SessionMemoryExtractor


class AdvancedSessionCompactManager(BaseSessionCompactManager):
    """Coordinate Advanced Compact state without wrapping a SessionService."""

    def __init__(
        self,
        runtime: "AdvancedMemoryRuntime",
        session_memory_extractor: "SessionMemoryExtractor",
    ) -> None:
        """Store the compact runtime and post-turn memory extractor."""
        self._runtime = runtime
        self._session_memory_extractor = session_memory_extractor
        self._session_service: SessionServiceABC | None = None

    @property
    def runtime(self) -> "AdvancedMemoryRuntime":
        """Return the runtime shared by all compact stages."""
        return self._runtime

    @property
    def session_memory_extractor(self) -> "SessionMemoryExtractor":
        """Return the post-turn Session Memory extractor."""
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
            raise ValueError(
                "Advanced Session Compact requires "
                "SessionServiceConfig(store_historical_events=True)"
            )
        self._session_service = session_service
        self._session_memory_extractor.attach_session_service(session_service)

    async def create_session_summary(
        self,
        session: "Session",
        force: bool = False,
        ctx: "InvocationContext | None" = None,
    ) -> None:
        """Use the native post-turn hook to update persistent Session Memory."""
        if ctx is not None:
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
        runtime = self._runtime.for_session(session)
        if runtime.session_memory is None:
            return None
        return await runtime.session_memory.read(session.id)

    async def delete_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """Delete compact side data after the framework Session is deleted."""
        await self._runtime.for_scope(app_name, user_id).delete_session(session_id)

    async def close(self) -> None:
        """Release Compact backend resources owned by this manager."""
        await self._runtime.close()
