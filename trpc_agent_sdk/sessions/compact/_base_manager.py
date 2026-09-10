# Tencent is pleased to support the open source community by making
# contributions to the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Define the Session Compact manager lifecycle contract."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trpc_agent_sdk.abc import SessionServiceABC
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.sessions import Session


class BaseSessionCompactManager(ABC):
    """Coordinate one Session Compact implementation with a SessionService."""

    @abstractmethod
    def setup(self, agent: Any) -> None:
        """Initialize this manager and install its Agent callbacks."""

    @abstractmethod
    def set_session_service(
        self,
        session_service: "SessionServiceABC",
        force: bool = False,
    ) -> None:
        """Bind this manager to the SessionService that owns its sessions."""

    @abstractmethod
    async def create_session_summary(
        self,
        session: "Session",
        force: bool = False,
        ctx: "InvocationContext | None" = None,
    ) -> None:
        """Update compact state through the SessionService post-turn hook."""

    @abstractmethod
    async def get_session_summary(self, session: "Session") -> str | None:
        """Return the compact representation exposed as a session summary."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources owned by this manager."""
