from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
if str(_EXAMPLE_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT.parents[1]))

from ..models import PipelineConfig
from ..pipeline import EvalOptimizePipeline


def test_pipeline_from_config_trace_mode():
    """Pipeline loads trace mode config correctly."""
    config_data = {
        "mode": "trace",
        "baseline_prompt_path": "/tmp/baseline.md",
        "candidate_prompt_path": "/tmp/candidate.md",
        "train_baseline_evalset": "/tmp/train_base.json",
        "train_candidate_evalset": "/tmp/train_cand.json",
        "val_baseline_evalset": "/tmp/val_base.json",
        "val_candidate_evalset": "/tmp/val_cand.json",
        "output_dir": "/tmp/outputs",
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains"}}},
                }
            ],
            "num_runs": 1,
        },
        "seed": 42,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        import json

        json.dump(config_data, f)
        config_path = f.name

    try:
        pipeline = EvalOptimizePipeline.from_config(config_path)
        assert pipeline._config.mode == "trace"
        assert pipeline._config.baseline_prompt_path == "/tmp/baseline.md"
        assert pipeline._config.seed == 42
    finally:
        os.unlink(config_path)


def test_pipeline_from_config_live_mode():
    """Pipeline loads live mode config correctly."""
    Path("/tmp/train.json").touch()
    Path("/tmp/val.json").touch()
    Path("/tmp/optimizer.json").touch()

    config_data = {
        "mode": "live",
        "live_train_evalset": "/tmp/train.json",
        "live_val_evalset": "/tmp/val.json",
        "optimizer_config_path": "/tmp/optimizer.json",
        "target_prompt_name": "system_prompt",
        "output_dir": "/tmp/outputs",
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains"}}},
                }
            ],
            "num_runs": 1,
        },
        "seed": 42,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        import json

        json.dump(config_data, f)
        config_path = f.name

    try:
        pipeline = EvalOptimizePipeline.from_config(
            config_path, call_agent=AsyncMock(), target_prompt=AsyncMock()
        )
        assert pipeline._config.mode == "live"
        assert pipeline._live_call_agent is not None
    finally:
        os.unlink(config_path)


def test_pipeline_live_mode_missing_params():
    """Live mode requires call_agent and target_prompt."""
    config_data = {
        "mode": "live",
        "live_train_evalset": "/tmp/train.json",
        "live_val_evalset": "/tmp/val.json",
        "output_dir": "/tmp/outputs",
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains"}}},
                }
            ],
            "num_runs": 1,
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        import json

        json.dump(config_data, f)
        config_path = f.name

    try:
        with pytest.raises(ValueError, match="call_agent"):
            EvalOptimizePipeline.from_config(config_path)
    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_pipeline_build_split_result():
    """_build_split_result extracts pass rates from EvaluateResult."""
    from trpc_agent_sdk.evaluation import (
        EvalCaseResult,
        EvalMetricResult,
        EvalStatus,
        EvaluateResult,
        EvalSetAggregateResult,
    )

    eval_results_by_eval_id = {
        "case_a": [
            EvalCaseResult(
                eval_set_id="test",
                eval_id="case_a",
                final_eval_status=EvalStatus.PASSED,
                overall_eval_metric_results=[
                    EvalMetricResult(
                        metric_name="m1",
                        score=1.0,
                        threshold=1.0,
                        eval_status=EvalStatus.PASSED,
                    ),
                ],
                eval_metric_result_per_invocation=[],
                session_id="s1",
            )
        ],
        "case_b": [
            EvalCaseResult(
                eval_set_id="test",
                eval_id="case_b",
                final_eval_status=EvalStatus.FAILED,
                overall_eval_metric_results=[
                    EvalMetricResult(
                        metric_name="m1",
                        score=0.0,
                        threshold=1.0,
                        eval_status=EvalStatus.FAILED,
                    ),
                ],
                eval_metric_result_per_invocation=[],
                session_id="s1",
            )
        ],
    }

    result = EvaluateResult(
        results_by_eval_set_id={
            "test": EvalSetAggregateResult(
                eval_results_by_eval_id=eval_results_by_eval_id,
                num_runs=1,
            )
        }
    )

    pipeline = EvalOptimizePipeline.__new__(EvalOptimizePipeline)
    pipeline._config = PipelineConfig.model_validate(
        {
            "mode": "trace",
            "output_dir": "/tmp",
            "evaluate": {
                "metrics": [
                    {
                        "metric_name": "m1",
                        "threshold": 1.0,
                        "criterion": {
                            "final_response": {"text": {"match": "contains"}}
                        },
                    }
                ]
            },
        }
    )

    sr = pipeline._build_split_result(result)
    assert sr.pass_rate == 0.5
    assert "case_a" in sr.per_case
    assert sr.per_case["case_a"].passed is True
    assert sr.per_case["case_b"].passed is False
    assert "m1" in sr.metric_breakdown


# ── Helpers ──────────────────────────────────────────────────────


def _make_fake_eval_result(
    eval_set_id: str,
    case_ids: list[str],
    passed: list[bool],
    metric_name: str = "m1",
) -> "EvaluateResult":
    from trpc_agent_sdk.evaluation import (
        EvalCaseResult,
        EvalMetricResult,
        EvalStatus,
        EvaluateResult,
        EvalSetAggregateResult,
    )

    eval_results_by_eval_id: dict[str, list[EvalCaseResult]] = {}
    for case_id, is_pass in zip(case_ids, passed):
        status = EvalStatus.PASSED if is_pass else EvalStatus.FAILED
        score = 1.0 if is_pass else 0.0
        eval_results_by_eval_id[case_id] = [
            EvalCaseResult(
                eval_set_id=eval_set_id,
                eval_id=case_id,
                final_eval_status=status,
                overall_eval_metric_results=[
                    EvalMetricResult(
                        metric_name=metric_name,
                        score=score,
                        threshold=1.0,
                        eval_status=status,
                    ),
                ],
                eval_metric_result_per_invocation=[],
                session_id=f"s_{case_id}",
            )
        ]

    return EvaluateResult(
        results_by_eval_set_id={
            eval_set_id: EvalSetAggregateResult(
                eval_results_by_eval_id=eval_results_by_eval_id,
                num_runs=1,
            )
        }
    )


def _make_pipeline(config_overrides: dict | None = None) -> EvalOptimizePipeline:
    base = {
        "mode": "trace",
        "output_dir": "/tmp/fake_outputs",
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "m1",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains"}}},
                }
            ],
            "num_runs": 1,
        },
        "train_baseline_evalset": "/tmp/train_base.json",
        "val_baseline_evalset": "/tmp/val_base.json",
        "train_candidate_evalset": "/tmp/train_cand.json",
        "val_candidate_evalset": "/tmp/val_cand.json",
        "seed": 42,
    }
    if config_overrides:
        base.update(config_overrides)

    pipeline = EvalOptimizePipeline.__new__(EvalOptimizePipeline)
    pipeline._config = PipelineConfig.model_validate(base)
    pipeline._live_call_agent = None
    pipeline._live_target_prompt = None
    pipeline._optimizer_call = None
    return pipeline


# ── New high-priority tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_write_eval_config_temp_creates_and_returns_path():
    pipeline = _make_pipeline()
    path = await pipeline._write_eval_config_temp()
    try:
        assert os.path.isfile(path)
        import json
        with open(path) as f:
            data = json.load(f)
        assert "metrics" in data
        assert data["numRuns"] == 1
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_run_optimization_calls_injected_hook():
    pipeline = _make_pipeline({"mode": "live"})
    hook = AsyncMock()
    pipeline._optimizer_call = hook
    await pipeline._run_optimization()
    hook.assert_called_once_with(pipeline)


@pytest.mark.asyncio
async def test_run_trace_mode_orchestration():
    pipeline = _make_pipeline()

    fake_train_base = _make_fake_eval_result("train_base", ["a", "b"], [False, False])
    fake_train_cand = _make_fake_eval_result("train_cand", ["a", "b"], [True, False])
    fake_val_base = _make_fake_eval_result("val_base", ["c", "d"], [True, True])
    fake_val_cand = _make_fake_eval_result("val_cand", ["c", "d"], [True, False])

    async def _fake_run_eval(_path: str):
        if "train_base" in _path:
            return fake_train_base
        if "train_cand" in _path:
            return fake_train_cand
        if "val_base" in _path:
            return fake_val_base
        if "val_cand" in _path:
            return fake_val_cand
        raise RuntimeError(f"unexpected path: {_path}")

    with patch.object(pipeline, "_run_eval", side_effect=_fake_run_eval):
        with patch("examples.optimization.eval_optimize_loop.pipeline.write_reports"):
            result = await pipeline.run()

    assert result.mode == "trace"
    assert result.seed == 42
    assert "train" in result.baseline
    assert "val" in result.baseline
    assert result.baseline["train"].pass_rate == 0.0   # both fail
    assert result.baseline["val"].pass_rate == 1.0      # both pass
    assert result.candidate["train"].pass_rate == 0.5   # one passes now
    assert result.candidate["val"].pass_rate == 0.5     # one regressed
    # Delta: train has newly_passing, val has newly_failing
    assert "a" in result.delta.train.newly_passing
    assert "d" in result.delta.val.newly_failing


@pytest.mark.asyncio
async def test_run_optimization_calls_agent_optimizer_when_no_hook():
    pipeline = _make_pipeline(
        {
            "mode": "live",
            "optimizer_config_path": "/tmp/opt.json",
            "live_train_evalset": "/tmp/train.json",
            "live_val_evalset": "/tmp/val.json",
        }
    )
    with patch(
        "examples.optimization.eval_optimize_loop.pipeline.AgentOptimizer.optimize",
        new_callable=AsyncMock,
    ) as mock_optimize:
        await pipeline._run_optimization()

    mock_optimize.assert_called_once()
    call_kwargs = mock_optimize.call_args.kwargs
    assert call_kwargs["config_path"] == "/tmp/opt.json"
    assert call_kwargs["train_dataset_path"] == "/tmp/train.json"
