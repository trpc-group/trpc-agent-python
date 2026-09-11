# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Runtime coordination for Session Compact."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..._session import Session
from ._config import AdvancedAutoCompactSummarizerConfig
from ._coordination import SessionOperationCoordinator


@dataclass
class AdvancedAutoCompactSummarizerRuntime:
    """Hold advanced auto compact summarizer configuration and per-session coordination only."""

    config: AdvancedAutoCompactSummarizerConfig = field(default_factory=AdvancedAutoCompactSummarizerConfig)
    coordination: SessionOperationCoordinator = field(default_factory=SessionOperationCoordinator)
    scope: str = field(default="")

    def for_session(self, session: Session) -> AdvancedAutoCompactSummarizerRuntime:
        return AdvancedAutoCompactSummarizerRuntime(config=self.config,
                                                    coordination=self.coordination,
                                                    scope=f"{session.app_name}\0{session.user_id}")

    def session_key(self, session_id: str) -> str:
        return f"{self.scope}\0{session_id}"
