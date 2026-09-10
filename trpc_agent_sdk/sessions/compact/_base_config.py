# Tencent is pleased to support the open source community by making
# contributions to the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Define the configuration contract for Session Compact strategies."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base_manager import BaseSessionCompactManager


class BaseSessionCompactConfig(ABC):
    """Create and attach one concrete Session Compact strategy."""

    @abstractmethod
    def setup(
        self,
        agent: Any,
        session_service: Any,
    ) -> "BaseSessionCompactManager":
        """Create the strategy manager and attach it to the SessionService."""
