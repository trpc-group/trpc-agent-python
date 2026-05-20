# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for OptimizerRegistry."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.evaluation._base_optimizer import BaseOptimizer
from trpc_agent_sdk.evaluation._optimize_registry import OPTIMIZER_REGISTRY
from trpc_agent_sdk.evaluation._optimize_registry import OptimizerRegistry
from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult


def _dummy_result() -> OptimizeResult:
    return OptimizeResult(
        algorithm="fake",
        status="SUCCEEDED",
        finish_reason="completed",
        baseline_pass_rate=0.0,
        best_pass_rate=0.0,
        pass_rate_improvement=0.0,
        total_rounds=0,
        total_reflection_lm_calls=0,
        total_judge_model_calls=0,
        duration_seconds=0.0,
        started_at="1970-01-01T00:00:00Z",
        finished_at="1970-01-01T00:00:00Z",
    )


class _FakeOptimizerA(BaseOptimizer):
    async def run(self) -> OptimizeResult:
        return _dummy_result()


class _FakeOptimizerB(BaseOptimizer):
    async def run(self) -> OptimizeResult:
        return _dummy_result()


def test_empty_registry_lists_nothing():
    registry = OptimizerRegistry()
    assert registry.list_registered() == []


def test_register_and_get_returns_class():
    registry = OptimizerRegistry()
    registry.register("fake_a", _FakeOptimizerA)
    assert registry.get("fake_a") is _FakeOptimizerA


def test_list_registered_is_sorted():
    registry = OptimizerRegistry()
    registry.register("zzz", _FakeOptimizerA)
    registry.register("aaa", _FakeOptimizerB)
    assert registry.list_registered() == ["aaa", "zzz"]


def test_register_overwrites_existing_name():
    registry = OptimizerRegistry()
    registry.register("dup", _FakeOptimizerA)
    registry.register("dup", _FakeOptimizerB)
    assert registry.get("dup") is _FakeOptimizerB


def test_get_unknown_algorithm_raises_valueerror_with_available_list():
    registry = OptimizerRegistry()
    registry.register("fake_a", _FakeOptimizerA)
    with pytest.raises(ValueError) as exc_info:
        registry.get("unknown_algo")
    msg = str(exc_info.value)
    assert "unknown_algo" in msg
    assert "fake_a" in msg


def test_get_on_empty_registry_lists_empty_available():
    registry = OptimizerRegistry()
    with pytest.raises(ValueError) as exc_info:
        registry.get("anything")
    assert "anything" in str(exc_info.value)


def test_register_rejects_non_basoptimizer_subclass():
    registry = OptimizerRegistry()

    class _NotAnOptimizer:
        pass

    with pytest.raises(TypeError):
        registry.register("bad", _NotAnOptimizer)


def test_module_level_singleton_is_optimizer_registry_instance():
    assert isinstance(OPTIMIZER_REGISTRY, OptimizerRegistry)


def test_module_level_singleton_contains_registered_algorithms():
    """Importing the evaluation package registers all available algorithms.

    The exact list grows over time, but ``gepa_reflective`` is the v1 baseline
    contract: any algorithm whose optional dependencies are installed and whose
    module is registered in ``_optimize_registrations`` must be reachable via
    ``OPTIMIZER_REGISTRY.get(name)``.
    """
    assert "gepa_reflective" in OPTIMIZER_REGISTRY.list_registered()
