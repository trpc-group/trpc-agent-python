# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Filter runner module.

This module defines the filter runner class, which is used to run filters.
"""

from abc import ABC
from typing import Any
from typing import Optional
from typing import Union

from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.context import AgentContext

from ._base_filter import BaseFilter
from ._registry import get_filter
from ._run_filter import AgentFilterAsyncGenHandleType
from ._run_filter import AgentFilterHandleType
from ._run_filter import run_filters
from ._run_filter import run_stream_filters


class FilterRunner(ABC):
    """Filter runner

    Args:
        filters_name: List of filter names.
        filters: List of filter instances.
    """

    def __init__(self, filters_name: Optional[list[str]] = None, filters: Optional[list[BaseFilter]] = None):
        self._filters_name = filters_name or []
        self._filters: list[BaseFilter] = filters or []
        self._name = self.__class__.__name__
        self._type = FilterType.UNSUPPORTED

    @property
    def filters_name(self) -> list[str]:
        """Get filter name."""
        return self._filters_name

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    def _init_filters(self):
        """Initialize filters."""
        for filter_name in self._filters_name:
            filter_instance = get_filter(self._type, filter_name)
            if not filter_instance:
                raise ValueError(f"Filter {filter_name} not found, type: {self._type}")
            self._filters.append(filter_instance)

    @property
    def filters(self) -> list[BaseFilter]:
        """Get filters."""
        return self._filters

    def add_filters(self, filters: list[Union[BaseFilter, str]], force: bool = False):
        """Add filters."""
        filter_list = []
        for filter in filters or []:
            if isinstance(filter, str):
                filter_instance = get_filter(self._type, filter)
                if not filter_instance:
                    raise ValueError(f"Filter {filter} not found, type: {self._type}")
                filter_list.append(filter_instance)
            else:
                filter_list.append(filter)
        if force:
            self._filters = filter_list
        else:
            self._filters.extend(filter_list)  # type: ignore

    def add_one_filter(self, filter: Union[BaseFilter, str], index: Optional[int] = None, force: bool = False):
        """Add one filter."""
        if isinstance(filter, str):
            filter_instance = get_filter(self._type, filter)
            if not filter_instance:
                raise ValueError(f"Filter {filter} not found, type: {self._type}")
        else:
            filter_instance = filter
        if not force:
            for f in self._filters:
                if f.name == filter_instance.name:
                    return
        if index is None:
            self._filters.append(filter_instance)
        else:
            self._filters.insert(index, filter_instance)

    def get_filter(self, filter_name: str) -> BaseFilter:
        """Get filter."""
        for filter in self._filters:
            if filter.name == filter_name:
                return filter
        raise ValueError(f"Filter {filter_name} not found")

    async def _run_filters(self,
                           ctx: AgentContext,
                           req: Any,
                           handle: AgentFilterHandleType,
                           extra_filters: Optional[list[BaseFilter]] = None) -> Any:
        """Run filters."""
        filters = self._filters.copy()
        if extra_filters:
            filters.extend(extra_filters)
        return await run_filters(ctx, req, filters, handle)

    async def _run_stream_filters(self,
                                  ctx: AgentContext,
                                  req: Any,
                                  handle: AgentFilterAsyncGenHandleType,
                                  extra_filters: Optional[list[BaseFilter]] = None) -> Any:
        """Run stream filters."""
        filters = self._filters.copy()
        if extra_filters:
            filters.extend(extra_filters)
        async for event in run_stream_filters(ctx, req, filters, handle):
            yield event
