from __future__ import annotations

import json
from pathlib import Path

from ..models import GateConfig, GateDecision, PipelineConfig, PipelineResult


def test_gate_config_defaults():
    gc = GateConfig()
    assert gc.min_improvement == 0.0
    assert gc.allow_new_fails is False
    assert gc.protected_case_ids == []
    assert gc.max_cost_usd is None
    assert gc.max_duration_seconds == 180


def test_gate_decision_accept():
    gd = GateDecision(decision="ACCEPT", reasons=["val improved by 0.33"])
    assert gd.decision == "ACCEPT"
    assert len(gd.reasons) == 1
    assert gd.overfitting_warning is False


def test_gate_decision_reject():
    gd = GateDecision(decision="REJECT", reasons=["new failures in val"], overfitting_warning=True)
    assert gd.decision == "REJECT"
    assert gd.overfitting_warning is True


def test_pipeline_config_trace_mode():
    data = {
        "mode": "trace",
        "baseline_prompt_path": "prompts/baseline.md",
        "candidate_prompt_path": "prompts/optimized.md",
        "train_baseline_evalset": "evalsets/train_base.json",
        "train_candidate_evalset": "evalsets/train_cand.json",
        "val_baseline_evalset": "evalsets/val_base.json",
        "val_candidate_evalset": "evalsets/val_cand.json",
        "output_dir": "outputs",
        "evaluate": {"metrics": [{"metric_name": "final_response_avg_score", "threshold": 1.0, "criterion": {"final_response": {"text": {"match": "contains"}}}}], "num_runs": 1},
        "gate": {"min_improvement": 0.1, "allow_new_fails": False},
        "seed": 42,
    }
    config = PipelineConfig.model_validate(data)
    assert config.mode == "trace"
    assert config.baseline_prompt_path == "prompts/baseline.md"
    assert config.gate.min_improvement == 0.1


def test_pipeline_config_live_mode():
    data = {
        "mode": "live",
        "live_train_evalset": "evalsets/live_train.json",
        "live_val_evalset": "evalsets/live_val.json",
        "optimizer_config_path": "optimizer.json",
        "target_prompt_name": "system_prompt",
        "output_dir": "outputs",
        "evaluate": {"metrics": [{"metric_name": "final_response_avg_score", "threshold": 1.0, "criterion": {"final_response": {"text": {"match": "contains"}}}}], "num_runs": 1},
        "seed": 42,
    }
    config = PipelineConfig.model_validate(data)
    assert config.mode == "live"
    assert config.optimizer_config_path == "optimizer.json"
    assert config.target_prompt_name == "system_prompt"


def test_pipeline_result_roundtrip():
    result = PipelineResult(
        mode="trace",
        gate_decision="ACCEPT",
        gate_reasons=["val pass rate improved from 0.33 to 1.00"],
        duration_seconds=2.5,
        seed=42,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:05",
    )
    # Verify direct read-back works
    loaded = PipelineResult.model_validate_json(result.model_dump_json())
    assert loaded.mode == "trace"
    assert loaded.gate_decision == "ACCEPT"
    assert loaded.seed == 42
