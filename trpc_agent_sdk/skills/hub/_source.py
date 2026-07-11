# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""The `SkillSource` contract shared by all skill registry adapters."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from ._types import SkillBundle
from ._types import SkillMeta


class SkillSource(ABC):
    """Contract every skill registry adapter implements.

    Concrete adapters subclass this directly, e.g.
    `class GitHubSource(SkillSource)`. A custom source does the same.
    """

    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier for this source, e.g. ``"clawhub"`` / ``"github"``."""
        ...

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
        """Search for skills matching a free-text query string."""
        ...

    @abstractmethod
    def inspect(self, identifier: str) -> SkillMeta | None:
        """Fetch metadata for a skill without downloading its files."""
        ...

    @abstractmethod
    def fetch(self, identifier: str) -> SkillBundle | None:
        """Download a skill bundle by identifier."""
        ...
