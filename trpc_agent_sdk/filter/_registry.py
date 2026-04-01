# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Filter registry for TRPC Agent framework."""

from functools import partial
from typing import Callable

from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.utils import BaseRegistryFactory
from trpc_agent_sdk.utils import SingletonBase

from ._base_filter import BaseFilter


class _FilterManager(BaseRegistryFactory[BaseFilter]):
    """Central registry for managing TRPC filters.

    This singleton class provides:
    - Filter registration and lookup functionality
    - Type-based filter management
    - Namespace isolation for different filter types

    Attributes:
        __filter_manager_cache: Dictionary mapping filter types to their managers
    """

    def __init__(self, filter_type: FilterType = FilterType.UNSUPPORTED) -> None:
        super().__init__()
        self._type = filter_type

    @property
    def filter_type(self) -> FilterType:
        """Get the filter type."""
        return self._type

    def register_filter(self, name: str) -> Callable[[type[BaseFilter]], type[BaseFilter]]:
        """Decorator factory for registering filter classes.

        This decorator automatically registers filter classes with the filter management system,
        associating them with a specific filter type and name.

        Args:
            filter_type: The FilterType this filter belongs to (e.g., FilterType.MODEL)
            name: Unique name identifier for this filter
            force: Whether to overwrite existing registration if name conflicts

        Returns:
            A decorator function that will register the filter class

        Example:
            @register_filter(FilterType.MODEL, "my_model_filter")
            class MyModelFilter(BaseFilter):
                ...
        """

        def decorator(cls: type[BaseFilter]) -> type[BaseFilter]:
            """Actual decorator that performs the filter registration.

          Args:
              cls: The filter class to be registered (must inherit from BaseFilter)

          Returns:
              The original class (for chaining)

          Raises:
              AssertionError: If the class doesn't instantiate to a BaseFilter
              TypeError: If filter type is invalid
          """
            """Decorator that registers a filter class.

          Args:
              cls: The filter class to register

          Returns:
              The original class (for chaining)

          Raises:
              TypeError: If filter already exists and force=False
          """
            nonlocal name
            self.register(cls.__name__, cls)
            filter_instance = self.create_and_save(cls.__name__, name)
            assert isinstance(filter_instance, BaseFilter)
            assert isinstance(name, str)
            filter_instance.type = self._type
            filter_instance.name = name
            return cls

        # We're called as @dataclass without parens.
        return decorator


class FilterRegistry(SingletonBase):
    """Filter registry.

    This singleton class provides:
    - Filter registration and lookup functionality
    - Type-based filter management
    - Namespace isolation for different filter types
    """

    def __init__(self) -> None:
        super().__init__()
        self._filter_registry: dict[FilterType, _FilterManager] = {}
        # Initialize filter managers for each filter type
        for t in FilterType.__members__.values():
            if t != FilterType.UNSUPPORTED:
                self._filter_registry[t] = _FilterManager(t)

    def register(self, filter_type: FilterType, name: str) -> Callable[[type[BaseFilter]], type[BaseFilter]]:
        """Register a filter class.

        Args:
            filter_type: The FilterType this filter belongs to (e.g., FilterType.MODEL)
            name: Unique name identifier for this filter
            force: Whether to overwrite existing registration if name conflicts

        Returns:
            A decorator function that will register the filter class
        """
        return self._filter_registry[filter_type].register_filter(name)

    def get(self, filter_type: FilterType, name: str) -> BaseFilter | None:
        """Get a specific filter by type and name.

        Args:
            filter_type: The FilterType this filter belongs to (e.g., FilterType.MODEL)
            name: The name of the filter

        Returns:
            The BaseFilter instance if found, None otherwise
        """
        return self._filter_registry[filter_type].get_instance(name)


def register_filter(filter_type: FilterType, name: str) -> Callable[[type[BaseFilter]], type[BaseFilter]]:
    """Decorator factory for registering filter classes.

    This decorator automatically registers filter classes with the filter management system,
    associating them with a specific filter type and name.

    Args:
        filter_type: The FilterType this filter belongs to (e.g., FilterType.MODEL)
        name: Unique name identifier for this filter

    Returns:
        A decorator function that will register the filter class

    Example:
        @register_filter(FilterType.MODEL, "my_model_filter")
        class MyModelFilter(BaseFilter):
            ...
    """
    return FilterRegistry().register(filter_type, name)


def get_filter(filter_type: FilterType, name: str) -> BaseFilter | None:
    """Get a specific filter by type and name.

    Args:
        filter_type: The FilterType this filter belongs to (e.g., FilterType.MODEL)
        name: The name of the filter
    """
    return FilterRegistry().get(filter_type, name)


register_tool_filter = partial(register_filter, FilterType.TOOL)
register_model_filter = partial(register_filter, FilterType.MODEL)
register_agent_filter = partial(register_filter, FilterType.AGENT)

get_tool_filter = partial(get_filter, FilterType.TOOL)
get_model_filter = partial(get_filter, FilterType.MODEL)
get_agent_filter = partial(get_filter, FilterType.AGENT)
