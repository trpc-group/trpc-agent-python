from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
if str(_EXAMPLE_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT.parents[1]))

from trpc_agent_sdk.evaluation import AgentEvaluator, EvalConfig

from ..models import PipelineConfig, PipelineResult, GateConfig
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
