# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Registry mapping optimizer algorithm name to BaseOptimizer subclass."""

from __future__ import annotations

import inspect
from typing import Type

from ._base_optimizer import BaseOptimizer


class OptimizerRegistry:
    """Maps optimizer algorithm name to a BaseOptimizer subclass."""

    def __init__(self) -> None:
        self._registry: dict[str, Type[BaseOptimizer]] = {}

    def register(self, name: str, optimizer_class: Type[BaseOptimizer]) -> None:
        """Register an optimizer class under the given algorithm name."""
        if not inspect.isclass(optimizer_class) or not issubclass(optimizer_class, BaseOptimizer):
            raise TypeError(f"optimizer_class must be a subclass of BaseOptimizer, "
                            f"got {optimizer_class!r}")
        self._registry[name] = optimizer_class

    def list_registered(self) -> list[str]:
        """Return sorted algorithm names currently registered."""
        return sorted(self._registry.keys())

    def get(self, name: str) -> Type[BaseOptimizer]:
        """Return the optimizer class registered under name; raise if absent."""
        if name not in self._registry:
            raise ValueError(f"No optimizer registered for algorithm: {name}. "
                             f"Available algorithms: {self.list_registered()}")
        return self._registry[name]


OPTIMIZER_REGISTRY = OptimizerRegistry()
