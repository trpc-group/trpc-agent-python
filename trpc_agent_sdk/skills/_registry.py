# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill registry for managing loaded skills.

This module provides the SkillRegistry class which maintains a registry of
all loaded skills and provides lookup functionality.
"""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import TypeAlias

from trpc_agent_sdk.utils import SingletonBase

SkillToolFunction: TypeAlias = Callable[..., Any]


class SkillRegistry(SingletonBase):
    """Registry for managing skills.

    This singleton class provides:
    - Skill registration and lookup
    - Name-based skill access
    - Tag-based skill filtering

    Example:
        >>> registry = SkillRegistry()
        >>> registry.register(skill)
        >>> skill = registry.get("my-skill")
        >>> skills = registry.get_by_tag("data-analysis")
    """

    def __init__(self) -> None:
        """Initialize the skill registry."""
        super().__init__()
        self._skills: Dict[str, SkillToolFunction] = {}

    def register(self, name: str, skill_function: SkillToolFunction) -> None:
        """Register a skill.

        Args:
            skill_function: Skill function to register

        Raises:
            ValueError: If a skill with the same name is already registered
        """
        if name in self._skills:
            raise ValueError(f"Skill '{name}' is already registered")
        self._skills[name] = skill_function

    def unregister(self, name: str) -> None:
        """Unregister a skill.

        Args:
            name: Name of the skill to unregister
        """
        self._skills.pop(name, None)

    def get(self, name: str) -> SkillToolFunction | None:
        """Get a skill by name.

        Args:
            name: Name of the skill

        Returns:
            Skill instance or None if not found
        """
        return self._skills.get(name)

    def get_all(self) -> List[SkillToolFunction]:
        """Get all registered skills.

        Returns:
            List of all registered skills
        """
        return list(self._skills.values())

    def search(self, query: str) -> List[SkillToolFunction]:
        """Search skills by name or description.

        Args:
            query: Search query string

        Returns:
            List of matching skills
        """
        query_lower = query.lower()
        skill_functions = []
        for name, skill_function in self._skills.items():
            if query_lower in name.lower():
                skill_functions.append(skill_function)
        return skill_functions

    def clear(self) -> None:
        """Clear all registered skills and clean them up."""
        self._skills.clear()


SKILL_REGISTRY: SkillRegistry = SkillRegistry()  # pylint: disable=invalid-name
