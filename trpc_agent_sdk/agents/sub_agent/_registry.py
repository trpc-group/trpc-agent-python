# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Insertion-ordered registry of sub-agent archetypes."""

from __future__ import annotations

from typing import Iterator

from ._archetype import SubAgentArchetype


class SubAgentRegistry:
    """A catalog of archetypes the SpawnSubAgentTool may instantiate.

    The registry preserves insertion order so the rendered tool description
    is deterministic. Duplicate names are rejected; lookups for unknown
    names raise ``KeyError``.
    """

    def __init__(self) -> None:
        self._items: dict[str, SubAgentArchetype] = {}

    def register(self, archetype: SubAgentArchetype) -> None:
        """Register a new archetype. Raises ``ValueError`` on name collision."""
        if archetype.name in self._items:
            raise ValueError(f"archetype name {archetype.name!r} already registered")
        self._items[archetype.name] = archetype

    def get(self, name: str) -> SubAgentArchetype:
        """Return the archetype with the given name. Raises ``KeyError`` if absent."""
        if name not in self._items:
            raise KeyError(f"archetype {name!r} not found in registry")
        return self._items[name]

    def names(self) -> list[str]:
        return list(self._items.keys())

    def archetypes(self) -> list[SubAgentArchetype]:
        return list(self._items.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items

    def __iter__(self) -> Iterator[SubAgentArchetype]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


__all__ = ["SubAgentRegistry"]
