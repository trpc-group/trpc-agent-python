# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for BaseOptimizer abstract interface."""

from __future__ import annotations

from typing import Optional

import pytest

from trpc_agent_sdk.evaluation._base_optimizer import BaseOptimizer
from trpc_agent_sdk.evaluation._eval_callbacks import Callbacks
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfigFile
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions
from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt


def _dummy_result() -> OptimizeResult:
    return OptimizeResult(
        algorithm="stub",
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


def _make_config() -> OptimizeConfigFile:
    return OptimizeConfigFile.model_validate(
        {
            "evaluate": {"metrics": [{"metric_name": "x", "threshold": 0.7}]},
            "optimize": {
                "algorithm": {
                    "name": "gepa_reflective",
                    "reflection_lm": OptimizeModelOptions(
                        model_name="m", api_key="k"
                    ).model_dump(),
                    "max_metric_calls": 10,
                }
            },
        }
    )


async def _noop_call_agent(query: str) -> str:
    return ""


class _StubOptimizer(BaseOptimizer):
    async def run(self) -> OptimizeResult:
        return _dummy_result()


class _IncompleteOptimizer(BaseOptimizer):
    """Subclass without implementing run()."""


def test_base_optimizer_cannot_instantiate_directly(tmp_path):
    target_prompt = TargetPrompt().add_path("system_prompt", str(_seed_prompt(tmp_path)))
    with pytest.raises(TypeError):
        BaseOptimizer(
            config=_make_config(),
            call_agent=_noop_call_agent,
            target_prompt=target_prompt,
            train_dataset_path=str(tmp_path / "train.json"),
            validation_dataset_path=str(tmp_path / "val.json"),
        )


def test_base_optimizer_subclass_without_run_cannot_instantiate(tmp_path):
    target_prompt = TargetPrompt().add_path("system_prompt", str(_seed_prompt(tmp_path)))
    with pytest.raises(TypeError):
        _IncompleteOptimizer(
            config=_make_config(),
            call_agent=_noop_call_agent,
            target_prompt=target_prompt,
            train_dataset_path=str(tmp_path / "train.json"),
            validation_dataset_path=str(tmp_path / "val.json"),
        )


def test_base_optimizer_stores_constructor_arguments(tmp_path):
    seed_path = _seed_prompt(tmp_path)
    target_prompt = TargetPrompt().add_path("system_prompt", str(seed_path))
    config = _make_config()
    train_path = str(tmp_path / "train.json")
    val_path = str(tmp_path / "val.json")
    callbacks = Callbacks()

    optimizer = _StubOptimizer(
        config=config,
        call_agent=_noop_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=train_path,
        validation_dataset_path=val_path,
        callbacks=callbacks,
    )

    assert optimizer.config is config
    assert optimizer.call_agent is _noop_call_agent
    assert optimizer.target_prompt is target_prompt
    assert optimizer.train_dataset_path == train_path
    assert optimizer.validation_dataset_path == val_path
    assert optimizer.callbacks is callbacks


def test_base_optimizer_callbacks_default_to_none(tmp_path):
    target_prompt = TargetPrompt().add_path("system_prompt", str(_seed_prompt(tmp_path)))
    optimizer = _StubOptimizer(
        config=_make_config(),
        call_agent=_noop_call_agent,
        target_prompt=target_prompt,
        train_dataset_path=str(tmp_path / "train.json"),
        validation_dataset_path=str(tmp_path / "val.json"),
    )
    assert optimizer.callbacks is None


def test_base_optimizer_rejects_positional_arguments(tmp_path):
    target_prompt = TargetPrompt().add_path("system_prompt", str(_seed_prompt(tmp_path)))
    with pytest.raises(TypeError):
        _StubOptimizer(
            _make_config(),
            _noop_call_agent,
            target_prompt,
            str(tmp_path / "train.json"),
            str(tmp_path / "val.json"),
        )


async def test_base_optimizer_run_is_async():
    import inspect

    assert inspect.iscoroutinefunction(BaseOptimizer.run)


def _seed_prompt(tmp_path):
    seed = tmp_path / "system.md"
    seed.write_text("you are a helpful assistant", encoding="utf-8")
    return seed


# ---------------------------------------------------------------------------
# BaseOptimizer.resolve_required_thresholds
# ---------------------------------------------------------------------------


def _stop_cfg(required_metrics):
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    return FrameworkStopConfig(required_metrics=required_metrics)


def test_resolve_required_thresholds_all_returns_full_dict():
    thresholds = {"m1": 0.5, "m2": 0.3}
    assert (
        BaseOptimizer.resolve_required_thresholds(_stop_cfg("all"), thresholds)
        == thresholds
    )


def test_resolve_required_thresholds_list_returns_subset():
    thresholds = {"m1": 0.5, "m2": 0.3, "m3": 0.9}
    assert BaseOptimizer.resolve_required_thresholds(
        _stop_cfg(["m1", "m3"]), thresholds
    ) == {"m1": 0.5, "m3": 0.9}


def test_resolve_required_thresholds_none_returns_empty():
    assert (
        BaseOptimizer.resolve_required_thresholds(_stop_cfg(None), {"m1": 0.5})
        == {}
    )


def test_resolve_required_thresholds_empty_list_returns_empty():
    assert (
        BaseOptimizer.resolve_required_thresholds(_stop_cfg([]), {"m1": 0.5})
        == {}
    )


def test_resolve_required_thresholds_list_silently_drops_unknown_names():
    thresholds = {"m1": 0.5}
    assert BaseOptimizer.resolve_required_thresholds(
        _stop_cfg(["m1", "missing"]), thresholds
    ) == {"m1": 0.5}


def test_resolve_required_thresholds_returns_copy_not_alias():
    thresholds = {"m1": 0.5}
    out = BaseOptimizer.resolve_required_thresholds(_stop_cfg("all"), thresholds)
    out["m1"] = 9.9
    assert thresholds["m1"] == 0.5


# ---------------------------------------------------------------------------
# BaseOptimizer.metrics_meet_thresholds
# ---------------------------------------------------------------------------


def test_metrics_meet_thresholds_empty_required_returns_false():
    assert BaseOptimizer.metrics_meet_thresholds({"m1": 1.0}, {}) is False


def test_metrics_meet_thresholds_all_above_returns_true():
    assert (
        BaseOptimizer.metrics_meet_thresholds(
            {"m1": 0.6, "m2": 0.4}, {"m1": 0.5, "m2": 0.3}
        )
        is True
    )


def test_metrics_meet_thresholds_one_below_returns_false():
    assert (
        BaseOptimizer.metrics_meet_thresholds(
            {"m1": 0.6, "m2": 0.2}, {"m1": 0.5, "m2": 0.3}
        )
        is False
    )


def test_metrics_meet_thresholds_exact_match_returns_true():
    assert BaseOptimizer.metrics_meet_thresholds({"m1": 0.5}, {"m1": 0.5}) is True


def test_metrics_meet_thresholds_missing_breakdown_key_returns_false():
    assert BaseOptimizer.metrics_meet_thresholds({"m2": 0.9}, {"m1": 0.5}) is False
