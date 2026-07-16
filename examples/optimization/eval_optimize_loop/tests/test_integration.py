from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent

from ..pipeline import EvalOptimizePipeline  # noqa: E402


@pytest.mark.asyncio
async def test_trace_mode_accept():
    pipeline_json = {
        "mode": "trace",
        "baseline_prompt_path": str(_EXAMPLE_ROOT / "prompts" / "baseline_system.md"),
        "candidate_prompt_path": str(_EXAMPLE_ROOT / "prompts" / "optimized_system.md"),
        "train_baseline_evalset": str(_EXAMPLE_ROOT / "evalsets" / "train_baseline.evalset.json"),
        "train_candidate_evalset": str(_EXAMPLE_ROOT / "evalsets" / "train_candidate.evalset.json"),
        "val_baseline_evalset": str(_EXAMPLE_ROOT / "evalsets" / "val_baseline.evalset.json"),
        "val_candidate_evalset": str(_EXAMPLE_ROOT / "evalsets" / "val_candidate.evalset.json"),
        "output_dir": str(tempfile.mkdtemp()),
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains", "case_insensitive": True}}},
                }
            ],
            "num_runs": 1,
        },
        "gate": {
            "min_improvement": 0.0,
            "allow_new_fails": True,
        },
        "seed": 42,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(pipeline_json, f)
        config_path = f.name

    try:
        pipeline = EvalOptimizePipeline.from_config(config_path)
        result = await pipeline.run()

        assert result.mode == "trace"
        assert result.gate_decision == "ACCEPT"

        output_dir = pipeline_json["output_dir"]
        assert os.path.isfile(os.path.join(output_dir, "optimization_report.json"))
        assert os.path.isfile(os.path.join(output_dir, "optimization_report.md"))

        baseline_train = result.baseline["train"]
        candidate_train = result.candidate["train"]
        assert baseline_train.pass_rate == pytest.approx(0.333, abs=0.01)
        assert candidate_train.pass_rate == pytest.approx(0.333, abs=0.01)

        baseline_val = result.baseline["val"]
        candidate_val = result.candidate["val"]
        assert baseline_val.pass_rate == pytest.approx(0.333, abs=0.01)
        assert candidate_val.pass_rate == pytest.approx(0.333, abs=0.01)

        assert "case_train_optimizable" in result.delta.train.newly_passing
        assert "case_train_regression" in result.delta.train.newly_failing
        assert "case_val_improves" in result.delta.val.newly_passing
        assert "case_val_regression" in result.delta.val.newly_failing

        assert result.failure_attribution.failed_cases >= 1
        assert len(result.failure_attribution.categories) >= 1

    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_trace_mode_reject():
    pipeline_json = {
        "mode": "trace",
        "baseline_prompt_path": str(_EXAMPLE_ROOT / "prompts" / "baseline_system.md"),
        "candidate_prompt_path": str(_EXAMPLE_ROOT / "prompts" / "optimized_system.md"),
        "train_baseline_evalset": str(_EXAMPLE_ROOT / "evalsets" / "train_baseline.evalset.json"),
        "train_candidate_evalset": str(_EXAMPLE_ROOT / "evalsets" / "train_candidate.evalset.json"),
        "val_baseline_evalset": str(_EXAMPLE_ROOT / "evalsets" / "val_baseline.evalset.json"),
        "val_candidate_evalset": str(_EXAMPLE_ROOT / "evalsets" / "val_candidate.evalset.json"),
        "output_dir": str(tempfile.mkdtemp()),
        "evaluate": {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains", "case_insensitive": True}}},
                }
            ],
            "num_runs": 1,
        },
        "gate": {
            "min_improvement": 0.0,
            "allow_new_fails": False,
        },
        "seed": 42,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(pipeline_json, f)
        config_path = f.name

    try:
        pipeline = EvalOptimizePipeline.from_config(config_path)
        result = await pipeline.run()

        assert result.mode == "trace"
        assert result.gate_decision == "REJECT"
        assert any("newly failing" in r.lower() for r in result.gate_reasons)

        output_dir = pipeline_json["output_dir"]
        assert os.path.isfile(os.path.join(output_dir, "optimization_report.json"))
        assert os.path.isfile(os.path.join(output_dir, "optimization_report.md"))

    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_trace_mode_overfitting():
    with tempfile.TemporaryDirectory() as tmpdir:
        train_base_path = os.path.join(tmpdir, "train_base.json")
        train_cand_path = os.path.join(tmpdir, "train_cand.json")
        val_base_path = os.path.join(tmpdir, "val_base.json")
        val_cand_path = os.path.join(tmpdir, "val_cand.json")

        base_evalset = lambda eid, actual: {
            "eval_set_id": eid,
            "eval_cases": [
                {
                    "eval_id": "case_1",
                    "eval_mode": "trace",
                    "conversation": [{
                        "invocation_id": "t1",
                        "user_content": {"parts": [{"text": "q"}], "role": "user"},
                        "final_response": {"parts": [{"text": "答案：42"}], "role": "model"},
                    }],
                    "actual_conversation": [{
                        "invocation_id": "t1",
                        "user_content": {"parts": [{"text": "q"}], "role": "user"},
                        "final_response": {"parts": [{"text": actual}], "role": "model"},
                    }],
                    "session_input": {"app_name": "test", "user_id": "u", "state": {}},
                }
            ]
        }

        with open(train_base_path, "w") as f:
            json.dump(base_evalset("train_base", "答案：99"), f)
        with open(train_cand_path, "w") as f:
            json.dump(base_evalset("train_cand", "答案：42"), f)
        with open(val_base_path, "w") as f:
            json.dump(base_evalset("val_base", "答案：42"), f)
        with open(val_cand_path, "w") as f:
            json.dump(base_evalset("val_cand", "答案：99"), f)

        pipeline_json = {
            "mode": "trace",
            "train_baseline_evalset": train_base_path,
            "train_candidate_evalset": train_cand_path,
            "val_baseline_evalset": val_base_path,
            "val_candidate_evalset": val_cand_path,
            "output_dir": tmpdir,
            "evaluate": {
                "metrics": [{
                    "metric_name": "final_response_avg_score",
                    "threshold": 1.0,
                    "criterion": {"final_response": {"text": {"match": "contains", "case_insensitive": True}}},
                }],
                "num_runs": 1,
            },
            "gate": {"min_improvement": -1.0, "allow_new_fails": True},
            "seed": 42,
        }

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump(pipeline_json, f)

        pipeline = EvalOptimizePipeline.from_config(config_path)
        result = await pipeline.run()

        assert result.overfitting_warning is True
        assert result.delta.train_pass_rate_delta > 0
        assert result.delta.val_pass_rate_delta < 0
        assert result.gate_decision == "ACCEPT"
