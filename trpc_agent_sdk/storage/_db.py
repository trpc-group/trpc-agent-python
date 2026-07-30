# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Database interface for TRPC Agent framework."""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Hashable
from typing import List
from typing import Optional


class BaseStorage(ABC):
    """Store some data"""

    @abstractmethod
    async def add(self, db: Any, data: Any):
        """Add data"""

    @abstractmethod
    async def delete(self, db: Any, key: Hashable) -> None:
        """Delete data"""

    @abstractmethod
    async def query(self, db: Any, key: Hashable, filters: List, limit: Optional[int] = None) -> Any:
        """Query data"""

    @abstractmethod
    async def get(self, db: Any, key: Hashable) -> Any:
        """Get data"""

    @abstractmethod
    async def commit(self, db: Any, data: Any) -> None:
        """Commit data"""

    @abstractmethod
    async def refresh(self, db: Any, data: Any) -> None:
        """Refresh data"""

    @abstractmethod
    async def close(self) -> None:
        """Close db"""
