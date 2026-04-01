# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Centralized tool registration system for TRPC Agent framework.

This module implements the core registry system that manages:
- Tool registration and discovery
- Toolset registration and management
- Decorator-based registration patterns

Key Features:
- Singleton registry instances
- Thread-safe operations
- Support for both class and function tools
- Name conflict resolution
"""
from typing import Callable
from typing import Optional
from typing import TypeAlias
from typing import Union

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.utils import BaseRegistryFactory
from trpc_agent_sdk.utils import SingletonBase

from ._base_tool import BaseTool
from ._function_tool import FunctionTool

ToolType: TypeAlias = Union[BaseTool, BaseToolSet, str]


class _ToolManager(BaseRegistryFactory[BaseTool]):
    """Singleton registry for managing BaseTool instances.

    Provides centralized storage and retrieval of tool instances with:
    - Thread-safe access
    - Name-based lookup
    - Decorator support

    Attributes:
        __tools_cache: Dictionary mapping tool names to instances
    """

    def add(self, tool: BaseTool) -> None:
        """Add a tool to the registry."""
        if tool.name in self._instance_map:
            raise TypeError(f"Tool '{tool.name}' already exists.")
        self._instance_map[tool.name] = tool

    def register(self,
                 name: str = '',
                 description: str = '',
                 filters_name: Optional[list[str]] = None) -> Callable[[type[BaseTool]], type[BaseTool]]:
        """Decorator factory for tool class registration.
        Args:
            name: Optional custom tool name
            description: Optional tool description
            filters_name: Optional list of filter names
        Returns:
            Class decorator function
        """

        def decorator(cls: type[BaseTool]) -> type[BaseTool]:
            """Actual decorator that registers the tool class."""
            self.register(cls.__name__, cls)
            nonlocal name, description
            name = name or cls.__name__
            description = description or cls.__doc__ or ''
            tool_instance = self.create_and_save(cls.__name__,
                                                 name=name,
                                                 description=description,
                                                 filters_name=filters_name)  # type: ignore
            assert isinstance(tool_instance, BaseTool)
            return cls

        return decorator

    def get_tool(self, name: Optional[str] = None) -> Optional[Union[BaseTool, list[BaseTool]]]:
        """Retrieve tool by name.

        Args:
            name: Tool name to lookup

        Returns:
            BaseTool instance if found, None otherwise
        """
        if name is None:
            return list(self.list_instance().values())
        return self.get_instance(name)


class ToolRegistry(SingletonBase):
    """Tool registry.

    This singleton class provides:
    - Tool registration and lookup functionality
    - Type-based tool management
    - Namespace isolation for different tool types
    """

    def __init__(self) -> None:
        super().__init__()
        self._tool_registry: _ToolManager = _ToolManager()

    def register(self,
                 name: str = '',
                 description: str = '',
                 filters_name: Optional[list[str]] = None) -> Callable[[type[BaseTool]], type[BaseTool]]:
        """Register a tool.

        Args:
            name: Optional custom name
            description: Optional description
        """
        return self._tool_registry.register(name, description, filters_name)

    def add(self, tool: BaseTool) -> None:
        """Add a tool to the registry."""
        self._tool_registry.add(tool)

    def get(self, name: Optional[str] = None) -> Optional[Union[BaseTool, list[BaseTool]]]:
        """Get a tool from the registry."""
        return self._tool_registry.get_tool(name)


def register_tool(
    name: str = '',
    description: str = '',
    filters_name: Optional[list[str]] = None,
) -> Callable:
    """Public decorator for registering tools and functions.

    Args:
        name: Optional custom name
        description: Optional description
        filters_name: Optional list of filter names

    Returns:
        Decorator function for classes or callables

    Example:
        @register_tool("get_weather")
        def get_weather(city: str) -> dict:
            '''Get weather for a city.'''
            return {"temp": 20}
    """

    def decorator(obj: Union[type[BaseTool], Callable]):
        """Inner decorator handling registration logic."""
        registry = ToolRegistry()
        if isinstance(obj, type) and issubclass(obj, BaseTool):
            return registry.register(name, description, filters_name)(obj)
        elif callable(obj):
            tool = FunctionTool(obj, filters_name=filters_name)
            registry.add(tool)
            return tool
        raise TypeError("Can only register BaseTool subclasses or callable functions")

    return decorator


def get_tool(name: str) -> BaseTool | None:
    """Public interface to retrieve tools by name.

    Args:
        name: Name of tool to retrieve

    Returns:
        Registered tool instance or None
    """
    return ToolRegistry().get(name)


class _ToolSetManager(BaseRegistryFactory[BaseToolSet]):
    """Singleton registry for managing BaseToolSet instances.

    Provides centralized storage and retrieval of toolset instances with:
    - Thread-safe access
    - Name-based lookup
    - Decorator support

    Attributes:
        __tools_set_cache: Dictionary mapping toolset names to instances
    """

    def add(self, tool_set: BaseToolSet) -> None:
        """Register a toolset instance.

        Args:
            tool_set: Toolset instance to register
            force: If True, overwrites existing registration

        Raises:
            TypeError: If name conflict exists and force=False
        """
        if tool_set.name in self._instance_map:
            raise TypeError(f"Tool set '{tool_set.name}' already exists.")
        self._instance_map[tool_set.name] = tool_set

    def register(self, name: str = '') -> Callable[[type[BaseToolSet]], type[BaseToolSet]]:
        """Decorator factory for toolset class registration.

        Args:
            name: Optional custom toolset name
            force: Allow overwriting existing registration

        Returns:
            Class decorator function
        """

        def decorator(cls: type[BaseToolSet]) -> type[BaseToolSet]:
            """Actual decorator that registers the toolset class."""
            nonlocal name
            name = name or cls.__name__

            class _RegisteredToolSet(cls):

                def __init__(self):
                    super().__init__()
                    self.name = name
                    self.initialize()

            _RegisteredToolSet.__name__ = cls.__name__
            _RegisteredToolSet.__qualname__ = cls.__qualname__
            _RegisteredToolSet.__module__ = cls.__module__
            _RegisteredToolSet._model_display_name = name

            tool_set_instance = _RegisteredToolSet()
            assert isinstance(tool_set_instance, BaseToolSet)
            self.add(tool_set_instance)
            return _RegisteredToolSet

        return decorator

    def get_tool_set(self, name: Optional[str] = None) -> Optional[Union[BaseToolSet, list[BaseToolSet]]]:
        """Retrieve toolset by name.

        Args:
            name: Toolset name to lookup

        Returns:
            BaseToolSet instance if found, None otherwise
        """
        if name is None:
            return list(self.list_instance().values())
        return self.get_instance(name)


class ToolSetRegistry(SingletonBase):
    """Tool set registry.

    This singleton class provides:
    - Tool set registration and lookup functionality
    - Type-based tool set management
    - Namespace isolation for different tool sets
    """

    def __init__(self) -> None:
        super().__init__()
        self._tool_set_manager: _ToolSetManager = _ToolSetManager()

    def register(self, name: str = '') -> Callable[[type[BaseToolSet]], type[BaseToolSet]]:
        """Register a tool set.

        Args:
            name: Optional custom name
        """
        return self._tool_set_manager.register(name)

    def add(self, tool_set: BaseToolSet) -> None:
        """Add a tool set to the registry."""
        self._tool_set_manager.add(tool_set)

    def get(self, name: Optional[str] = None) -> Optional[Union[BaseToolSet, list[BaseToolSet]]]:
        """Get a tool set from the registry."""
        return self._tool_set_manager.get_tool_set(name)


def register_tool_set(name: str = '') -> Callable:
    """Public decorator for registering toolsets.

    Args:
        name: Custom toolset name
        force: Allow overwriting existing registration

    Returns:
        Decorator function for toolset classes
    """

    return ToolSetRegistry().register(name)


def get_tool_set(name: str) -> BaseToolSet | None:
    """Public interface to retrieve toolsets by name.

    Args:
        name: Name of toolset to retrieve

    Returns:
        Registered toolset instance or None
    """
    return ToolSetRegistry().get(name)
