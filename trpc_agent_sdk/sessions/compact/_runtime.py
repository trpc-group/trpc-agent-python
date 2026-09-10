# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Runtime coordination for Session Compact."""

from __future__ import annotations

from dataclasses import dataclass

from ._config import AdvancedCompactConfig
from ._coordination import SessionOperationCoordinator


@dataclass
class SessionCompactRuntime:
    """Hold compression configuration and per-session coordination only."""

    config: AdvancedCompactConfig
    coordination: SessionOperationCoordinator

    @classmethod
    def create(cls, config: AdvancedCompactConfig | None = None) -> "SessionCompactRuntime":
        return cls(config or AdvancedCompactConfig(), SessionOperationCoordinator())

    def for_session(self, session: object) -> "ScopedSessionCompactRuntime":
        app_name = getattr(session, "app_name", None)
        user_id = getattr(session, "user_id", None)
        if not isinstance(app_name, str) or not isinstance(user_id, str):
            raise ValueError("Session Compact requires session app_name and user_id")
        return ScopedSessionCompactRuntime(self, f"{app_name}\0{user_id}")


@dataclass
class ScopedSessionCompactRuntime:
    """Session-scoped view used by compression callbacks."""

    root: SessionCompactRuntime
    scope: str

    @property
    def config(self) -> AdvancedCompactConfig:
        return self.root.config

    @property
    def coordination(self) -> SessionOperationCoordinator:
        return self.root.coordination

    def session_key(self, session_id: str) -> str:
        return f"{self.scope}\0{session_id}"

    def for_session(self, session: object) -> "ScopedSessionCompactRuntime":
        return self.root.for_session(session)
