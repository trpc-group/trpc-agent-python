# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool prompt factory for TRPC Agent framework."""

from typing import Dict
from typing import Type

from ._base import ToolPrompt


class ToolPromptFactory:
    """Factory for creating tool prompt implementations."""

    def __init__(self):
        """Initialize the factory."""
        self._registry: Dict[str, Type[ToolPrompt]] = {}

    def register(self, name: str, tool_prompt_class: Type[ToolPrompt]) -> None:
        """Register a tool prompt class with a name.

        Args:
            name: Name to register the tool prompt class with
            tool_prompt_class: ToolPrompt class to register

        Note:
            If the name is duplicated, keeps the latest registration.
        """
        self._registry[name] = tool_prompt_class

    def create(self, name: str) -> ToolPrompt:
        """Create a tool prompt instance by name.

        Args:
            name: Name of the tool prompt to create

        Returns:
            ToolPrompt instance

        Raises:
            ValueError: If name is not registered
        """
        if name not in self._registry:
            raise ValueError(f"Tool prompt '{name}' is not registered. Available: {list(self._registry.keys())}")

        tool_prompt_class = self._registry[name]
        return tool_prompt_class()


# Global factory instance
_factory: ToolPromptFactory = None


def initialize() -> None:
    """Initialize the factory and register built-in tool prompts.

    This function will be called when the OpenAIModel is imported.
    It registers JsonToolPrompt and XmlToolPrompt.
    """
    global _factory  # pylint: disable=invalid-name
    if _factory is None:
        _factory = ToolPromptFactory()

        # Import and register built-in tool prompts
        from ._json import JsonToolPrompt
        from ._xml import XmlToolPrompt

        _factory.register("json", JsonToolPrompt)
        _factory.register("xml", XmlToolPrompt)


def get_factory() -> ToolPromptFactory:
    """Get the initialized factory.

    Returns:
        ToolPromptFactory instance

    Raises:
        RuntimeError: If factory is not initialized
    """
    global _factory  # pylint: disable=invalid-name
    if _factory is None:
        raise RuntimeError("Factory is not initialized. Call initialize() first.")
    return _factory
