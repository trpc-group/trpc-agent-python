# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for examples/optimization/eval_optimize_loop."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from jsonschema import ValidationError

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "optimization" / "eval_optimize_loop"
RUN_PIPELINE = EXAMPLE_DIR / "run_pipeline.py"
REPORT_SCHEMA = EXAMPLE_DIR / "optimization_report.schema.json"
ROUTE_TOOL_ARGS_METRIC = "route_tool_args_score"


def _gate_summary(
    score: float,
    cases: list[dict[str, Any]],
    *,
    metric_passed: bool = True,
) -> dict[str, Any]:
    return {
        "score": score,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": metric_passed}},
        "case_results": cases,
    }


def _complete_summary(score: float, *, passed: bool, tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "eval_set_id": "stubbed_online",
        "score": score,
        "pass_rate": 1.0 if passed else 0.0,
        "metrics": {
            ROUTE_TOOL_ARGS_METRIC: {
                "score": score,
                "threshold": 1.0,
                "passed": passed,
                "status": "passed" if passed else "failed",
            }
        },
        "case_results": [
            {
                "case_id": "case_1",
                "tags": tags or [],
                "user": "stubbed user",
                "score": score,
                "passed": passed,
                "metrics": {
                    ROUTE_TOOL_ARGS_METRIC: {
                        "score": score,
                        "threshold": 1.0,
                        "passed": passed,
                        "status": "passed" if passed else "failed",
                    }
                },
                "actual_text": "",
                "expected_text": "",
                "key_trace": {
                    "invocation_id": "case_1",
                    "actual_final_response": "",
                    "expected_final_response": "",
                    "error_message": None,
                },
                "root_cause": "" if passed else "final_response_mismatch",
                "reasons": [] if passed else ["stubbed mismatch"],
            }
        ],
        "failed_case_ids": [] if passed else ["case_1"],
        "source": "AgentEvaluator",
    }


def _optimizer_result(token_usage: Any) -> SimpleNamespace:
    return SimpleNamespace(
        status="SUCCEEDED",
        error_message="",
        baseline_pass_rate=0.0,
        best_pass_rate=1.0,
        pass_rate_improvement=1.0,
        stop_reason="completed",
        total_llm_cost=0.01,
        total_reflection_lm_calls=1,
        total_judge_model_calls=0,
        total_token_usage=token_usage,
        best_prompts={"system_prompt": "better system", "router_prompt": "better router"},
        baseline_prompts={"system_prompt": "baseline system", "router_prompt": "baseline router"},
        baseline_metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 0.0},
        best_metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
        rounds=[],
    )


async def _run_stubbed_online(
    *,
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: SimpleNamespace,
    run_id: str,
    gate_config: dict[str, Any] | None = None,
    optimizer_agent_calls: int = 0,
    optimizer_config: Path | None = None,
) -> dict[str, Any]:
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")

    async def fake_optimize(**kwargs: Any) -> SimpleNamespace:
        for _ in range(optimizer_agent_calls):
            await kwargs["call_agent"]("optimizer candidate")
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        return result

    summaries = iter(
        [
            _complete_summary(0.0, passed=False),
            _complete_summary(0.0, passed=False),
            _complete_summary(0.0, passed=False),
            _complete_summary(1.0, passed=True),
            _complete_summary(1.0, passed=True),
            _complete_summary(1.0, passed=True),
        ]
    )

    async def fake_run_evaluator(**_: Any) -> dict[str, Any]:
        return next(summaries)

    import trpc_agent_sdk.evaluation as evaluation_pkg

    if optimizer_agent_calls:

        async def fake_call_agent(_: str) -> str:
            return "{}"

        monkeypatch.setattr(module, "make_online_call_agent", lambda **_: fake_call_agent)
    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    monkeypatch.setattr(module, "run_evaluator", fake_run_evaluator)
    run_dir = await module.run_online(
        seed=7,
        output_dir=tmp_path,
        run_id=run_id,
        gate_config=gate_config,
        optimizer_config=optimizer_config,
    )
    return load_report(run_dir / "optimization_report.json")


def _write_optimizer_config(tmp_path: Path, *, metrics: list[dict[str, Any]]) -> Path:
    payload = load_report(EXAMPLE_DIR / "optimizer.json")
    payload["evaluate"]["metrics"] = metrics
    path = tmp_path / "optimizer.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def load_pipeline_module() -> Any:
    spec = importlib.util.spec_from_file_location("eval_optimize_loop_run_pipeline", RUN_PIPELINE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gate_fails_closed_for_boundary_and_invalid_evidence():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [
            {"case_id": "a", "score": 0.0, "passed": False, "tags": []},
            {"case_id": "b", "score": 0.5, "passed": True, "tags": ["critical"]},
        ],
    )
    valid_candidate = _gate_summary(
        0.75,
        [
            {"case_id": "a", "score": 1.0, "passed": True, "tags": []},
            {"case_id": "b", "score": 0.5, "passed": True, "tags": ["critical"]},
        ],
    )

    exact_boundary = module.apply_gate(
        candidate_id="boundary",
        baseline_val=baseline,
        candidate_val=valid_candidate,
        gate_config={
            "min_validation_delta": 0.5,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert exact_boundary["accepted"] is False

    missing_case = copy.deepcopy(valid_candidate)
    missing_case["case_results"] = missing_case["case_results"][:1]
    missing = module.apply_gate(
        candidate_id="missing",
        baseline_val=baseline,
        candidate_val=missing_case,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert missing["accepted"] is False
    assert missing["missing_case_ids"] == ["b"]

    extra_case = copy.deepcopy(valid_candidate)
    extra_case["case_results"].append({"case_id": "c", "score": 1.0, "passed": True, "tags": []})
    extra = module.apply_gate(
        candidate_id="extra",
        baseline_val=baseline,
        candidate_val=extra_case,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert extra["accepted"] is False
    assert extra["unexpected_case_ids"] == ["c"]

    non_finite = copy.deepcopy(valid_candidate)
    non_finite["score"] = float("nan")
    invalid = module.apply_gate(
        candidate_id="nan",
        baseline_val=baseline,
        candidate_val=non_finite,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert invalid["accepted"] is False
    assert "finite" in " ".join(invalid["reasons"])
    json.dumps(invalid, allow_nan=False)


@pytest.mark.parametrize(
    ("field", "value", "gate_config"),
    [
        ("duration_seconds", "1.0", {"max_duration_seconds": 10}),
        ("duration_seconds", True, {"max_duration_seconds": 10}),
        ("duration_seconds", float("nan"), {"max_duration_seconds": 10}),
        ("duration_seconds", float("inf"), {"max_duration_seconds": 10}),
        ("cost_usd", "1.0", {"max_cost_usd": 10}),
        ("cost_usd", None, {"max_cost_usd": 10}),
        ("cost_usd", True, {"max_cost_usd": 10}),
        ("cost_usd", float("nan"), {"max_cost_usd": 10}),
        ("cost_usd", float("inf"), {"max_cost_usd": 10}),
        ("config", "0.1", {"min_validation_delta": "0.1"}),
        ("config", True, {"min_validation_delta": True}),
        ("config", float("nan"), {"min_validation_delta": float("nan")}),
        ("config", float("inf"), {"min_validation_delta": float("inf")}),
        ("config", float("-inf"), {"min_validation_delta": float("-inf")}),
        ("config", -0.1, {"min_validation_delta": -0.1}),
        ("config", "10", {"max_duration_seconds": "10"}),
        ("config", True, {"max_cost_usd": True}),
        ("config", float("inf"), {"max_cost_usd": float("inf")}),
    ],
)
def test_gate_rejects_malformed_numeric_evidence_without_raising(
    field: str,
    value: Any,
    gate_config: dict[str, Any],
):
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
    )
    candidate = _gate_summary(
        0.75,
        [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}],
    )
    duration_seconds: Any = 1.0
    cost_usd: Any = 0.0
    if field == "duration_seconds":
        duration_seconds = value
    elif field == "cost_usd":
        cost_usd = value

    result = module.apply_gate(
        candidate_id="malformed_numeric",
        baseline_val=baseline,
        candidate_val=candidate,
        gate_config=gate_config,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
    )

    assert result["accepted"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    ("gate_config", "baseline_case", "candidate_case", "reason"),
    [
        (
            {"allow_new_hard_fails": "false", "required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
            {"case_id": "a", "score": 0.25, "passed": True, "tags": []},
            {"case_id": "a", "score": 0.75, "passed": False, "tags": []},
            "hard fail",
        ),
        (
            {"allow_critical_regression": "false", "required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
            {"case_id": "a", "score": 1.0, "passed": True, "tags": ["critical"]},
            {"case_id": "a", "score": 0.75, "passed": True, "tags": ["critical"]},
            "critical",
        ),
    ],
)
def test_gate_allow_flags_require_exact_booleans(
    gate_config: dict[str, Any],
    baseline_case: dict[str, Any],
    candidate_case: dict[str, Any],
    reason: str,
):
    module = load_pipeline_module()
    result = module.apply_gate(
        candidate_id="invalid_allow_flag",
        baseline_val=_gate_summary(0.25, [baseline_case]),
        candidate_val=_gate_summary(0.75, [candidate_case]),
        gate_config=gate_config,
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert reason in " ".join(result["reasons"])
    assert "boolean" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "required_metrics",
    [1, True, {}, [""], [" "], ["metric", "metric"], ["metric", 1], [["metric"]]],
)
def test_gate_rejects_invalid_required_metrics_without_raising(required_metrics: Any):
    module = load_pipeline_module()
    result = module.apply_gate(
        candidate_id="invalid_required_metrics",
        baseline_val=_gate_summary(0.25, [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]),
        candidate_val=_gate_summary(0.75, [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}]),
        gate_config={"required_metrics": required_metrics},
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert "required_metrics" in " ".join(result["reasons"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    ("malformed_field", "malformed_value"),
    [
        ("baseline_val", None),
        ("baseline_val", []),
        ("candidate_val", "invalid"),
        ("candidate_val", 1),
        ("gate_config", None),
        ("gate_config", []),
    ],
)
def test_apply_gate_rejects_non_mapping_inputs_without_raising(
    malformed_field: str,
    malformed_value: Any,
):
    module = load_pipeline_module()
    kwargs: dict[str, Any] = {
        "candidate_id": "non_mapping",
        "baseline_val": _gate_summary(
            0.25,
            [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
        ),
        "candidate_val": _gate_summary(
            0.75,
            [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}],
        ),
        "gate_config": {"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        "duration_seconds": 1.0,
        "cost_usd": 0.0,
    }
    kwargs[malformed_field] = malformed_value

    result = module.apply_gate(**kwargs)

    assert result["accepted"] is False
    assert "mapping" in " ".join(result["reasons"])
    json.dumps(result, allow_nan=False)


def test_load_gate_config_preserves_invalid_single_metric_string_for_rejection():
    module = load_pipeline_module()
    config = module.load_gate_config(overrides={"required_metrics": "metric"})

    assert config["required_metrics"] == "metric"
    result = module.apply_gate(
        candidate_id="single_metric_string",
        baseline_val=_gate_summary(
            0.25,
            [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
        ),
        candidate_val=_gate_summary(
            0.75,
            [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}],
        ),
        gate_config=config,
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert result["accepted"] is False
    assert "required_metrics" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [["allow_new_hard_fails", True], ["required_metrics", []]],
    ],
)
def test_load_gate_config_rejects_non_mapping_sources(tmp_path: Path, payload: Any):
    module = load_pipeline_module()
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gate config.*object"):
        module.load_gate_config(gate_path)
    with pytest.raises(ValueError, match="gate config overrides.*mapping"):
        module.load_gate_config(overrides=payload)


def test_unknown_gate_keys_fail_closed_for_files_overrides_and_direct_calls(tmp_path: Path):
    module = load_pipeline_module()
    typo_gate = {"min_validaton_delta": 0.9}
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(typo_gate), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown gate config"):
        module.load_gate_config(gate_path)
    with pytest.raises(ValueError, match="unknown gate config"):
        module.load_gate_config(overrides=typo_gate)

    result = module.apply_gate(
        candidate_id="typo_gate",
        baseline_val=_gate_summary(
            0.25,
            [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
        ),
        candidate_val=_gate_summary(
            0.75,
            [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}],
        ),
        gate_config={
            **typo_gate,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert result["accepted"] is False
    assert "unknown gate config" in " ".join(result["reasons"])


def test_apply_gate_rejects_summary_score_not_derived_from_cases():
    module = load_pipeline_module()
    baseline_cases = [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]
    candidate_cases = [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]

    result = module.apply_gate(
        candidate_id="forged_aggregate",
        baseline_val=_gate_summary(0.25, baseline_cases),
        candidate_val=_gate_summary(0.75, candidate_cases),
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert "summary score" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "required_metrics",
    [1, "metric", [1], [["metric"]], [""], ["metric", "metric"]],
)
def test_optimizer_required_metrics_rejects_malformed_values(
    tmp_path: Path,
    required_metrics: Any,
):
    module = load_pipeline_module()
    payload = load_report(EXAMPLE_DIR / "optimizer.json")
    payload["optimize"]["stop"]["required_metrics"] = required_metrics
    path = tmp_path / "optimizer.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="required_metrics"):
        module.optimizer_required_metrics(path)


def test_gate_rejects_all_required_metrics_when_evidence_is_empty():
    module = load_pipeline_module()
    candidate = _gate_summary(0.75, [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}])
    candidate["metrics"] = {}

    result = module.apply_gate(
        candidate_id="empty_all_metrics",
        baseline_val=_gate_summary(0.25, [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]),
        candidate_val=candidate,
        gate_config={"required_metrics": "all"},
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert "required_metrics" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    ("duration", "cost", "gate_config"),
    [
        (-1.0, 0.0, {}),
        (1.0, -1.0, {}),
        (1.0, 0.0, {"max_cost_usd": -1.0}),
        (1.0, 0.0, {"max_duration_seconds": -1.0}),
    ],
)
def test_gate_rejects_negative_observed_values_and_budgets(
    duration: float,
    cost: float,
    gate_config: dict[str, Any],
):
    module = load_pipeline_module()
    result = module.apply_gate(
        candidate_id="negative_budget_evidence",
        baseline_val=_gate_summary(0.25, [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]),
        candidate_val=_gate_summary(0.75, [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}]),
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC], **gate_config},
        duration_seconds=duration,
        cost_usd=cost,
    )

    assert result["accepted"] is False
    assert "non-negative" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "candidate_case",
    [
        {"case_id": 1, "score": 0.75, "passed": True, "tags": []},
        {"case_id": " ", "score": 0.75, "passed": True, "tags": []},
        {"case_id": "a", "score": -0.01, "passed": True, "tags": []},
        {"case_id": "a", "score": 1.01, "passed": True, "tags": []},
        {"case_id": "a", "score": 0.75, "passed": True, "tags": ("critical",)},
    ],
)
def test_gate_rejects_malformed_case_evidence_with_json_safe_result(candidate_case: dict[str, Any]):
    module = load_pipeline_module()
    result = module.apply_gate(
        candidate_id="malformed_case_evidence",
        baseline_val=_gate_summary(0.25, [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}]),
        candidate_val=_gate_summary(0.75, [candidate_case]),
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    json.dumps(result, allow_nan=False)


def test_gate_rejects_regression_even_when_minimum_delta_is_negative():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.75,
        [{"case_id": "a", "score": 0.75, "passed": True, "tags": []}],
    )
    candidate = _gate_summary(
        0.5,
        [{"case_id": "a", "score": 0.5, "passed": True, "tags": []}],
    )

    result = module.apply_gate(
        candidate_id="regression",
        baseline_val=baseline,
        candidate_val=candidate,
        gate_config={
            "min_validation_delta": -0.5,
            "allow_critical_regression": True,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert "did not improve" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    ("baseline_tags", "candidate_tags"),
    [
        (["critical", "validation"], ["validation"]),
        (["validation"], ["validation", "critical"]),
        (["validation", "refund"], ["validation", "faq"]),
    ],
)
def test_gate_rejects_tag_mismatch_and_uses_baseline_for_critical_status(
    baseline_tags: list[str],
    candidate_tags: list[str],
):
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.5,
        [{"case_id": "a", "score": 1.0, "passed": True, "tags": baseline_tags}],
    )
    candidate = _gate_summary(
        0.75,
        [{"case_id": "a", "score": 0.5, "passed": True, "tags": candidate_tags}],
    )

    result = module.apply_gate(
        candidate_id="retagged",
        baseline_val=baseline,
        candidate_val=candidate,
        gate_config={
            "min_validation_delta": 0.0,
            "allow_new_hard_fails": True,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    assert "tag mismatch" in " ".join(result["reasons"])
    if "critical" in baseline_tags:
        assert result["critical_regression_ids"] == ["a"]


@pytest.mark.parametrize(
    "candidate_cases",
    [
        None,
        ["not a case"],
        [
            {"case_id": "a", "score": 1.0, "passed": True, "tags": []},
            {"case_id": "a", "score": 1.0, "passed": True, "tags": []},
        ],
        [{"case_id": "a", "score": 1.0, "passed": "true", "tags": []}],
        [{"case_id": "a", "score": 1.0, "passed": True, "tags": "critical"}],
        [{"case_id": "a", "score": float("nan"), "passed": True, "tags": []}],
    ],
)
def test_gate_rejects_malformed_case_sets_without_raising(candidate_cases: Any):
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
    )
    candidate = _gate_summary(0.75, candidate_cases)

    result = module.apply_gate(
        candidate_id="malformed_cases",
        baseline_val=baseline,
        candidate_val=candidate,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )

    assert result["accepted"] is False
    json.dumps(result, allow_nan=False)


def test_required_metric_passed_must_be_true_and_case_deltas_are_total():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [
            {"case_id": "a", "score": 0.25, "passed": False, "tags": []},
            {"case_id": "b", "score": 0.25, "passed": True, "tags": []},
        ],
    )
    candidate = _gate_summary(
        0.75,
        [
            {"case_id": "b", "score": 0.75, "passed": True, "tags": []},
            {"case_id": "c", "score": 1.0, "passed": True, "tags": []},
        ],
    )
    candidate["metrics"][ROUTE_TOOL_ARGS_METRIC]["passed"] = "false"

    gate = module.apply_gate(
        candidate_id="string_metric_status",
        baseline_val=baseline,
        candidate_val=candidate,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert gate["accepted"] is False

    deltas = module.build_case_deltas(baseline, candidate)
    assert [item["case_id"] for item in deltas] == ["a", "b", "c"]
    assert deltas[0]["root_cause"] == "missing_candidate"
    assert deltas[0]["candidate_score"] is None
    assert deltas[2]["root_cause"] == "unexpected_candidate"
    assert deltas[2]["baseline_score"] is None
    json.dumps(deltas, allow_nan=False)


def test_case_deltas_classify_pass_fail_and_score_transitions():
    module = load_pipeline_module()
    baseline = {
        "case_results": [
            {"case_id": "new_pass", "score": 0.0, "passed": False, "actual_text": "b1"},
            {"case_id": "new_fail", "score": 1.0, "passed": True, "actual_text": "b2"},
            {"case_id": "up", "score": 0.4, "passed": True, "actual_text": "b3"},
            {"case_id": "down", "score": 0.8, "passed": True, "actual_text": "b4"},
            {"case_id": "same", "score": 1.0, "passed": True, "actual_text": "b5"},
        ]
    }
    candidate = {
        "case_results": [
            {"case_id": "new_pass", "score": 1.0, "passed": True, "actual_text": "c1", "root_cause": "", "reasons": []},
            {
                "case_id": "new_fail",
                "score": 0.0,
                "passed": False,
                "actual_text": "c2",
                "root_cause": "format_error",
                "reasons": ["bad"],
            },
            {"case_id": "up", "score": 0.6, "passed": True, "actual_text": "c3", "root_cause": "", "reasons": []},
            {"case_id": "down", "score": 0.6, "passed": True, "actual_text": "c4", "root_cause": "", "reasons": []},
            {"case_id": "same", "score": 1.0, "passed": True, "actual_text": "c5", "root_cause": "", "reasons": []},
        ]
    }

    by_id = {item["case_id"]: item for item in module.build_case_deltas(baseline, candidate)}

    assert by_id["new_pass"]["change_type"] == "new_pass"
    assert by_id["new_fail"]["change_type"] == "new_fail"
    assert by_id["up"]["change_type"] == "score_improved"
    assert by_id["down"]["change_type"] == "score_regressed"
    assert by_id["same"]["change_type"] == "unchanged"
    assert by_id["new_fail"]["baseline_passed"] is True
    assert by_id["new_fail"]["candidate_passed"] is False


def test_summary_omits_thoughts_and_redacts_provider_credentials_from_report_text():
    module = load_pipeline_module()
    payload = load_report(EXAMPLE_DIR / "val.evalset.json")
    case = payload["eval_cases"][0]
    visible_final = '{"route":"faq","tool":{"name":"none","arguments":{}}}'
    actual_invocation = SimpleNamespace(
        final_response={
            "parts": [
                {"text": "internal chain of thought", "thought": True},
                {"text": visible_final, "thought": False},
            ]
        }
    )
    expected_invocation = SimpleNamespace(final_response={"parts": [{"text": visible_final, "thought": False}]})
    secret = "ASIA_SECRET_SESSION_TOKEN"
    run = SimpleNamespace(
        eval_metric_result_per_invocation=[
            SimpleNamespace(
                actual_invocation=actual_invocation,
                expected_invocation=expected_invocation,
            )
        ],
        final_eval_status="failed",
        error_message=f"request failed: X-Amz-Security-Token: {secret}; retry later",
        overall_eval_metric_results=[
            SimpleNamespace(
                metric_name="provider_metric",
                score=0.0,
                eval_status="failed",
                details=SimpleNamespace(reason=f"provider headers: X-Amz-Security-Token: {secret}"),
                threshold=1.0,
            ),
            SimpleNamespace(
                metric_name="normal_metric",
                score=1.0,
                eval_status="passed",
                details=SimpleNamespace(reason="normal evaluator explanation"),
                threshold=1.0,
            ),
        ],
    )
    result = SimpleNamespace(
        results_by_eval_set_id={
            payload["eval_set_id"]: SimpleNamespace(
                eval_results_by_eval_id={case["eval_id"]: [run]},
            )
        }
    )

    summary = module.summarize_evaluate_result(result, payload)
    case_result = summary["case_results"][0]

    assert case_result["actual_text"] == visible_final
    assert case_result["key_trace"]["actual_final_response"] == visible_final
    assert "internal chain of thought" not in json.dumps(case_result)
    assert "request failed" in case_result["key_trace"]["error_message"]
    assert case_result["metrics"]["normal_metric"]["reason"] == "normal evaluator explanation"
    serialized_summary = json.dumps(summary)
    assert "X-Amz-Security-Token" not in serialized_summary
    assert secret not in serialized_summary


@pytest.mark.parametrize(
    "sensitive_text",
    [
        "Authorization: Bearer secret-token",
        "X-Api-Key: api-key-value",
        "access_token=access-token-value",
        "session token=session-token-value",
        "security-token=security-token-value",
        "client_secret=client-secret-value",
        "db_credential=credential-value",
        "Set-Cookie: session=cookie-value",
        "X-Custom-Token: custom-token-value",
    ],
)
def test_sanitize_report_text_redacts_semantic_credential_markers(sensitive_text: str):
    module = load_pipeline_module()

    assert module.sanitize_report_text(f"upstream failed: {sensitive_text}") == (
        "upstream failed: provider details redacted"
    )


@pytest.mark.parametrize(
    "sensitive_text",
    [
        "https://user:password@provider.example/v1?api_key=fake-secret",
        "http://provider.example/v1/models",
        "sk-fake0123456789abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_sanitize_report_text_redacts_urls_and_standalone_provider_keys(sensitive_text: str):
    module = load_pipeline_module()
    sanitized = module.sanitize_report_text(f"request failed while calling {sensitive_text}")

    assert sanitized == "request failed while calling: provider details redacted"
    assert sensitive_text not in sanitized


def test_sanitize_report_text_redacts_exact_configured_provider_values(monkeypatch: pytest.MonkeyPatch):
    configured_key = "configured-fake-key-value"
    configured_url = "provider.internal.example/v1/fake"
    monkeypatch.setenv("TRPC_AGENT_API_KEY", configured_key)
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", configured_url)
    module = load_pipeline_module()

    for configured_value in (configured_key, configured_url):
        sanitized = module.sanitize_report_text(f"connection failed at {configured_value}")
        assert sanitized == "connection failed at: provider details redacted"
        assert configured_value not in sanitized


def test_sanitize_report_text_preserves_ordinary_error_context():
    module = load_pipeline_module()
    message = "optimizer rejected round 2 because validation score decreased"
    assert module.sanitize_report_text(message) == message


def test_no_run_key_trace_uses_safe_shape_and_omits_thought_content():
    module = load_pipeline_module()
    payload = load_report(EXAMPLE_DIR / "val.evalset.json")
    case = payload["eval_cases"][0]
    case["conversation"][0]["final_response"] = {
        "parts": [
            {"text": "internal expected thought", "thought": True},
            {"text": "visible expected final", "thought": False},
        ]
    }
    result = SimpleNamespace(
        results_by_eval_set_id={
            payload["eval_set_id"]: SimpleNamespace(
                eval_results_by_eval_id={case["eval_id"]: []},
            )
        }
    )

    summary = module.summarize_evaluate_result(result, payload)
    key_trace = summary["case_results"][0]["key_trace"]

    assert key_trace == {
        "invocation_id": str(case["conversation"][0]["invocation_id"]),
        "actual_final_response": "",
        "expected_final_response": "visible expected final",
        "error_message": "AgentEvaluator returned no run for case",
    }
    assert "thought" not in json.dumps(key_trace)


def test_build_candidate_report_rejects_case_set_mismatch():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
    )
    validation = _gate_summary(
        0.75,
        [{"case_id": "b", "score": 0.75, "passed": True, "tags": []}],
    )
    report = module.build_candidate_report(
        candidate_id="mismatched",
        fixture={},
        train=baseline,
        optimizer_dev=baseline,
        validation=validation,
        baseline_train=baseline,
        baseline_optimizer_dev=baseline,
        baseline_val=baseline,
        gate_config={},
        duration_seconds=1.0,
        cost_usd=0.0,
        seed=7,
        optimizer_config=EXAMPLE_DIR / "optimizer.json",
    )

    assert report["gate"]["accepted"] is False
    assert report["gate"]["missing_case_ids"] == ["a"]
    assert report["gate"]["unexpected_case_ids"] == ["b"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "candidate_cases",
    [
        None,
        ["not a case"],
        [{"case_id": "a", "score": float("nan"), "passed": True, "tags": []}],
        [{"case_id": "a", "score": 0.75, "passed": "false", "tags": "critical"}],
    ],
)
def test_build_candidate_report_is_total_for_malformed_validation_cases(candidate_cases: Any):
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
    )
    validation = _gate_summary(0.75, candidate_cases)
    report = module.build_candidate_report(
        candidate_id="malformed_report",
        fixture={},
        train=baseline,
        optimizer_dev=baseline,
        validation=validation,
        baseline_train=baseline,
        baseline_optimizer_dev=baseline,
        baseline_val=baseline,
        gate_config={},
        duration_seconds=1.0,
        cost_usd=0.0,
        seed=7,
        optimizer_config=EXAMPLE_DIR / "optimizer.json",
    )

    assert report["gate"]["accepted"] is False
    json.dumps(report, allow_nan=False)


def test_build_candidate_report_sanitizes_nonfinite_case_reasons():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [{"case_id": "a", "score": 0.25, "passed": True, "tags": []}],
    )
    validation = _gate_summary(
        0.75,
        [
            {
                "case_id": "a",
                "score": 0.0,
                "passed": False,
                "tags": [],
                "reasons": [float("nan")],
            }
        ],
    )

    report = module.build_candidate_report(
        candidate_id="nonfinite_reasons",
        fixture={},
        train=baseline,
        optimizer_dev=baseline,
        validation=validation,
        baseline_train=baseline,
        baseline_optimizer_dev=baseline,
        baseline_val=baseline,
        gate_config={},
        duration_seconds=1.0,
        cost_usd=0.0,
        seed=7,
        optimizer_config=EXAMPLE_DIR / "optimizer.json",
    )

    assert report["gate"]["accepted"] is False
    json.dumps(report, allow_nan=False)


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_evaluate_result(eval_set_path: Path, *, score: float = 1.0, passed: bool = True):
    from trpc_agent_sdk.evaluation import EvalCaseResult
    from trpc_agent_sdk.evaluation import EvalMetricResult
    from trpc_agent_sdk.evaluation import EvalSetAggregateResult
    from trpc_agent_sdk.evaluation import EvalStatus
    from trpc_agent_sdk.evaluation import EvaluateResult

    payload = load_report(eval_set_path)
    status = EvalStatus.PASSED if passed else EvalStatus.FAILED
    case_results = {}
    for case in payload["eval_cases"]:
        metric = EvalMetricResult(
            metric_name=ROUTE_TOOL_ARGS_METRIC,
            threshold=1.0,
            criterion={"final_response": {"json": {"match": "exact"}}},
            score=score,
            eval_status=status,
        )
        case_results[case["eval_id"]] = [
            EvalCaseResult(
                eval_set_id=payload["eval_set_id"],
                eval_id=case["eval_id"],
                run_id=1,
                final_eval_status=status,
                overall_eval_metric_results=[metric],
                eval_metric_result_per_invocation=[],
                session_id="fake-session",
            )
        ]
    return EvaluateResult(
        results_by_eval_set_id={
            payload["eval_set_id"]: EvalSetAggregateResult(
                eval_results_by_eval_id=case_results,
                num_runs=1,
            )
        }
    )


def patch_agent_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    *,
    score: float = 1.0,
    passed: bool = True,
) -> list[Path]:
    calls: list[Path] = []

    class FakeExecuter:
        def __init__(self, eval_set_path: str) -> None:
            self.eval_set_path = Path(eval_set_path)
            self.result = None

        async def evaluate(self) -> None:
            calls.append(self.eval_set_path)
            self.result = make_evaluate_result(self.eval_set_path, score=score, passed=passed)

        def get_result(self):
            return self.result

    def fake_get_executer(eval_dataset_file_path_or_dir: str, **_: Any) -> FakeExecuter:
        return FakeExecuter(eval_dataset_file_path_or_dir)

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentEvaluator, "get_executer", staticmethod(fake_get_executer))
    return calls


def _copy_public_evalsets(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = []
    for name in ("train.evalset.json", "optimizer_dev.evalset.json", "val.evalset.json"):
        target = tmp_path / name
        shutil.copyfile(EXAMPLE_DIR / name, target)
        paths.append(target)
    return tuple(paths)  # type: ignore[return-value]


def test_validate_inputs_accepts_distinct_public_evalset_copies(tmp_path: Path):
    module = load_pipeline_module()
    module.validate_inputs(*_copy_public_evalsets(tmp_path))


def test_validate_inputs_rejects_byte_identical_copies(tmp_path: Path):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    shutil.copyfile(train, optimizer_dev)

    with pytest.raises(ValueError, match="byte-identical"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_rejects_hardlinks_when_supported(tmp_path: Path):
    module = load_pipeline_module()
    train, _, validation = _copy_public_evalsets(tmp_path)
    optimizer_dev = tmp_path / "optimizer_dev_hardlink.evalset.json"
    try:
        os.link(train, optimizer_dev)
    except OSError as error:
        pytest.skip(f"hardlinks unavailable: {error}")

    with pytest.raises(ValueError, match="same file"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_rejects_same_target_symlinks_when_supported(tmp_path: Path):
    module = load_pipeline_module()
    train, _, validation = _copy_public_evalsets(tmp_path)
    optimizer_dev = tmp_path / "optimizer_dev_symlink.evalset.json"
    try:
        optimizer_dev.symlink_to(train)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="same file"):
        module.validate_inputs(train, optimizer_dev, validation)


@pytest.mark.parametrize("overlap", ["id", "input", "gold"])
def test_validate_inputs_rejects_pairwise_semantic_overlap(tmp_path: Path, overlap: str):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = json.loads(train.read_text(encoding="utf-8"))
    dev_payload = json.loads(optimizer_dev.read_text(encoding="utf-8"))
    train_case = train_payload["eval_cases"][0]
    dev_case = dev_payload["eval_cases"][0]
    if overlap == "id":
        dev_case["eval_id"] = train_case["eval_id"]
    elif overlap == "input":
        source = train_case["conversation"][0]["user_content"]["parts"][0]["text"]
        dev_case["conversation"][0]["user_content"]["parts"][0]["text"] = f"  {source.upper()}  "
    else:
        dev_case["conversation"][0]["final_response"]["parts"] = copy.deepcopy(
            train_case["conversation"][0]["final_response"]["parts"]
        )
        dev_case["conversation"][0]["final_response"]["role"] = "different-metadata"
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")

    with pytest.raises(ValueError, match=overlap):
        module.validate_inputs(train, optimizer_dev, validation)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"eval_cases": []},
        {"eval_cases": "invalid"},
        {"eval_cases": [{}]},
        {"eval_cases": [{"eval_id": "x", "conversation": []}]},
    ],
)
def test_validate_inputs_rejects_invalid_evalset_shapes(tmp_path: Path, payload: Any):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    optimizer_dev.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="optimizer_dev evalset"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_rejects_duplicate_eval_ids_within_a_split(tmp_path: Path):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    payload = load_report(optimizer_dev)
    payload["eval_cases"][1]["eval_id"] = payload["eval_cases"][0]["eval_id"]
    optimizer_dev.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate eval_id"):
        module.validate_inputs(train, optimizer_dev, validation)


@pytest.mark.parametrize("variant", ["thought_part", "split_visible_parts"])
def test_validate_inputs_canonicalizes_gold_with_final_text_from_content(
    tmp_path: Path,
    variant: str,
):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = load_report(train)
    dev_payload = load_report(optimizer_dev)
    train_response = train_payload["eval_cases"][0]["conversation"][0]["final_response"]
    dev_response = dev_payload["eval_cases"][0]["conversation"][0]["final_response"]
    if variant == "thought_part":
        dev_response["parts"] = copy.deepcopy(train_response["parts"])
        dev_response["parts"].append({"text": "private thought", "thought": True})
    else:
        train_response["parts"] = [{"text": "visible one\nvisible two"}]
        dev_response["parts"] = [{"text": "visible one"}, {"text": "visible two"}]
    train.write_text(json.dumps(train_payload), encoding="utf-8")
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gold"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_checks_every_conversation_turn(tmp_path: Path):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = load_report(train)
    dev_payload = load_report(optimizer_dev)
    shared_turn = {
        "invocation_id": "shared_second_turn",
        "user_content": {"parts": [{"text": "shared optimizer-visible turn"}], "role": "user"},
        "final_response": {"parts": [{"text": '{"shared": true}'}], "role": "model"},
    }
    train_payload["eval_cases"][0]["conversation"].append(copy.deepcopy(shared_turn))
    dev_payload["eval_cases"][0]["conversation"].append(copy.deepcopy(shared_turn))
    train.write_text(json.dumps(train_payload), encoding="utf-8")
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="input|gold"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_canonicalizes_structurally_equal_json_gold(tmp_path: Path):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = load_report(train)
    dev_payload = load_report(optimizer_dev)
    train_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [
        {"text": '{"route":"shared","tool":{"name":"x","arguments":{"a":1,"b":2}}}'}
    ]
    dev_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [
        {"text": '{ "tool": { "arguments": { "b": 2, "a": 1 }, "name": "x" }, "route": "shared" }'}
    ]
    train.write_text(json.dumps(train_payload), encoding="utf-8")
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gold"):
        module.validate_inputs(train, optimizer_dev, validation)


def test_validate_inputs_uses_json_criterion_numeric_tolerance(tmp_path: Path):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = load_report(train)
    dev_payload = load_report(optimizer_dev)
    train_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [{"text": '{"x":1.0}'}]
    dev_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [{"text": '{"x":1.0000005}'}]
    train.write_text(json.dumps(train_payload), encoding="utf-8")
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gold"):
        module.validate_inputs(train, optimizer_dev, validation)


@pytest.mark.parametrize("json_alias", ["json", "json_strategy", "jsonStrategy"])
def test_validate_inputs_uses_configured_json_criterion_tolerance(tmp_path: Path, json_alias: str):
    module = load_pipeline_module()
    train, optimizer_dev, validation = _copy_public_evalsets(tmp_path)
    train_payload = load_report(train)
    dev_payload = load_report(optimizer_dev)
    train_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [{"text": '{"x":1.0}'}]
    dev_payload["eval_cases"][0]["conversation"][0]["final_response"]["parts"] = [{"text": '{"x":1.05}'}]
    train.write_text(json.dumps(train_payload), encoding="utf-8")
    optimizer_dev.write_text(json.dumps(dev_payload), encoding="utf-8")
    metrics_config = {
        "metrics": [
            {
                "metric_name": "json_match",
                "threshold": 1.0,
                "criterion": {
                    "final_response": {
                        json_alias: {
                            "match": "exact",
                            "number_tolerance": 0.1,
                        }
                    }
                },
            }
        ],
        "num_runs": 1,
    }

    with pytest.raises(ValueError, match="gold"):
        module.validate_inputs(
            train,
            optimizer_dev,
            validation,
            metrics_config=metrics_config,
        )


def test_directory_layout_and_assets_exist():
    expected = {
        "README.md",
        "run_pipeline.py",
        "optimizer.json",
        "optimization_report.schema.json",
        "train.evalset.json",
        "optimizer_dev.evalset.json",
        "val.evalset.json",
        "agent/__init__.py",
        "agent/agent.py",
        "agent/config.py",
        "agent/prompts/system.md",
        "agent/prompts/router.md",
        "fixtures/fake_outputs.json",
        "fixtures/offline_metrics.sample.json",
        "fixtures/trace_outputs.json",
        "fixtures/optimization_report.sample.json",
    }
    for rel in expected:
        assert (EXAMPLE_DIR / rel).exists(), f"missing example asset: {rel}"


def test_evalsets_and_optimizer_config_are_schema_loadable():
    from trpc_agent_sdk.evaluation import EvalSet
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    train = EvalSet.model_validate_json((EXAMPLE_DIR / "train.evalset.json").read_text(encoding="utf-8"))
    optimizer_dev = EvalSet.model_validate_json(
        (EXAMPLE_DIR / "optimizer_dev.evalset.json").read_text(encoding="utf-8")
    )
    val = EvalSet.model_validate_json((EXAMPLE_DIR / "val.evalset.json").read_text(encoding="utf-8"))
    assert len(train.eval_cases) == 3
    assert len(optimizer_dev.eval_cases) >= 1
    assert len(val.eval_cases) == 3
    assert {case.eval_id for case in train.eval_cases} == {
        "train_refund_001",
        "train_manual_002",
        "train_faq_003",
    }
    assert "val_shipping_delay_103" in {case.eval_id for case in val.eval_cases}
    assert {case.eval_id for case in optimizer_dev.eval_cases}.isdisjoint({case.eval_id for case in val.eval_cases})

    def evidence_sets(evalset: Any) -> tuple[set[str], set[str], set[str]]:
        ids = {case.eval_id for case in evalset.eval_cases}
        users = {
            "".join(part.text or "" for part in case.conversation[0].user_content.parts) for case in evalset.eval_cases
        }
        gold = {
            "".join(part.text or "" for part in case.conversation[0].final_response.parts)
            for case in evalset.eval_cases
        }
        return ids, users, gold

    train_evidence = evidence_sets(train)
    optimizer_dev_evidence = evidence_sets(optimizer_dev)
    validation_evidence = evidence_sets(val)
    assert len(optimizer_dev.eval_cases) == 3
    for optimizer_values, train_values, validation_values in zip(
        optimizer_dev_evidence,
        train_evidence,
        validation_evidence,
    ):
        assert optimizer_values.isdisjoint(train_values)
        assert optimizer_values.isdisjoint(validation_values)

    config = load_optimize_config(str(EXAMPLE_DIR / "optimizer.json"))
    assert config.optimize.algorithm.name == "gepa_reflective"
    assert {metric.metric_name for metric in config.evaluate.get_eval_metrics()} == {
        ROUTE_TOOL_ARGS_METRIC,
        "llm_rubric_response",
    }


def test_pipeline_module_exposes_testable_contracts():
    module = load_pipeline_module()
    assert inspect.iscoroutinefunction(module.amain)
    assert inspect.iscoroutinefunction(module.run_fake_or_trace)
    assert inspect.iscoroutinefunction(module.run_online)
    assert callable(module.gate_candidate)
    assert callable(module.attribution_for)


def test_readme_includes_design_notes_and_sample_report_shape():
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    assert "## Design Notes" in readme
    assert "fixtures/optimization_report.sample.json" in readme
    assert "candidate_local_patch" in readme
    assert "candidate_overfit" in readme
    assert "not globally suppressed" in readme
    assert "pipeline awaits `Runner.close()`" in readme
    assert "upstream OpenAI/httpx" in readme
    assert "warnings remain observable" in readme
    assert "cleanup warnings are cleanup defects" not in readme
    assert "`tests/conftest.py` ignores" not in readme
    assert "may fail during collection" in readme

    sample = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    required = {
        "run_id",
        "mode",
        "seed",
        "baseline",
        "candidates",
        "delta",
        "gate_decision",
        "failure_attribution",
        "cost",
        "duration_seconds",
        "artifacts",
    }
    assert required <= set(sample)
    assert sample["gate_decision"]["winner"] == "candidate_local_patch"
    assert {candidate["id"] for candidate in sample["candidates"]} == {
        "candidate_local_patch",
        "candidate_noop",
        "candidate_overfit",
    }


def test_public_candidates_cover_success_noop_and_aggregate_regression(tmp_path: Path):
    module = load_pipeline_module()
    report = module.make_report(
        mode="fake",
        run_id="public_scenarios",
        run_dir=tmp_path,
        seed=7,
        started=module.time.perf_counter(),
    )
    candidates = {item["id"]: item for item in report["candidates"]}
    assert candidates["candidate_local_patch"]["gate"]["accepted"] is True
    assert candidates["candidate_noop"]["delta"]["validation_score"] == 0
    assert candidates["candidate_noop"]["gate"]["accepted"] is False
    overfit = candidates["candidate_overfit"]
    assert overfit["delta"]["train_score"] > 0
    assert overfit["delta"]["validation_score"] < 0
    assert overfit["gate"]["accepted"] is False


def test_design_notes_length_is_within_issue_limit():
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Design Notes", 1)[1].split("## Verification", 1)[0]
    non_whitespace = len("".join(section.split()))
    assert 300 <= non_whitespace <= 500


def test_sample_report_is_deterministic_and_has_no_temporary_paths():
    sample = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    assert sample["run_id"] == "sample"
    assert "known_warning_filters" not in sample["environment_snapshot"]
    assert "SSEDecoder._aiter_chunks close RuntimeWarning" not in json.dumps(sample)
    stable_environment = {
        "git_commit": "sample",
        "git_dirty": False,
        "python_version": "3.x",
        "sdk_version": "sample",
        "model_name": None,
        "base_url_host": None,
        "command": (
            "python examples/optimization/eval_optimize_loop/run_pipeline.py "
            "--mode fake --output-dir runs --run-id sample"
        ),
        "config_path": "examples/optimization/eval_optimize_loop/optimizer.json",
    }
    for key, value in stable_environment.items():
        assert sample["environment_snapshot"][key] == value

    durations: list[Any] = []
    strings: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "duration_seconds":
                    durations.append(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            strings.append(value)

    collect(sample)
    assert durations
    assert all(value == 0.0 for value in durations)
    assert all("\\" not in value for value in strings)
    assert all(not (len(value) >= 3 and value[1:3] == ":/") for value in strings)
    normalized_bytes = (
        (EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json").read_bytes().replace(b"\r\n", b"\n")
    )
    assert normalized_bytes.endswith(b"\n")
    assert not normalized_bytes.endswith(b"\n\n")


def test_sample_report_validates_against_schema_and_required_fields_are_enforced():
    module = load_pipeline_module()
    sample = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")

    module.validate_report_schema(sample)

    broken = dict(sample)
    broken.pop("environment_snapshot", None)
    with pytest.raises(ValidationError):
        module.validate_report_schema(broken)


@pytest.mark.parametrize(
    ("mutation_path", "replacement"),
    [
        (("candidates", 0, "delta"), {}),
        (("baseline", "validation", "case_results"), [{}]),
        (("candidates", 0, "case_deltas"), [{}]),
        (("failure_attribution", "coverage"), 9.0),
        (("candidates", 0, "audit"), {}),
    ],
)
def test_report_schema_rejects_incomplete_core_objects(
    mutation_path: tuple[Any, ...],
    replacement: Any,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    target: Any = report
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = replacement

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("mutation_path", "value"),
    [
        (("baseline", "validation", "case_results", 0, "score"), float("nan")),
        (("candidates", 0, "audit", "duration_seconds"), float("inf")),
        (("duration_seconds",), float("-inf")),
    ],
)
def test_report_schema_rejects_nonfinite_numbers(
    mutation_path: tuple[Any, ...],
    value: float,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    target: Any = report
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = value

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


def test_report_schema_requires_numeric_delta_for_accepted_gate():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    accepted_candidate = next(candidate for candidate in report["candidates"] if candidate["gate"]["accepted"])
    accepted_candidate["gate"]["validation_delta"] = None

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)

    accepted_candidate["gate"]["validation_delta"] = accepted_candidate["delta"]["validation_score"]
    rejected_candidate = next(candidate for candidate in report["candidates"] if not candidate["gate"]["accepted"])
    rejected_candidate["gate"]["validation_delta"] = None
    with pytest.raises(ValidationError, match="gate validation_delta"):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    "contradiction",
    [
        "accepted_without_winner",
        "accepted_unknown_winner",
        "rejected_with_winner",
        "rejected_with_accepted_candidate",
        "accepted_nonpositive_delta",
        "candidate_gate_id_mismatch",
        "baseline_validation_alias_mismatch",
        "candidate_validation_alias_mismatch",
        "winner_delta_mismatch",
        "winner_reasons_mismatch",
        "rejected_nonzero_delta",
        "top_level_model_call_sum_mismatch",
        "optimizer_model_call_sum_mismatch",
        "final_model_call_sum_mismatch",
        "token_total_mismatch",
    ],
)
def test_report_semantic_validation_rejects_contradictions(contradiction: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    winner = next(candidate for candidate in report["candidates"] if candidate["gate"]["accepted"])
    if contradiction == "accepted_without_winner":
        report["gate_decision"]["winner"] = None
    elif contradiction == "accepted_unknown_winner":
        report["gate_decision"]["winner"] = "missing_candidate"
    elif contradiction == "rejected_with_winner":
        report["gate_decision"]["accepted"] = False
    elif contradiction == "rejected_with_accepted_candidate":
        report["gate_decision"] = {"accepted": False, "winner": None, "reasons": ["rejected"]}
        report["delta"] = {"train_score": 0.0, "optimizer_dev_score": 0.0, "validation_score": 0.0}
    elif contradiction == "accepted_nonpositive_delta":
        winner["gate"]["validation_delta"] = 0.0
    elif contradiction == "candidate_gate_id_mismatch":
        winner["gate"]["candidate_id"] = "different_candidate"
    elif contradiction == "baseline_validation_alias_mismatch":
        report["baseline"]["validation"] = copy.deepcopy(report["baseline"]["validation"])
        report["baseline"]["validation"]["score"] = 0.0
    elif contradiction == "candidate_validation_alias_mismatch":
        winner["validation"] = copy.deepcopy(winner["validation"])
        winner["validation"]["score"] = 0.0
    elif contradiction == "winner_delta_mismatch":
        report["delta"]["validation_score"] = 0.25
    elif contradiction == "winner_reasons_mismatch":
        report["gate_decision"]["reasons"] = ["different reason"]
    elif contradiction == "rejected_nonzero_delta":
        report["gate_decision"] = {"accepted": False, "winner": None, "reasons": ["rejected"]}
        for candidate in report["candidates"]:
            candidate["gate"]["accepted"] = False
        report["delta"]["validation_score"] = 0.25
    elif contradiction == "top_level_model_call_sum_mismatch":
        report["cost"]["model_calls"] = 1
    elif contradiction == "optimizer_model_call_sum_mismatch":
        report["cost"]["optimizer"]["model_calls"] = 1
        report["cost"]["model_calls"] = 1
    elif contradiction == "final_model_call_sum_mismatch":
        report["cost"]["final_revalidation"]["model_calls"] = 1
        report["cost"]["model_calls"] = 1
    else:
        report["cost"]["token_usage"]["total"] = 1

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


def test_report_semantics_reject_duplicate_candidate_ids():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    duplicate_id = report["candidates"][0]["id"]
    report["candidates"][1]["id"] = duplicate_id
    report["candidates"][1]["gate"]["candidate_id"] = duplicate_id

    with pytest.raises(ValidationError, match="duplicate candidate"):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("owner", "summary_name"),
    [
        ("baseline", "train"),
        ("baseline", "optimizer_dev"),
        ("baseline", "validation"),
        ("candidate", "train"),
        ("candidate", "optimizer_dev"),
        ("candidate", "validation"),
    ],
)
def test_report_semantics_reject_duplicate_case_ids_in_every_summary(owner: str, summary_name: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    container = report["baseline"] if owner == "baseline" else report["candidates"][0]
    summary = container[summary_name]
    summary["case_results"].append(copy.deepcopy(summary["case_results"][0]))
    if summary_name == "validation":
        container["final_validation"] = copy.deepcopy(summary)

    with pytest.raises(ValidationError, match="duplicate case_id"):
        module.validate_report_schema(report)


@pytest.mark.parametrize("delta_name", ["train_score", "optimizer_dev_score", "validation_score"])
def test_report_semantics_recompute_candidate_deltas(delta_name: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["candidates"][1]["delta"][delta_name] = 0.123456

    with pytest.raises(ValidationError, match="candidate delta"):
        module.validate_report_schema(report)


def test_report_semantics_require_gate_delta_to_match_candidate_delta():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["candidates"][1]["gate"]["validation_delta"] = 0.123456

    with pytest.raises(ValidationError, match="gate validation_delta"):
        module.validate_report_schema(report)


def test_report_semantics_recompute_case_deltas():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["candidates"][0]["case_deltas"][0]["change_type"] = "score_improved"

    with pytest.raises(ValidationError, match="case_deltas"):
        module.validate_report_schema(report)


@pytest.mark.parametrize("field", ["pass_rate", "failed_case_ids", "source"])
def test_report_semantics_recompute_evaluation_summary_evidence(field: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    summary = report["baseline"]["train"]
    if field == "pass_rate":
        summary[field] = 0.123456
    elif field == "failed_case_ids":
        summary[field] = [summary["case_results"][0]["case_id"]]
    else:
        summary[field] = "fixture"

    with pytest.raises(ValidationError, match="summary"):
        module.validate_report_schema(report)


def test_report_semantics_reject_case_level_aggregate_forgery():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    validation = copy.deepcopy(candidate["validation"])
    validation["case_results"][0]["score"] = 0.5
    candidate["validation"] = validation
    candidate["final_validation"] = copy.deepcopy(validation)
    candidate["case_deltas"] = module.build_case_deltas(report["baseline"]["validation"], validation)

    with pytest.raises(ValidationError, match="summary score"):
        module.validate_report_schema(report)


def test_report_semantics_recompute_critical_regression_gate_evidence():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    validation = copy.deepcopy(candidate["validation"])
    critical_case = next(case for case in validation["case_results"] if "critical" in case["tags"])
    critical_case["score"] = 0.5
    validation["score"] = round(
        sum(case["score"] for case in validation["case_results"]) / len(validation["case_results"]),
        6,
    )
    candidate["validation"] = validation
    candidate["final_validation"] = copy.deepcopy(validation)
    candidate["delta"]["validation_score"] = module._score_delta(
        validation["score"],
        report["baseline"]["validation"]["score"],
    )
    candidate["gate"]["validation_delta"] = candidate["delta"]["validation_score"]
    candidate["case_deltas"] = module.build_case_deltas(report["baseline"]["validation"], validation)
    report["delta"] = copy.deepcopy(candidate["delta"])

    with pytest.raises(ValidationError, match="gate evidence|primary metric|metric-derived score"):
        module.validate_report_schema(report)


def test_report_semantics_reject_required_metric_status_forgery():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    metric = candidate["validation"]["metrics"][ROUTE_TOOL_ARGS_METRIC]
    metric["score"] = 0.0
    candidate["final_validation"] = copy.deepcopy(candidate["validation"])

    with pytest.raises(ValidationError, match="metric"):
        module.validate_report_schema(report)


def test_report_semantics_recompute_aggregate_metrics_from_case_metrics():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    metric = candidate["validation"]["metrics"]["llm_rubric_response"]
    metric["score"] = 0.75
    metric["threshold"] = 0.5
    candidate["final_validation"] = copy.deepcopy(candidate["validation"])

    with pytest.raises(ValidationError, match="aggregate metric"):
        module.validate_report_schema(report)


@pytest.mark.parametrize("mutation", ["omitted", "contradictory"])
def test_report_semantics_require_complete_consistent_case_metrics(mutation: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    case_metric = candidate["validation"]["case_results"][0]["metrics"][ROUTE_TOOL_ARGS_METRIC]
    if mutation == "omitted":
        del candidate["validation"]["case_results"][0]["metrics"][ROUTE_TOOL_ARGS_METRIC]
    else:
        case_metric.update({"passed": False, "status": "failed"})
    candidate["final_validation"] = copy.deepcopy(candidate["validation"])

    with pytest.raises(ValidationError, match="case metric|metric coverage"):
        module.validate_report_schema(report)


def test_report_semantics_recompute_case_score_when_primary_metric_is_null():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    case = candidate["validation"]["case_results"][0]
    primary = case["metrics"][ROUTE_TOOL_ARGS_METRIC]
    primary.update({"score": None, "passed": False, "status": "failed"})
    rubric = case["metrics"]["llm_rubric_response"]
    rubric.update({"score": 0.5, "threshold": 0.66, "passed": False, "status": "failed"})
    case.update(
        {
            "score": 1.0,
            "passed": False,
            "root_cause": "rubric_failed",
            "reasons": ["rubric metric failed"],
        }
    )
    candidate["validation"]["pass_rate"] = 0.666667
    candidate["validation"]["failed_case_ids"] = [case["case_id"]]
    candidate["final_validation"] = copy.deepcopy(candidate["validation"])

    with pytest.raises(ValidationError, match="metric-derived score"):
        module.validate_report_schema(report)


def test_report_semantics_require_zero_score_for_explicit_no_run_case():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    candidate = next(item for item in report["candidates"] if item["gate"]["accepted"])
    case = candidate["validation"]["case_results"][0]
    case.update(
        {
            "score": 1.0,
            "passed": False,
            "metrics": {},
            "root_cause": "runtime_error",
            "reasons": ["evaluation runtime error: no run"],
        }
    )
    case["key_trace"]["error_message"] = "AgentEvaluator returned no run for case"
    candidate["validation"]["pass_rate"] = 0.666667
    candidate["validation"]["failed_case_ids"] = [case["case_id"]]
    candidate["final_validation"] = copy.deepcopy(candidate["validation"])

    with pytest.raises(ValidationError, match="no-run case score"):
        module.validate_report_schema(report)


def test_sample_report_records_hashed_normalized_evaluation_config():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    snapshot = report["config_snapshot"]

    assert snapshot["evaluation"]
    assert snapshot["evaluation_sha256"] == module.sha256_json(snapshot["evaluation"])
    snapshot["evaluation"]["num_runs"] = 2
    with pytest.raises(ValidationError, match="evaluation config hash"):
        module.validate_report_schema(report)
    snapshot["evaluation_sha256"] = module.sha256_json(snapshot["evaluation"])
    with pytest.raises(ValidationError, match="evaluation metrics artifact"):
        module.validate_report_schema(report)


def test_sample_prompt_artifact_hashes_are_self_contained():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    artifacts = list(report["baseline"]["prompt_artifacts"])
    for candidate in report["candidates"]:
        artifacts.extend(candidate["prompt_artifacts"])

    assert artifacts
    for artifact in artifacts:
        assert artifact["sha256"] == module.sha256_text(artifact["content"])


def test_report_semantics_reconcile_candidate_audit_cost_with_pipeline_cost():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["candidates"][0]["audit"]["cost"]["estimated"] = 0.5

    with pytest.raises(ValidationError, match="candidate audit cost"):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "environment_seed",
        "candidate_config_hash",
        "environment_extra_secret",
        "config_extra_secret",
        "duplicate_round",
        "round_prompt_hash_keys",
        "candidate_prompt_hash",
        "round_prompt_hash_value",
        "missing_config_hash_disagreement",
        "evalset_turn_count",
        "missing_config_coordinated",
        "missing_metrics_coordinated",
        "missing_evalsets_coordinated",
        "candidate_prompt_artifacts_empty",
        "accepted_round_prompt_evidence_empty",
    ],
)
def test_report_semantics_bind_reproducibility_audit_fields(mutation: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    round_record = {
        "round": 1,
        "optimized_field_names": [],
        "prompt_paths": {"system_prompt": "runs/sample/system.md"},
        "prompt_sha256": {"system_prompt": "0" * 64},
        "prompt_contents": {"system_prompt": "round content"},
        "validation_pass_rate": 0.0,
        "metric_breakdown": {},
        "accepted": False,
        "decision_reason": "audit fixture",
        "failed_case_ids": [],
        "cost_usd": 0.0,
        "token_usage": {"prompt": 0, "completion": 0, "total": 0},
        "duration_seconds": 0.0,
    }
    if mutation == "environment_seed":
        report["environment_snapshot"]["seed"] = 999
    elif mutation == "candidate_config_hash":
        report["candidates"][0]["audit"]["config_sha256"] = "0" * 64
    elif mutation == "environment_extra_secret":
        report["environment_snapshot"]["api_key"] = "must-not-be-accepted"
    elif mutation == "config_extra_secret":
        report["config_snapshot"]["api_key"] = "must-not-be-accepted"
    elif mutation == "duplicate_round":
        report["optimization_rounds"] = [copy.deepcopy(round_record), copy.deepcopy(round_record)]
    elif mutation == "round_prompt_hash_keys":
        round_record["prompt_sha256"] = {}
        report["optimization_rounds"] = [round_record]
    elif mutation == "candidate_prompt_hash":
        report["candidates"][0]["prompt_artifacts"][0]["sha256"] = "0" * 64
    elif mutation == "round_prompt_hash_value":
        round_record["prompt_paths"]["system_prompt"] = str(EXAMPLE_DIR / "agent" / "prompts" / "system.md")
        report["optimization_rounds"] = [round_record]
    elif mutation == "missing_config_hash_disagreement":
        missing_path = "missing/optimizer.json"
        report["config_snapshot"]["paths"]["optimizer_config"] = missing_path
        report["environment_snapshot"]["config_path"] = missing_path
        for index, candidate in enumerate(report["candidates"]):
            candidate["audit"]["config_path"] = missing_path
            candidate["audit"]["config_sha256"] = f"{index + 1:064x}"
    elif mutation == "evalset_turn_count":
        report["config_snapshot"]["evalsets"]["train"]["turn_count"] = 99
    elif mutation == "missing_config_coordinated":
        missing_path = "missing/optimizer.json"
        report["config_snapshot"]["paths"]["optimizer_config"] = missing_path
        report["config_snapshot"]["optimizer_config_sha256"] = "0" * 64
        report["environment_snapshot"]["config_path"] = missing_path
        report["artifacts"]["optimizer_config"] = missing_path
        for candidate in report["candidates"]:
            candidate["audit"]["config_path"] = missing_path
            candidate["audit"]["config_sha256"] = "0" * 64
    elif mutation == "missing_metrics_coordinated":
        missing_path = "missing/metrics.json"
        report["config_snapshot"]["paths"]["evaluation_metrics"] = missing_path
        report["config_snapshot"]["evaluation_metrics_sha256"] = "0" * 64
        report["artifacts"]["eval_metrics"] = missing_path
    elif mutation == "missing_evalsets_coordinated":
        path_keys = {
            "train": "train_evalset",
            "optimizer_dev": "optimizer_dev_evalset",
            "final_validation": "final_validation_evalset",
        }
        for role, path_key in path_keys.items():
            missing_path = f"missing/{role}.json"
            report["config_snapshot"]["paths"][path_key] = missing_path
            report["artifacts"][path_key] = missing_path
            manifest = report["config_snapshot"]["evalsets"][role]
            manifest.update(
                {
                    "path": missing_path,
                    "sha256": "0" * 64,
                    "case_count": 99,
                    "turn_count": 99,
                }
            )
        report["config_snapshot"]["paths"]["validation_evalset"] = report["config_snapshot"]["paths"][
            "final_validation_evalset"
        ]
        report["artifacts"]["validation_evalset"] = report["artifacts"]["final_validation_evalset"]
    elif mutation == "candidate_prompt_artifacts_empty":
        for candidate in report["candidates"]:
            candidate["prompt_artifacts"] = []
    else:
        round_record.update(
            {
                "optimized_field_names": ["system_prompt"],
                "prompt_paths": {},
                "prompt_sha256": {},
                "prompt_contents": {},
                "accepted": True,
            }
        )
        report["optimization_rounds"] = [round_record]

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    "contradiction",
    [
        "candidate_calls_known",
        "candidate_calls_missing_scoped_reason",
        "candidate_calls_missing_top_cost_reason",
        "candidate_calls_missing_top_token_reason",
        "optimizer_unknown_not_propagated",
        "final_unknown_not_propagated",
        "top_unknown_with_known_scopes",
        "reflection_cost_mismatch",
        "reflection_tokens_mismatch",
        "reflection_zero_calls_nonzero_cost",
        "reflection_zero_calls_nonzero_tokens",
    ],
)
def test_report_semantics_enforce_cost_and_token_knownness(contradiction: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    cost = report["cost"]
    optimizer = cost["optimizer"]
    final = cost["final_revalidation"]
    if contradiction == "candidate_calls_known":
        optimizer["candidate_evaluation_agent_calls"] = 1
        optimizer["model_calls"] = 1
        cost["model_calls"] = 1
    elif contradiction == "candidate_calls_missing_scoped_reason":
        optimizer["candidate_evaluation_agent_calls"] = 1
        optimizer["model_calls"] = 1
        optimizer["estimated_cost"] = None
        optimizer["token_usage"] = None
        optimizer["token_usage_known"] = False
        optimizer["unknown_token_usage_reason"] = "unknown"
        cost.update(
            {
                "estimated_total": None,
                "cost_source": "unknown",
                "unknown_cost_reason": "unknown",
                "model_calls": 1,
                "token_usage": None,
                "token_usage_known": False,
                "unknown_token_usage_reason": "unknown",
            }
        )
    elif contradiction in {
        "candidate_calls_missing_top_cost_reason",
        "candidate_calls_missing_top_token_reason",
    }:
        optimizer["candidate_evaluation_agent_calls"] = 1
        optimizer["model_calls"] = 1
        optimizer["estimated_cost"] = None
        optimizer["token_usage"] = None
        optimizer["token_usage_known"] = False
        optimizer["unknown_token_usage_reason"] = "optimizer candidate-evaluation token usage is not exposed"
        cost.update(
            {
                "estimated_total": None,
                "cost_source": "unknown",
                "unknown_cost_reason": (
                    "unknown"
                    if contradiction == "candidate_calls_missing_top_cost_reason"
                    else "optimizer candidate-evaluation calls do not expose token or cost usage"
                ),
                "model_calls": 1,
                "token_usage": None,
                "token_usage_known": False,
                "unknown_token_usage_reason": (
                    "unknown"
                    if contradiction == "candidate_calls_missing_top_token_reason"
                    else "optimizer candidate-evaluation token usage is not exposed"
                ),
            }
        )
    elif contradiction == "optimizer_unknown_not_propagated":
        optimizer["estimated_cost"] = None
        optimizer["token_usage"] = None
        optimizer["token_usage_known"] = False
        optimizer["unknown_token_usage_reason"] = "optimizer unknown"
    elif contradiction == "final_unknown_not_propagated":
        final["estimated_cost"] = None
        final["token_usage"] = None
        final["token_usage_known"] = False
        final["unknown_token_usage_reason"] = "final unknown"
    elif contradiction == "top_unknown_with_known_scopes":
        cost.update(
            {
                "estimated_total": None,
                "cost_source": "unknown",
                "unknown_cost_reason": "unknown",
                "token_usage": None,
                "token_usage_known": False,
                "unknown_token_usage_reason": "unknown",
            }
        )
    elif contradiction == "reflection_cost_mismatch":
        optimizer["reflection_reported_usage"]["estimated_cost"] = 0.5
    elif contradiction == "reflection_tokens_mismatch":
        optimizer["reflection_reported_usage"]["token_usage"] = {
            "prompt": 1,
            "completion": 0,
            "total": 1,
        }
    elif contradiction == "reflection_zero_calls_nonzero_cost":
        optimizer["reflection_reported_usage"]["estimated_cost"] = 0.5
        optimizer["estimated_cost"] = 0.5
        cost["estimated_total"] = 0.5
    else:
        nonzero_tokens = {"prompt": 1, "completion": 0, "total": 1}
        optimizer["reflection_reported_usage"]["token_usage"] = nonzero_tokens
        optimizer["token_usage"] = copy.deepcopy(nonzero_tokens)
        cost["token_usage"] = copy.deepcopy(nonzero_tokens)

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


def test_report_schema_requires_candidate_audit_and_optimization_rounds():
    module = load_pipeline_module()
    missing_audit = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    missing_audit["candidates"][0].pop("audit")

    with pytest.raises(ValidationError):
        module.validate_report_schema(missing_audit)

    missing_rounds = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    missing_rounds.pop("optimization_rounds")

    with pytest.raises(ValidationError):
        module.validate_report_schema(missing_rounds)


def test_report_schema_requires_each_optimization_round_token_usage_field():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["optimization_rounds"] = [
        {
            "round": 1,
            "optimized_field_names": [],
            "prompt_paths": {},
            "prompt_sha256": {},
            "validation_pass_rate": 1.0,
            "metric_breakdown": {},
            "accepted": False,
            "decision_reason": "malformed evidence rejected",
            "failed_case_ids": [],
            "cost_usd": 0.0,
            "token_usage": {"prompt": 0, "completion": 0},
            "duration_seconds": 0.0,
        }
    ]

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("known", "estimated"),
    [(True, None), (False, 0.0)],
)
def test_report_schema_requires_consistent_candidate_audit_cost(
    known: bool,
    estimated: float | None,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    cost = report["candidates"][0]["audit"]["cost"]
    cost["known"] = known
    cost["estimated"] = estimated

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("known", "reason"),
    [(True, "must be null when known"), (False, None)],
)
def test_report_schema_requires_consistent_optimizer_token_accounting(
    known: bool,
    reason: str | None,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    optimizer = report["cost"]["optimizer"]
    optimizer["token_usage_known"] = known
    optimizer["unknown_token_usage_reason"] = reason

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


def test_report_schema_allows_empty_no_run_case_metrics():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    for summary_name in ("validation", "final_validation"):
        case = report["baseline"][summary_name]["case_results"][0]
        case["metrics"] = {}
        case["key_trace"]["error_message"] = "AgentEvaluator returned no run for case"
        case["root_cause"] = "runtime_error"
        case["reasons"] = ["evaluation runtime error: AgentEvaluator returned no run for case"]
        metric = report["baseline"][summary_name]["metrics"][ROUTE_TOOL_ARGS_METRIC]
        metric.update({"score": 1.0, "passed": True, "status": "passed"})
    report["failure_attribution"] = module.attribution_for(report["baseline"]["validation"])

    module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("mutation_path", "name"),
    [
        (("baseline", "validation", "case_results", 0), "unexpected_case_field"),
        (("candidates", 0, "case_deltas", 0), "unexpected_delta_field"),
        (("candidates", 0, "gate"), "unexpected_gate_field"),
        (("candidates", 0, "audit", "cost"), "unexpected_cost_field"),
    ],
)
def test_report_schema_rejects_extra_core_properties(
    mutation_path: tuple[Any, ...],
    name: str,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    target: Any = report
    for key in mutation_path:
        target = target[key]
    target[name] = True

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize("name", ["unexpected_root_field", "api_key"])
def test_report_schema_rejects_unknown_root_properties(name: str):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report[name] = "must not be accepted"

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    ("mutation_path", "value"),
    [
        (("run_id",), "../escape"),
        (("candidates", 0, "id"), "nested/candidate"),
        (("candidates", 0, "gate", "candidate_id"), "C:\\absolute"),
        (("run_id",), "CON"),
        (("run_id",), "nul.txt"),
        (("candidates", 0, "id"), "Com1.json"),
        (("candidates", 0, "gate", "candidate_id"), "LPT9.log"),
    ],
)
def test_report_schema_rejects_unsafe_artifact_identifiers(
    mutation_path: tuple[Any, ...],
    value: str,
):
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    target: Any = report
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = value

    with pytest.raises(ValidationError):
        module.validate_report_schema(report)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/run",
        "nested\\run",
        "/absolute",
        "C:\\absolute",
        "CON",
        "nul.txt",
        "Com1.json",
        "LPT9.log",
    ],
)
def test_make_run_dir_rejects_unsafe_single_path_components(tmp_path: Path, run_id: str):
    module = load_pipeline_module()

    with pytest.raises(ValueError, match="run_id"):
        module.make_run_dir(tmp_path, run_id)


@pytest.mark.parametrize("candidate_id", ["..", "../escape", "nested/candidate", "nested\\candidate"])
def test_prompt_artifacts_reject_unsafe_candidate_path_components(
    tmp_path: Path,
    candidate_id: str,
):
    module = load_pipeline_module()
    source_prompts = module.read_source_prompts(
        EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )

    with pytest.raises(ValueError, match="candidate_id"):
        module.write_prompt_artifacts(
            run_dir=tmp_path,
            candidate_id=candidate_id,
            source_prompts=source_prompts,
            candidate_prompts={},
            summary="unsafe candidate",
            source_written=False,
        )

    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("writer", ["candidate", "optimizer_round"])
def test_prompt_artifact_writers_reject_preexisting_prompts_symlink_escape(
    tmp_path: Path,
    writer: str,
):
    module = load_pipeline_module()
    run_dir = tmp_path / "run"
    outside = tmp_path / "outside"
    run_dir.mkdir()
    outside.mkdir()
    try:
        (run_dir / "prompts").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="beneath run_dir"):
        if writer == "candidate":
            module.write_prompt_artifacts(
                run_dir=run_dir,
                candidate_id="candidate",
                source_prompts=module.read_source_prompts(
                    EXAMPLE_DIR / "agent" / "prompts" / "system.md",
                    EXAMPLE_DIR / "agent" / "prompts" / "router.md",
                ),
                candidate_prompts={},
                summary="containment test",
                source_written=False,
            )
        else:
            module.write_optimizer_round_artifacts(
                run_dir=run_dir,
                rounds=[
                    SimpleNamespace(
                        round=1,
                        optimized_field_names=[],
                        candidate_prompts={"system_prompt": "prompt"},
                        validation_pass_rate=1.0,
                        metric_breakdown={},
                        accepted=False,
                        acceptance_reason=None,
                        skip_reason="containment test",
                        error_message=None,
                        failed_case_ids=[],
                        round_llm_cost=0.0,
                        round_token_usage={"prompt": 0, "completion": 0, "total": 0},
                        duration_seconds=0.0,
                    )
                ],
            )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("writer", ["report_file", "trace_evalsets_dir"])
def test_run_artifact_writers_reject_preexisting_symlink_escape(
    tmp_path: Path,
    writer: str,
):
    module = load_pipeline_module()
    run_dir = tmp_path / "run"
    outside = tmp_path / "outside"
    run_dir.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    try:
        if writer == "report_file":
            (run_dir / "optimization_report.json").symlink_to(sentinel)
        else:
            (run_dir / "evalsets").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="run_dir|symlink"):
        if writer == "report_file":
            report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
            module.write_report(run_dir, report)
        else:
            payload = load_report(EXAMPLE_DIR / "train.evalset.json")
            module.materialize_trace_evalset(
                source_evalset=EXAMPLE_DIR / "train.evalset.json",
                payload=payload,
                outputs={},
                run_dir=run_dir,
                candidate_id="candidate",
                split="train",
            )

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def test_router_prompt_is_instructional_not_a_gold_answer():
    prompt = (EXAMPLE_DIR / "agent" / "prompts" / "router.md").read_text(encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        json.loads(prompt)

    assert "Output exactly one JSON object" in prompt
    assert "route" in prompt
    assert "create_refund_ticket" in prompt


@pytest.mark.asyncio
async def test_fake_mode_generates_complete_report_and_selects_local_patch(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="fake_case",
    )
    report = load_report(run_dir / "optimization_report.json")

    required = {
        "run_id",
        "mode",
        "seed",
        "baseline",
        "candidates",
        "delta",
        "gate_decision",
        "failure_attribution",
        "cost",
        "duration_seconds",
        "artifacts",
    }
    assert required <= set(report)
    assert report["mode"] == "fake"
    assert "known_warning_filters" not in report["environment_snapshot"]
    assert "SSEDecoder._aiter_chunks close RuntimeWarning" not in json.dumps(report)
    assert report["gate_decision"]["accepted"] is True
    assert report["gate_decision"]["winner"] == "candidate_local_patch"
    assert report["baseline"]["validation"]["score"] == pytest.approx(2 / 3)
    assert report["baseline"]["final_validation"]["score"] == pytest.approx(2 / 3)
    assert "optimizer_dev" in report["baseline"]
    assert report["artifacts"]["optimizer_dev_evalset"].endswith("optimizer_dev.evalset.json")
    assert report["artifacts"]["final_validation_evalset"].endswith("val.evalset.json")
    assert report["delta"]["validation_score"] == pytest.approx(1 / 3)
    assert "environment_snapshot" in report
    assert report["environment_snapshot"]["seed"] == 7
    assert report["environment_snapshot"]["config_path"].endswith("optimizer.json")
    first_case = report["baseline"]["validation"]["case_results"][0]
    assert first_case["expected_text"]
    assert first_case["key_trace"]["invocation_id"]
    assert first_case["key_trace"]["actual_final_response"] == first_case["actual_text"]
    assert first_case["key_trace"]["expected_final_response"] == first_case["expected_text"]
    assert set(first_case["key_trace"]) == {
        "invocation_id",
        "actual_final_response",
        "expected_final_response",
        "error_message",
    }
    module.validate_report_schema(report)
    assert (run_dir / "optimization_report.md").is_file()


def _assert_candidate_audit(candidate: dict[str, Any], seed: int) -> None:
    audit = candidate["audit"]
    assert audit["seed"] == seed
    assert audit["duration_seconds"] >= 0
    assert audit["cost"]["currency"] == "USD"
    assert audit["config_sha256"]
    assert len(audit["config_sha256"]) == 64


@pytest.mark.asyncio
async def test_fake_mode_audits_each_candidate_independently(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="candidate_audit",
    )
    report = load_report(run_dir / "optimization_report.json")

    assert report["optimization_rounds"] == []
    for candidate in report["candidates"]:
        _assert_candidate_audit(candidate, 7)
        assert Path(candidate["artifacts"]["prompt_dir"]).is_dir()
        assert Path(candidate["artifacts"]["prompt_patch"]).is_file()


@pytest.mark.asyncio
async def test_markdown_includes_rejected_candidate_delta_types_absent_from_winner(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="markdown_case_delta_parity",
    )
    report = load_report(run_dir / "optimization_report.json")
    markdown = (run_dir / "optimization_report.md").read_text(encoding="utf-8")
    winner = next(
        candidate for candidate in report["candidates"] if candidate["id"] == report["gate_decision"]["winner"]
    )
    rejected = next(candidate for candidate in report["candidates"] if candidate["id"] == "candidate_overfit")
    rejected_types = {item["change_type"] for item in rejected["case_deltas"]}
    winner_types = {item["change_type"] for item in winner["case_deltas"]}

    assert rejected["gate"]["accepted"] is False
    assert rejected_types - winner_types == {"new_fail"}
    for candidate in report["candidates"]:
        header = f"## Validation Case Delta: `{candidate['id']}`"
        assert header in markdown
        section = markdown.split(header, 1)[1].split("## ", 1)[0]
        for item in candidate["case_deltas"]:
            assert f"`{item['case_id']}`" in section
            assert f"change_type `{item['change_type']}`" in section


@pytest.mark.asyncio
async def test_fake_mode_report_scores_come_from_agent_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = patch_agent_evaluator(monkeypatch, score=0.25, passed=False)
    module = load_pipeline_module()

    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="fake_evaluator_backed",
    )
    report = load_report(run_dir / "optimization_report.json")

    assert calls, "fake mode must run AgentEvaluator, not direct fixture scoring"
    assert report["baseline"]["validation"]["score"] == pytest.approx(0.25)
    first_case = report["baseline"]["validation"]["case_results"][0]
    assert first_case["metrics"][ROUTE_TOOL_ARGS_METRIC]["score"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_route_tool_argument_metric_ignores_reason_text(tmp_path: Path):
    module = load_pipeline_module()
    payload = load_report(EXAMPLE_DIR / "train.evalset.json")
    payload["eval_set_id"] = "reason_wording_regression"
    payload["eval_cases"] = [payload["eval_cases"][0]]
    evalset_path = tmp_path / "reason_wording.evalset.json"
    evalset_path.write_text(json.dumps(payload), encoding="utf-8")
    metrics_path = module.offline_metrics_path(tmp_path)

    async def call_agent(_: str) -> str:
        return (
            '{"route":"refund","tool":{"name":"create_refund_ticket","arguments":{}},'
            '"reason":"A different but harmless explanation."}'
        )

    summary = await module.run_evaluator(
        evalset_path=evalset_path,
        evalset_payload=payload,
        metrics_path=metrics_path,
        call_agent=call_agent,
        offline_rubric=True,
    )

    assert summary["score"] == pytest.approx(1.0)
    assert summary["case_results"][0]["metrics"][ROUTE_TOOL_ARGS_METRIC]["passed"] is True


def test_gate_rejects_noop_and_overfit_candidates(tmp_path: Path):
    module = load_pipeline_module()
    started = module.time.perf_counter()
    report = module.make_report(
        mode="fake",
        run_id="gate_unit",
        run_dir=tmp_path,
        seed=7,
        started=started,
    )
    by_id = {candidate["id"]: candidate for candidate in report["candidates"]}

    assert by_id["candidate_local_patch"]["gate"]["accepted"] is True
    assert by_id["candidate_noop"]["gate"]["accepted"] is False
    assert "validation score did not improve" in " ".join(by_id["candidate_noop"]["gate"]["reasons"])
    assert by_id["candidate_overfit"]["gate"]["accepted"] is False
    overfit_reasons = " ".join(by_id["candidate_overfit"]["gate"]["reasons"])
    assert "hard fail" in overfit_reasons
    assert "critical case" in overfit_reasons


@pytest.mark.asyncio
async def test_trace_mode_uses_replay_without_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("TRPC_AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("TRPC_AGENT_MODEL_NAME", raising=False)

    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="trace",
        seed=7,
        output_dir=tmp_path,
        run_id="trace_case",
    )
    report = load_report(run_dir / "optimization_report.json")
    assert report["mode"] == "trace"
    assert report["gate_decision"]["winner"] == "candidate_local_patch"
    module.validate_report_schema(report)
    assert (run_dir / "trace_evalset.json").is_file()
    assert (run_dir / "trace_metrics.json").is_file()
    trace_payload = load_report(run_dir / "trace_evalset.json")
    assert all(case["eval_mode"] == "trace" for case in trace_payload["eval_cases"])
    assert report["artifacts"]["fixtures"].endswith("trace_outputs.json")


@pytest.mark.asyncio
async def test_trace_mode_consumes_trace_fixture_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = load_pipeline_module()
    trace_fixtures = load_report(EXAMPLE_DIR / "fixtures" / "fake_outputs.json")
    trace_marker = '{"route":"faq","tool":{"name":"none","arguments":{}},' '"reason":"TRACE FIXTURE MARKER"}'
    trace_fixtures["candidate_local_patch"]["outputs"]["val_address_change_102"] = trace_marker
    trace_fixture_path = tmp_path / "trace_outputs.json"
    trace_fixture_path.write_text(json.dumps(trace_fixtures), encoding="utf-8")
    monkeypatch.setattr(module, "TRACE_FIXTURE_PATH", trace_fixture_path, raising=False)

    run_dir = await module.run_fake_or_trace(
        mode="trace",
        seed=7,
        output_dir=tmp_path,
        run_id="trace_fixture_source",
    )
    report = load_report(run_dir / "optimization_report.json")
    candidate = next(item for item in report["candidates"] if item["id"] == "candidate_local_patch")
    actual_by_id = {item["case_id"]: item["actual_text"] for item in candidate["validation"]["case_results"]}

    assert report["artifacts"]["fixtures"] == str(trace_fixture_path.resolve())
    assert actual_by_id["val_address_change_102"] == trace_marker


@pytest.mark.asyncio
async def test_trace_mode_evaluates_baseline_and_each_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = patch_agent_evaluator(monkeypatch)
    module = load_pipeline_module()

    await module.run_fake_or_trace(
        mode="trace",
        seed=7,
        output_dir=tmp_path,
        run_id="trace_all_candidates",
    )

    assert len(calls) == 12


def test_cli_fake_mode_runs_end_to_end(tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            str(RUN_PIPELINE),
            "--mode",
            "fake",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "cli_fake",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    run_dir = Path(proc.stdout.strip().splitlines()[-1])
    assert run_dir == tmp_path / "cli_fake"
    report = load_report(run_dir / "optimization_report.json")
    assert report["gate_decision"]["winner"] == "candidate_local_patch"


def test_cli_trace_mode_defaults_to_trace_outputs(tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            str(RUN_PIPELINE),
            "--mode",
            "trace",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "cli_trace_fixture",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    run_dir = Path(proc.stdout.strip().splitlines()[-1])
    report = load_report(run_dir / "optimization_report.json")

    assert report["artifacts"]["fixtures"].endswith("trace_outputs.json")


@pytest.mark.asyncio
async def test_gate_config_can_require_larger_validation_delta(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="strict_gate",
        gate_config={"min_validation_delta": 0.5},
    )
    report = load_report(run_dir / "optimization_report.json")
    assert report["gate_decision"]["accepted"] is False
    assert report["gate_decision"]["winner"] is None
    reasons = " ".join(report["gate_decision"]["reasons"])
    assert "validation score improvement" in reasons


def test_default_gate_inherits_required_metrics_from_optimizer_config():
    module = load_pipeline_module()

    gate = module.load_gate_config(optimizer_config=EXAMPLE_DIR / "optimizer.json")

    assert gate["required_metrics"] == [
        ROUTE_TOOL_ARGS_METRIC,
        "llm_rubric_response",
    ]
    assert gate["required_metrics_source"] == "optimizer_config"


def test_required_metric_failure_rejects_even_when_primary_score_improves():
    module = load_pipeline_module()
    baseline_val = {
        "score": 0.5,
        "metrics": {
            ROUTE_TOOL_ARGS_METRIC: {"passed": False},
            "llm_rubric_response": {"passed": True},
        },
        "case_results": [
            {"case_id": "case_1", "score": 0.5, "passed": False, "tags": []},
        ],
    }
    candidate_val = {
        "score": 1.0,
        "metrics": {
            ROUTE_TOOL_ARGS_METRIC: {"passed": True},
            "llm_rubric_response": {"passed": False},
        },
        "case_results": [
            {"case_id": "case_1", "score": 1.0, "passed": True, "tags": []},
        ],
    }

    gate = module.apply_gate(
        candidate_id="candidate",
        baseline_val=baseline_val,
        candidate_val=candidate_val,
        gate_config=module.load_gate_config(optimizer_config=EXAMPLE_DIR / "optimizer.json"),
        duration_seconds=0.01,
        cost_usd=0.0,
    )

    assert gate["accepted"] is False
    assert "llm_rubric_response" in " ".join(gate["reasons"])


def test_validation_regression_is_rejected_even_without_hard_fail():
    module = load_pipeline_module()
    baseline_val = {
        "score": 0.75,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": True}},
        "case_results": [
            {"case_id": "case_1", "score": 0.75, "passed": True, "tags": []},
        ],
    }
    candidate_val = {
        "score": 0.5,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": True}},
        "case_results": [
            {"case_id": "case_1", "score": 0.5, "passed": True, "tags": []},
        ],
    }

    gate = module.apply_gate(
        candidate_id="candidate",
        baseline_val=baseline_val,
        candidate_val=candidate_val,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=0.01,
        cost_usd=0.0,
    )

    assert gate["accepted"] is False
    assert "validation score did not improve" in " ".join(gate["reasons"])


def test_failure_attribution_taxonomy_handles_parameter_format_and_rubric_failures():
    module = load_pipeline_module()

    parameter = module.attribute_failure_case(
        actual_text='{"route":"refund","tool":{"name":"create_refund_ticket","arguments":{"unexpected":true}}}',
        expected_text='{"route":"refund","tool":{"name":"create_refund_ticket","arguments":{}}}',
        error_message=None,
        metrics={ROUTE_TOOL_ARGS_METRIC: {"passed": False}},
    )
    assert parameter["root_cause"] == "parameter_error"
    assert parameter["reasons"]

    formatted = module.attribute_failure_case(
        actual_text="not json",
        expected_text='{"route":"faq","tool":{"name":"none","arguments":{}}}',
        error_message=None,
        metrics={ROUTE_TOOL_ARGS_METRIC: {"passed": False}},
    )
    assert formatted["root_cause"] == "format_error"

    rubric = module.attribute_failure_case(
        actual_text='{"route":"faq","tool":{"name":"none","arguments":{}}}',
        expected_text='{"route":"faq","tool":{"name":"none","arguments":{}}}',
        error_message=None,
        metrics={
            ROUTE_TOOL_ARGS_METRIC: {"passed": True},
            "llm_rubric_response": {"passed": False},
        },
    )
    assert rubric["root_cause"] == "rubric_failed"


@pytest.mark.parametrize(
    ("actual_text", "expected_root"),
    [
        (
            '{"route":"faq","tool":"none","reason":"bad shape"}',
            "tool_call_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none","arguments":null},"reason":"bad args"}',
            "parameter_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none","arguments":[]},"reason":"bad args"}',
            "parameter_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none"},"reason":"missing args"}',
            "parameter_error",
        ),
    ],
)
def test_failure_attribution_is_total_for_malformed_tool_shapes(
    actual_text: str,
    expected_root: str,
):
    module = load_pipeline_module()
    result = module.attribute_failure_case(
        actual_text=actual_text,
        expected_text=('{"route":"faq","tool":{"name":"none","arguments":{}},' '"reason":"expected"}'),
        error_message=None,
        metrics={ROUTE_TOOL_ARGS_METRIC: {"passed": False}},
    )
    assert result["root_cause"] == expected_root
    assert result["reasons"]


def test_gate_rejects_when_configured_cost_budget_cannot_be_evaluated():
    module = load_pipeline_module()
    baseline_val = {
        "score": 0.5,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": False}},
        "case_results": [
            {"case_id": "case_1", "score": 0.5, "passed": False, "tags": []},
        ],
    }
    candidate_val = {
        "score": 1.0,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": True}},
        "case_results": [
            {"case_id": "case_1", "score": 1.0, "passed": True, "tags": []},
        ],
    }

    gate = module.apply_gate(
        candidate_id="candidate",
        baseline_val=baseline_val,
        candidate_val=candidate_val,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC], "max_cost_usd": 0.01},
        duration_seconds=0.01,
        cost_usd=None,
    )

    assert gate["accepted"] is False
    assert "cost budget could not be evaluated" in " ".join(gate["reasons"])


def test_cli_accepts_custom_paths(tmp_path: Path):
    custom_dir = tmp_path / "inputs"
    custom_dir.mkdir()
    train = custom_dir / "train.copy.evalset.json"
    val = custom_dir / "val.copy.evalset.json"
    optimizer_dev = custom_dir / "optimizer_dev.copy.evalset.json"
    optimizer = custom_dir / "optimizer.copy.json"
    system_prompt = custom_dir / "system.md"
    router_prompt = custom_dir / "router.md"
    shutil.copy2(EXAMPLE_DIR / "train.evalset.json", train)
    shutil.copy2(EXAMPLE_DIR / "val.evalset.json", val)
    shutil.copy2(EXAMPLE_DIR / "optimizer_dev.evalset.json", optimizer_dev)
    shutil.copy2(EXAMPLE_DIR / "optimizer.json", optimizer)
    shutil.copy2(EXAMPLE_DIR / "agent" / "prompts" / "system.md", system_prompt)
    shutil.copy2(EXAMPLE_DIR / "agent" / "prompts" / "router.md", router_prompt)

    proc = subprocess.run(
        [
            sys.executable,
            str(RUN_PIPELINE),
            "--mode",
            "fake",
            "--train-evalset",
            str(train),
            "--val-evalset",
            str(val),
            "--optimizer-dev-evalset",
            str(optimizer_dev),
            "--optimizer-config",
            str(optimizer),
            "--system-prompt",
            str(system_prompt),
            "--router-prompt",
            str(router_prompt),
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "custom_paths",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    run_dir = Path(proc.stdout.strip().splitlines()[-1])
    report = load_report(run_dir / "optimization_report.json")
    assert report["artifacts"]["train_evalset"] == str(train)
    assert report["artifacts"]["validation_evalset"] == str(val)
    assert report["artifacts"]["optimizer_dev_evalset"] == str(optimizer_dev)
    assert report["artifacts"]["optimizer_config"] == str(optimizer)


@pytest.mark.asyncio
async def test_run_evaluator_propagates_unrelated_assertion_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    metrics_path = module.offline_metrics_path(tmp_path)

    class BrokenExecuter:
        async def evaluate(self) -> None:
            raise AssertionError("metric configuration is broken")

        def get_result(self):
            return make_evaluate_result(EXAMPLE_DIR / "train.evalset.json")

    def fake_get_executer(*_: Any, **__: Any) -> BrokenExecuter:
        return BrokenExecuter()

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentEvaluator, "get_executer", staticmethod(fake_get_executer))

    with pytest.raises(AssertionError, match="metric configuration is broken"):
        await module.run_evaluator(
            evalset_path=EXAMPLE_DIR / "train.evalset.json",
            evalset_payload=load_report(EXAMPLE_DIR / "train.evalset.json"),
            metrics_path=metrics_path,
        )


@pytest.mark.asyncio
async def test_fake_mode_records_prompt_artifacts(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="prompt_audit",
    )
    report = load_report(run_dir / "optimization_report.json")

    all_artifacts = list(report["baseline"]["prompt_artifacts"])
    for candidate in report["candidates"]:
        all_artifacts.extend(candidate["prompt_artifacts"])
        assert Path(candidate["artifacts"]["prompt_patch"]).is_file()

    expected_count = 2 * (1 + len(report["candidates"]))
    assert len(all_artifacts) == expected_count

    for prompt_artifact in all_artifacts:
        assert prompt_artifact["name"] in {"system_prompt", "router_prompt"}
        assert Path(prompt_artifact["source_path"]).is_file()
        assert Path(prompt_artifact["candidate_path"]).is_file()
        assert len(prompt_artifact["sha256"]) == 64
        assert prompt_artifact["source_written"] is False
        assert prompt_artifact["summary"]
        assert "diff" in prompt_artifact


def test_online_preflight_reports_presence_without_secret(monkeypatch: pytest.MonkeyPatch):
    module = load_pipeline_module()
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "sk-secret-value")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "example-model")

    preflight = module.online_preflight()
    text = module.format_online_preflight(preflight)

    assert preflight == {
        "TRPC_AGENT_API_KEY": True,
        "TRPC_AGENT_BASE_URL": True,
        "TRPC_AGENT_MODEL_NAME": True,
    }
    assert "sk-secret-value" not in text
    assert "TRPC_AGENT_API_KEY=present" in text


@pytest.mark.asyncio
async def test_online_mode_missing_env_fails_before_api_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("TRPC_AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("TRPC_AGENT_MODEL_NAME", raising=False)
    module = load_pipeline_module()

    with pytest.raises(ValueError) as exc_info:
        await module.run_online(seed=7, output_dir=tmp_path, run_id="online_missing_env")
    message = str(exc_info.value)
    assert "online mode requires environment variables" in message
    assert "TRPC_AGENT_API_KEY" in message


@pytest.mark.asyncio
async def test_online_rejects_malformed_optimizer_required_metrics_before_optimizer_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")
    module = load_pipeline_module()
    payload = load_report(EXAMPLE_DIR / "optimizer.json")
    payload["optimize"]["stop"]["required_metrics"] = "metric"
    optimizer_config = tmp_path / "malformed_optimizer.json"
    optimizer_config.write_text(json.dumps(payload), encoding="utf-8")
    called = False

    async def fail_if_called(**_: Any):
        nonlocal called
        called = True
        raise AssertionError("optimizer must not be called")

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fail_if_called))

    with pytest.raises(ValueError, match="required_metrics"):
        await module.run_online(
            seed=7,
            output_dir=tmp_path,
            run_id="malformed_optimizer_config",
            optimizer_config=optimizer_config,
        )

    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_during_run", [False, True])
async def test_online_call_agent_closes_runner(
    monkeypatch: pytest.MonkeyPatch,
    raise_during_run: bool,
):
    module = load_pipeline_module()
    closed: list[bool] = []

    class FakeRunner:
        def __init__(self, **kwargs: Any):
            pass

        async def run_async(self, **kwargs: Any):
            if raise_during_run:
                raise RuntimeError("model stream failed")
            if False:
                yield None

        async def close(self):
            closed.append(True)

    import trpc_agent_sdk.runners as runners

    monkeypatch.setattr(runners, "Runner", FakeRunner)
    monkeypatch.setattr(module, "_make_llm_agent_from_prompts", lambda prompt_texts: object())
    call_agent = module.make_online_call_agent(
        system_prompt=EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        router_prompt=EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )

    if raise_during_run:
        with pytest.raises(RuntimeError, match="model stream failed"):
            await call_agent("hello")
    else:
        assert await call_agent("hello") == ""

    assert closed == [True]


@pytest.mark.asyncio
async def test_online_call_agent_closes_runner_when_session_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    closed: list[bool] = []

    class FakeRunner:
        def __init__(self, **kwargs: Any):
            pass

        async def close(self):
            closed.append(True)

    class FailingSessionService:
        async def create_session(self, **kwargs: Any):
            raise RuntimeError("session creation failed")

    import trpc_agent_sdk.runners as runners
    import trpc_agent_sdk.sessions as sessions

    monkeypatch.setattr(runners, "Runner", FakeRunner)
    monkeypatch.setattr(sessions, "InMemorySessionService", FailingSessionService)
    monkeypatch.setattr(module, "_make_llm_agent_from_prompts", lambda prompt_texts: object())
    call_agent = module.make_online_call_agent(
        system_prompt=EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        router_prompt=EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )

    with pytest.raises(RuntimeError, match="session creation failed"):
        await call_agent("hello")

    assert closed == [True]


@pytest.mark.asyncio
async def test_online_call_agent_preserves_stream_error_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    closed: list[bool] = []
    cleanup_messages: list[str] = []

    class FakeRunner:
        def __init__(self, **kwargs: Any):
            pass

        async def run_async(self, **kwargs: Any):
            raise RuntimeError("model stream failed")
            yield None

        async def close(self):
            closed.append(True)
            raise RuntimeError("runner close failed")

    class FakeLogger:
        def exception(self, message: str):
            cleanup_messages.append(message)

    import trpc_agent_sdk.runners as runners

    monkeypatch.setattr(runners, "Runner", FakeRunner)
    monkeypatch.setattr(module, "logger", FakeLogger(), raising=False)
    monkeypatch.setattr(module, "_make_llm_agent_from_prompts", lambda prompt_texts: object())
    call_agent = module.make_online_call_agent(
        system_prompt=EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        router_prompt=EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )

    with pytest.raises(RuntimeError, match="model stream failed"):
        await call_agent("hello")

    assert closed == [True]
    assert cleanup_messages == ["Failed to close online evaluation runner after a primary error."]


@pytest.mark.asyncio
async def test_online_call_agent_surfaces_close_failure_after_success(
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    closed: list[bool] = []

    class FakeRunner:
        def __init__(self, **kwargs: Any):
            pass

        async def run_async(self, **kwargs: Any):
            if False:
                yield None

        async def close(self):
            closed.append(True)
            raise RuntimeError("runner close failed")

    import trpc_agent_sdk.runners as runners

    monkeypatch.setattr(runners, "Runner", FakeRunner)
    monkeypatch.setattr(module, "_make_llm_agent_from_prompts", lambda prompt_texts: object())
    call_agent = module.make_online_call_agent(
        system_prompt=EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        router_prompt=EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )

    with pytest.raises(RuntimeError, match="runner close failed"):
        await call_agent("hello")

    assert closed == [True]


def test_online_warning_observability_is_not_suppressed():
    from trpc_agent_sdk.models.openai_adapter import _deepseek
    from trpc_agent_sdk.types import GenerateContentConfig

    sse_warning = "coroutine method 'aclose' of 'SSEDecoder._aiter_chunks' was never awaited"
    with patch.object(_deepseek.logger, "warning") as warning:
        handled, response_format = _deepseek.DeepSeekAdapter("deepseek-v4-flash").build_response_format(
            GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema={"type": "object"},
            )
        )

    assert handled is True
    assert response_format == {"type": "json_object"}
    warning.assert_called_once_with("DeepSeek only supports JSON object response_format; response schema is ignored.")
    assert not any(
        action == "ignore" and issubclass(RuntimeWarning, category) and (message is None or message.search(sse_warning))
        for action, message, category, _module, _line in warnings.filters
    )
    with pytest.warns(RuntimeWarning, match=re.escape(sse_warning)):
        warnings.warn(sse_warning, RuntimeWarning, stacklevel=1)


@pytest.mark.asyncio
async def test_online_mode_can_construct_optimizer_call_without_real_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")

    module = load_pipeline_module()
    captured: dict[str, Any] = {}

    class FakeResult:
        status = "SUCCEEDED"
        baseline_pass_rate = 0.5
        best_pass_rate = 1.0
        pass_rate_improvement = 0.5
        stop_reason = "completed"
        total_llm_cost = 0.0
        total_reflection_lm_calls = 2
        total_judge_model_calls = 3
        best_prompts = {
            "system_prompt": "fake system",
            "router_prompt": "fake router",
        }
        baseline_prompts = {
            "system_prompt": "baseline system",
            "router_prompt": "baseline router",
        }
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 8, "completion": 2, "total": 10}

    async def fake_optimize(**kwargs):
        captured.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.txt").write_text("fake", encoding="utf-8")
        return FakeResult()

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    patch_agent_evaluator(monkeypatch)
    run_dir = await module.run_online(seed=42, output_dir=tmp_path, run_id="online_wiring")

    assert captured["config_path"].endswith("optimizer.json")
    captured_config = load_report(Path(captured["config_path"]))
    assert captured_config["optimize"]["algorithm"]["seed"] == 42
    assert captured["train_dataset_path"].endswith("train.evalset.json")
    assert captured["validation_dataset_path"].endswith("optimizer_dev.evalset.json")
    assert not captured["validation_dataset_path"].endswith("val.evalset.json")
    assert captured["update_source"] is False
    assert sorted(captured["target_prompt"].names()) == ["router_prompt", "system_prompt"]
    report = load_report(run_dir / "optimization_report.json")
    assert report["mode"] == "online"
    assert report["online_result"]["status"] == "SUCCEEDED"
    assert report["artifacts"]["optimizer_dev_evalset"].endswith("optimizer_dev.evalset.json")
    assert report["artifacts"]["final_validation_evalset"].endswith("val.evalset.json")
    assert report["optimization_rounds"] == []
    _assert_candidate_audit(report["candidates"][0], 42)
    for name, value in report["artifacts"].items():
        if name.startswith("native_") and value:
            assert Path(value).exists(), name
    assert report["environment_snapshot"]["model_name"] == "fake-model"
    assert report["environment_snapshot"]["base_url_host"] == "localhost"
    assert report["environment_snapshot"]["command"]
    module.validate_report_schema(report)
    assert report["cost"]["estimated_total"] is None
    assert report["cost"]["cost_source"] == "unknown"
    assert report["cost"]["optimizer"]["model_calls"] == 5
    assert report["cost"]["optimizer"]["native_judge_model_calls"] == 3
    assert report["cost"]["optimizer"]["judge_model_call_source"] == "native_optimizer_counter"
    assert report["cost"]["optimizer"]["token_usage"] is None
    assert report["cost"]["optimizer"]["reflection_reported_usage"]["token_usage"]["total"] == 10
    assert report["cost"]["final_revalidation"]["model_calls"] > 0
    assert report["cost"]["token_usage"] is None
    assert report["cost"]["token_usage_known"] is False
    assert report["cost"]["optimizer"]["token_usage_known"] is False
    assert report["cost"]["final_revalidation"]["token_usage"] is None
    assert report["cost"]["final_revalidation"]["token_usage_known"] is False
    assert report["cost"]["model_calls"] == (
        report["cost"]["optimizer"]["model_calls"] + report["cost"]["final_revalidation"]["model_calls"]
    )


@pytest.mark.asyncio
async def test_online_failed_optimizer_preserves_partial_best_prompt_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")
    module = load_pipeline_module()

    class FakeResult:
        status = "FAILED"
        error_message = "optimizer provider failed"
        baseline_pass_rate = 0.5
        best_pass_rate = 0.5
        pass_rate_improvement = 0.0
        stop_reason = None
        total_llm_cost = 0.0
        total_reflection_lm_calls = 0
        total_judge_model_calls = 0
        best_prompts = {"system_prompt": "partial system prompt"}
        baseline_prompts = {}
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 0, "completion": 0, "total": 0}
        rounds: list[Any] = []

    async def fake_optimize(**kwargs: Any) -> FakeResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        return FakeResult()

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    patch_agent_evaluator(monkeypatch, score=0.5, passed=False)

    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_failed_partial")
    report = load_report(run_dir / "optimization_report.json")
    candidate = report["candidates"][0]
    router_artifact = next(
        artifact for artifact in candidate["prompt_artifacts"] if artifact["name"] == "router_prompt"
    )

    assert report["online_result"]["status"] == "FAILED"
    assert report["online_result"]["error_message"] == "optimizer provider failed"
    assert report["gate_decision"]["accepted"] is False
    assert "optimizer provider failed" in " ".join(candidate["gate"]["reasons"])
    assert Path(router_artifact["candidate_path"]).read_text(encoding="utf-8") == (
        EXAMPLE_DIR / "agent" / "prompts" / "router.md"
    ).read_text(encoding="utf-8")
    _assert_candidate_audit(candidate, 7)


@pytest.mark.asyncio
async def test_online_round_audit_writes_native_prompt_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")
    module = load_pipeline_module()

    round_prompt = "optimized system prompt"

    class FakeResult:
        status = "SUCCEEDED"
        error_message = ""
        baseline_pass_rate = 0.5
        best_pass_rate = 1.0
        pass_rate_improvement = 0.5
        stop_reason = "completed"
        total_llm_cost = 0.01
        total_reflection_lm_calls = 1
        total_judge_model_calls = 0
        best_prompts = {"system_prompt": round_prompt, "router_prompt": "optimized router prompt"}
        baseline_prompts = {}
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 8, "completion": 2, "total": 10}
        rounds = [
            SimpleNamespace(
                round=1,
                optimized_field_names=["system_prompt"],
                candidate_prompts={"system_prompt": round_prompt},
                validation_pass_rate=1.0,
                metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
                accepted=True,
                acceptance_reason="improved validation",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, "completion": 2, "total": 10},
                duration_seconds=0.25,
            )
        ]

    async def fake_optimize(**kwargs: Any) -> FakeResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.txt").write_text("complete", encoding="utf-8")
        (output_dir / "rounds").mkdir()
        return FakeResult()

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    patch_agent_evaluator(monkeypatch, score=0.5, passed=False)

    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_round_audit")
    report = load_report(run_dir / "optimization_report.json")
    round_record = report["optimization_rounds"][0]
    prompt_path = Path(round_record["prompt_paths"]["system_prompt"])

    assert round_record["round"] == 1
    assert round_record["optimized_field_names"] == ["system_prompt"]
    assert prompt_path.is_file()
    assert prompt_path.read_text(encoding="utf-8") == round_prompt
    assert round_record["prompt_sha256"]["system_prompt"] == module.sha256_text(round_prompt)
    assert round_record["accepted"] is True
    assert round_record["decision_reason"] == "improved validation"
    assert Path(report["artifacts"]["native_rounds_dir"]).is_dir()


def test_optimizer_round_audit_redacts_and_rejects_nonfinite_numeric_evidence(tmp_path: Path):
    module = load_pipeline_module()
    round_prompt = "round prompt"
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=["system_prompt"],
                candidate_prompts={"system_prompt": round_prompt},
                validation_pass_rate=float("nan"),
                metric_breakdown={
                    "finite_metric": 0.5,
                    "infinite_metric": float("inf"),
                    "negative_infinite_metric": float("-inf"),
                },
                accepted=True,
                acceptance_reason="",
                skip_reason=None,
                error_message="optimizer failed: Authorization: Bearer round-secret",
                failed_case_ids=[],
                round_llm_cost=float("inf"),
                round_token_usage={
                    "prompt": float("nan"),
                    "completion": float("inf"),
                    "total": float("-inf"),
                },
                duration_seconds=float("-inf"),
            )
        ],
    )
    record = records[0]

    serialized = json.dumps(records, allow_nan=False)
    assert "round-secret" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    assert record["accepted"] is False
    assert "invalid numeric round evidence" in record["decision_reason"]
    assert record["validation_pass_rate"] == 0.0
    assert record["metric_breakdown"] == {
        "finite_metric": 0.5,
        "infinite_metric": 0.0,
        "negative_infinite_metric": 0.0,
    }
    assert record["cost_usd"] == 0.0
    assert record["duration_seconds"] == 0.0
    assert record["token_usage"] == {"prompt": 0, "completion": 0, "total": 0}
    assert record["prompt_sha256"]["system_prompt"] == module.sha256_text(round_prompt)
    assert Path(record["prompt_paths"]["system_prompt"]).read_text(encoding="utf-8") == round_prompt


def test_optimizer_round_audit_normalizes_nonfinite_round_id(tmp_path: Path):
    module = load_pipeline_module()
    round_prompt = "round prompt"
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=float("nan"),
                optimized_field_names=["system_prompt"],
                candidate_prompts={"system_prompt": round_prompt},
                validation_pass_rate=0.5,
                metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 0.5},
                accepted=True,
                acceptance_reason="improved validation",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, "completion": 2, "total": 10},
                duration_seconds=0.25,
            )
        ],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    assert isinstance(record["round"], int)
    assert record["round"] > 0
    assert record["accepted"] is False
    assert "invalid round identifier" in record["decision_reason"]
    assert record["prompt_sha256"]["system_prompt"] == module.sha256_text(round_prompt)
    assert Path(record["prompt_paths"]["system_prompt"]).read_text(encoding="utf-8") == round_prompt


def test_optimizer_round_audit_rejects_out_of_range_validation_rate(tmp_path: Path):
    module = load_pipeline_module()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=2,
                optimized_field_names=["system_prompt"],
                candidate_prompts={"system_prompt": "round prompt"},
                validation_pass_rate=1.5,
                metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
                accepted=True,
                acceptance_reason="improved validation",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, "completion": 2, "total": 10},
                duration_seconds=0.25,
            )
        ],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    assert 0.0 <= record["validation_pass_rate"] <= 1.0
    assert record["accepted"] is False
    assert "validation_pass_rate" in record["decision_reason"]


def test_optimizer_round_audit_rejects_duplicate_round_ids_without_overwriting_artifacts(
    tmp_path: Path,
):
    module = load_pipeline_module()

    def round_record(prompt: str, reason: str) -> SimpleNamespace:
        return SimpleNamespace(
            round=1,
            optimized_field_names=["system_prompt"],
            candidate_prompts={"system_prompt": prompt},
            validation_pass_rate=1.0,
            metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
            accepted=True,
            acceptance_reason=reason,
            skip_reason=None,
            error_message=None,
            failed_case_ids=[],
            round_llm_cost=0.01,
            round_token_usage={"prompt": 8, "completion": 2, "total": 10},
            duration_seconds=0.25,
        )

    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            round_record("first round prompt", "first round accepted"),
            round_record("duplicate round prompt", "duplicate round accepted"),
        ],
    )

    serialized = json.dumps(records, allow_nan=False)
    assert serialized
    assert [record["round"] for record in records] == [1, 2]
    assert len({record["round"] for record in records}) == 2
    assert records[0]["accepted"] is True
    assert records[0]["decision_reason"] == "first round accepted"
    assert records[1]["accepted"] is False
    assert "duplicate round identifier" in records[1]["decision_reason"]

    prompt_paths = [Path(record["prompt_paths"]["system_prompt"]) for record in records]
    assert len(set(prompt_paths)) == 2
    for record, prompt_path in zip(records, prompt_paths):
        content = prompt_path.read_text(encoding="utf-8")
        assert content == ("first round prompt" if record is records[0] else "duplicate round prompt")
        assert record["prompt_sha256"]["system_prompt"] == module.sha256_text(content)


def test_optimizer_round_audit_normalizes_prompt_keys_without_path_collisions(
    tmp_path: Path,
):
    module = load_pipeline_module()
    round_dir = tmp_path / "prompts" / "optimizer_round_001"
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=["system_prompt"],
                candidate_prompts={
                    "system_prompt": "valid system prompt",
                    "router_prompt": "valid router prompt",
                    "a/../b": "traversal prompt",
                    "a/../system_prompt": "malformed system prompt",
                    "b": "plain prompt",
                },
                validation_pass_rate=1.0,
                metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
                accepted=True,
                acceptance_reason="accepted",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, "completion": 2, "total": 10},
                duration_seconds=0.25,
            )
        ],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    prompt_paths = record["prompt_paths"]
    prompt_hashes = record["prompt_sha256"]
    assert record["accepted"] is False
    assert "prompt artifact key" in record["decision_reason"]
    assert prompt_paths["system_prompt"].endswith("system_prompt.md")
    assert prompt_paths["router_prompt"].endswith("router_prompt.md")
    assert set(prompt_paths) == set(prompt_hashes)
    assert len(prompt_paths) == len(set(prompt_paths)) == len({Path(path) for path in prompt_paths.values()})
    assert Path(prompt_paths["system_prompt"]).read_text(encoding="utf-8") == "valid system prompt"
    assert Path(prompt_paths["router_prompt"]).read_text(encoding="utf-8") == "valid router prompt"

    contents = set()
    for key, path_value in prompt_paths.items():
        prompt_path = Path(path_value)
        assert prompt_path.resolve().is_relative_to(round_dir.resolve())
        content = prompt_path.read_text(encoding="utf-8")
        contents.add(content)
        assert prompt_hashes[key] == module.sha256_text(content)
    assert contents == {
        "valid system prompt",
        "valid router prompt",
        "traversal prompt",
        "malformed system prompt",
        "plain prompt",
    }


@pytest.mark.parametrize("reserved_name", ["CON", "nul.txt", "Com1.json", "LPT9.log"])
def test_optimizer_round_audit_normalizes_reserved_prompt_filenames(
    tmp_path: Path,
    reserved_name: str,
):
    module = load_pipeline_module()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=[],
                candidate_prompts={reserved_name: "prompt"},
                validation_pass_rate=1.0,
                metric_breakdown={},
                accepted=True,
                acceptance_reason="accepted",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.0,
                round_token_usage={"prompt": 0, "completion": 0, "total": 0},
                duration_seconds=0.0,
            )
        ],
    )
    record = records[0]

    assert record["accepted"] is False
    assert "prompt artifact key" in record["decision_reason"]
    assert all(
        Path(path).stem.casefold() != Path(reserved_name).stem.casefold() for path in record["prompt_paths"].values()
    )


@pytest.mark.parametrize("candidate_prompts", [None, ["not a mapping"]])
def test_optimizer_round_audit_normalizes_malformed_prompt_payloads(
    tmp_path: Path,
    candidate_prompts: Any,
):
    module = load_pipeline_module()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[SimpleNamespace(round=1, candidate_prompts=candidate_prompts)],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    assert record["prompt_paths"] == {}
    assert record["prompt_sha256"] == {}
    assert record["accepted"] is False
    assert "candidate_prompts" in record["decision_reason"]


def test_optimizer_round_audit_disambiguates_casefolded_prompt_filenames(
    tmp_path: Path,
):
    module = load_pipeline_module()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=["system_prompt"],
                candidate_prompts={"Prompt": "first", "prompt": "second"},
                validation_pass_rate=1.0,
                metric_breakdown={ROUTE_TOOL_ARGS_METRIC: 1.0},
                accepted=True,
                acceptance_reason="accepted",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, "completion": 2, "total": 10},
                duration_seconds=0.25,
            )
        ],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    paths = [Path(path) for path in record["prompt_paths"].values()]
    assert len(paths) == 2
    assert len({path.name.casefold() for path in paths}) == 2
    assert record["accepted"] is False
    assert "case-insensitive" in record["decision_reason"]
    contents = {path.read_text(encoding="utf-8") for path in paths}
    assert contents == {"first", "second"}
    for key, path_value in record["prompt_paths"].items():
        content = Path(path_value).read_text(encoding="utf-8")
        assert record["prompt_sha256"][key] == module.sha256_text(content)


def test_optimizer_round_audit_drops_non_string_collection_members_and_mapping_keys(
    tmp_path: Path,
):
    module = load_pipeline_module()
    invalid_member = object()
    invalid_metric_key = object()
    invalid_token_key = object()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=["system_prompt", invalid_member, 7],
                candidate_prompts={"system_prompt": "prompt"},
                validation_pass_rate=1.0,
                metric_breakdown={
                    ROUTE_TOOL_ARGS_METRIC: 1.0,
                    invalid_metric_key: 0.5,
                },
                accepted=True,
                acceptance_reason="accepted",
                skip_reason=None,
                error_message=None,
                failed_case_ids=["case_1", invalid_member, 9],
                round_llm_cost=0.01,
                round_token_usage={"prompt": 8, invalid_token_key: 2},
                duration_seconds=0.25,
            )
        ],
    )
    record = records[0]

    json.dumps(records, allow_nan=False)
    assert record["optimized_field_names"] == ["system_prompt"]
    assert record["failed_case_ids"] == ["case_1"]
    assert record["metric_breakdown"] == {ROUTE_TOOL_ARGS_METRIC: 1.0}
    assert record["token_usage"] == {"prompt": 0, "completion": 0, "total": 0}
    assert record["accepted"] is False
    assert "invalid round collections" in record["decision_reason"]
    assert "mapping keys" in record["decision_reason"]


@pytest.mark.parametrize(
    "token_usage",
    [
        None,
        {"prompt": 8},
        {"prompt": 8, "completion": 2, "total": 9},
        {"prompt": -1, "completion": 2, "total": 1},
        {"prompt": 8, "completion": 2, "total": 10, "cached": 3},
        {"prompt": float("nan"), "completion": 2, "total": 10},
    ],
)
def test_optimizer_round_audit_normalizes_malformed_token_usage_to_schema_shape(
    tmp_path: Path,
    token_usage: Any,
):
    module = load_pipeline_module()
    records = module.write_optimizer_round_artifacts(
        run_dir=tmp_path,
        rounds=[
            SimpleNamespace(
                round=1,
                optimized_field_names=[],
                candidate_prompts={},
                validation_pass_rate=1.0,
                metric_breakdown={},
                accepted=True,
                acceptance_reason="accepted",
                skip_reason=None,
                error_message=None,
                failed_case_ids=[],
                round_llm_cost=0.0,
                round_token_usage=token_usage,
                duration_seconds=0.0,
            )
        ],
    )

    assert records[0]["token_usage"] == {"prompt": 0, "completion": 0, "total": 0}
    assert records[0]["accepted"] is False
    assert "token" in records[0]["decision_reason"]
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    report["optimization_rounds"] = records
    module.validate_report_schema(report)


def test_online_cost_audit_distinguishes_optimizer_tokens_from_pipeline_tokens():
    module = load_pipeline_module()
    audit = module.online_cost_audit(
        _optimizer_result({"prompt": 8, "completion": 2, "total": 10}),
        optimizer_candidate_agent_calls=2,
        optimizer_llm_metric_count=0,
        final_revalidation_calls={"agent_calls": 6, "judge_model_calls": 6, "model_calls": 12},
    )

    assert audit["optimizer"]["candidate_evaluation_agent_calls"] == 2
    assert audit["optimizer"]["model_calls"] == 3
    assert audit["optimizer"]["token_usage"] is None
    assert audit["optimizer"]["token_usage_known"] is False
    assert audit["optimizer"]["estimated_cost"] is None
    assert audit["optimizer"]["reflection_reported_usage"] == {
        "estimated_cost": 0.01,
        "token_usage": {"prompt": 8, "completion": 2, "total": 10},
        "token_usage_known": True,
        "unknown_token_usage_reason": None,
    }
    assert audit["final_revalidation"]["token_usage"] is None
    assert audit["final_revalidation"]["token_usage_known"] is False
    assert audit["token_usage"] is None
    assert audit["token_usage_known"] is False
    assert audit["model_calls"] == 15


def test_online_cost_audit_rejects_noninteger_native_call_counters():
    module = load_pipeline_module()
    result = _optimizer_result({"prompt": 8, "completion": 2, "total": 10})
    result.total_reflection_lm_calls = 1.0

    audit = module.online_cost_audit(
        result,
        optimizer_candidate_agent_calls=0,
        optimizer_llm_metric_count=0,
        final_revalidation_calls={"agent_calls": 0, "judge_model_calls": 0, "model_calls": 0},
    )

    assert audit["optimizer"]["reflection_lm_calls"] == 0
    assert audit["optimizer"]["usage_evidence_valid"] is False
    assert audit["optimizer"]["token_usage_known"] is False
    assert audit["optimizer"]["token_usage"] is None


def test_online_cost_audit_reconciles_native_and_derived_optimizer_judge_calls():
    module = load_pipeline_module()
    result = _optimizer_result({"prompt": 8, "completion": 2, "total": 10})
    result.total_judge_model_calls = 3

    audit = module.online_cost_audit(
        result,
        optimizer_candidate_agent_calls=2,
        optimizer_llm_metric_count=1,
        final_revalidation_calls={"agent_calls": 0, "judge_model_calls": 0, "model_calls": 0},
    )
    optimizer = audit["optimizer"]

    assert optimizer["native_judge_model_calls"] == 3
    assert optimizer["derived_judge_model_calls"] == 2
    assert optimizer["judge_model_calls"] == 3
    assert optimizer["judge_model_call_source"] == "reconciled_native_and_derived_max"
    assert optimizer["model_calls"] == 6
    assert audit["model_calls"] == 6


def test_judge_call_accounting_includes_each_model_sample_and_eval_run():
    module = load_pipeline_module()
    metrics_config = {
        "metrics": [
            {
                "metric_name": "llm_multi_judge",
                "threshold": 0.5,
                "criterion": {
                    "llm_judge": {
                        "judge_models": [
                            {"model_name": "judge-a", "num_samples": 2},
                            {"model_name": "judge-b", "num_samples": 3},
                        ]
                    }
                },
            }
        ],
        "num_runs": 2,
    }
    summary = {"case_results": [{"case_id": "a"}, {"case_id": "b"}]}

    assert module.judge_calls_per_agent_call(metrics_config) == 5
    final = module.final_revalidation_call_audit([summary], metrics_config)
    assert final == {
        "agent_calls_per_run": 2,
        "agent_calls": 4,
        "judge_calls_per_agent_call": 5,
        "judge_model_calls": 20,
        "model_calls": 24,
    }
    audit = module.online_cost_audit(
        _optimizer_result({"prompt": 8, "completion": 2, "total": 10}),
        optimizer_candidate_agent_calls=2,
        optimizer_judge_calls_per_agent_call=5,
        final_revalidation_calls=final,
    )
    assert audit["optimizer"]["judge_calls_per_candidate_evaluation"] == 5
    assert audit["optimizer"]["derived_judge_model_calls"] == 10
    assert audit["optimizer"]["judge_model_calls"] == 10


def test_final_revalidation_call_audit_counts_every_conversation_turn():
    module = load_pipeline_module()
    metrics_config = {
        "metrics": [
            {
                "metric_name": "llm_multi_judge",
                "threshold": 0.5,
                "criterion": {
                    "llm_judge": {
                        "judge_models": [
                            {"model_name": "judge-a", "num_samples": 2},
                            {"model_name": "judge-b", "num_samples": 3},
                        ]
                    }
                },
            }
        ],
        "num_runs": 1,
    }
    summaries = [{"case_results": [{"case_id": "a"}]}]
    evalset_payloads = [
        {
            "eval_cases": [
                {
                    "eval_id": "a",
                    "conversation": [
                        {"user_content": {}, "final_response": {}},
                        {"user_content": {}, "final_response": {}},
                    ],
                }
            ]
        }
    ]

    audit = module.final_revalidation_call_audit(
        summaries,
        metrics_config,
        evalset_payloads=evalset_payloads,
    )
    assert audit == {
        "agent_calls_per_run": 2,
        "agent_calls": 2,
        "judge_calls_per_agent_call": 5,
        "judge_model_calls": 10,
        "model_calls": 12,
    }


def test_report_semantics_recompute_derived_judge_calls_from_recorded_multiplier():
    module = load_pipeline_module()
    report = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")
    optimizer = report["cost"]["optimizer"]
    optimizer["candidate_evaluation_agent_calls"] = 2
    optimizer["judge_calls_per_candidate_evaluation"] = 2

    with pytest.raises(ValidationError, match="derived judge|offline reports"):
        module.validate_report_schema(report)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include_llm_metric", "expected_judge_calls", "expected_optimizer_calls", "expected_source"),
    [
        (False, 0, 3, "none"),
        (True, 2, 5, "derived_from_candidate_calls_and_llm_metrics"),
    ],
)
async def test_run_online_accounts_for_optimizer_internal_llm_metric_judges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_llm_metric: bool,
    expected_judge_calls: int,
    expected_optimizer_calls: int,
    expected_source: str,
):
    module = load_pipeline_module()
    metrics = load_report(EXAMPLE_DIR / "optimizer.json")["evaluate"]["metrics"]
    optimizer_config = _write_optimizer_config(
        tmp_path,
        metrics=metrics if include_llm_metric else metrics[:1],
    )
    report = await _run_stubbed_online(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        result=_optimizer_result({"prompt": 8, "completion": 2, "total": 10}),
        run_id=f"optimizer_judges_{include_llm_metric}",
        optimizer_agent_calls=2,
        optimizer_config=optimizer_config,
    )
    optimizer = report["cost"]["optimizer"]

    assert optimizer["derived_judge_model_calls"] == expected_judge_calls
    assert optimizer["judge_model_calls"] == expected_judge_calls
    assert optimizer["judge_model_call_source"] == expected_source
    assert optimizer["model_calls"] == expected_optimizer_calls
    assert report["cost"]["model_calls"] == (
        expected_optimizer_calls + report["cost"]["final_revalidation"]["model_calls"]
    )
    module.validate_report_schema(report)


@pytest.mark.asyncio
async def test_online_optimizer_candidate_agent_calls_are_instrumented(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    report = await _run_stubbed_online(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        result=_optimizer_result({"prompt": 8, "completion": 2, "total": 10}),
        run_id="counted_optimizer_agent_calls",
        gate_config={"max_cost_usd": 1.0, "required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        optimizer_agent_calls=2,
    )

    optimizer = report["cost"]["optimizer"]
    assert optimizer["candidate_evaluation_agent_calls"] == 2
    assert optimizer["derived_judge_model_calls"] == 2
    assert optimizer["model_calls"] == 5
    assert optimizer["token_usage_known"] is False
    assert report["cost"]["model_calls"] == (
        optimizer["model_calls"] + report["cost"]["final_revalidation"]["model_calls"]
    )
    assert report["gate_decision"]["accepted"] is False
    module.validate_report_schema(report)


@pytest.mark.asyncio
async def test_online_malformed_optimizer_usage_writes_rejected_schema_valid_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    report = await _run_stubbed_online(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        result=_optimizer_result({"prompt": "invalid", "completion": 2, "total": 2}),
        run_id="malformed_optimizer_usage",
    )

    assert report["cost"]["optimizer"]["token_usage"] is None
    assert report["cost"]["optimizer"]["reflection_reported_usage"]["token_usage"] == {
        "prompt": 0,
        "completion": 0,
        "total": 0,
    }
    assert report["cost"]["optimizer"]["token_usage_known"] is False
    assert report["gate_decision"]["accepted"] is False
    assert "usage evidence" in " ".join(report["gate_decision"]["reasons"])
    module.validate_report_schema(report)


@pytest.mark.asyncio
async def test_online_duration_gate_uses_total_elapsed_and_audits_revalidation_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    module = load_pipeline_module()
    clock = iter([0.0, 40.0, 60.0, 90.0, 91.0])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(clock))
    report = await _run_stubbed_online(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        result=_optimizer_result({"prompt": 8, "completion": 2, "total": 10}),
        run_id="online_total_duration",
        gate_config={
            "max_duration_seconds": 80.0,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
    )
    candidate = report["candidates"][0]

    assert report["gate_decision"]["accepted"] is False
    assert "90.00s > 80.00s" in " ".join(candidate["gate"]["reasons"])
    assert candidate["audit"]["duration_seconds"] == 30.0
    assert report["online_duration"] == {
        "optimization_seconds": 40.0,
        "baseline_revalidation_seconds": 20.0,
        "candidate_revalidation_seconds": 30.0,
        "gate_elapsed_seconds": 90.0,
    }
    assert report["duration_seconds"] == 91.0
    module.validate_report_schema(report)


@pytest.mark.asyncio
async def test_online_optimizer_validation_improvement_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")
    module = load_pipeline_module()

    class FakeResult:
        status = "SUCCEEDED"
        baseline_pass_rate = 0.5
        best_pass_rate = 1.0
        pass_rate_improvement = 0.5
        stop_reason = "required_metrics_passing"
        total_llm_cost = 0.0
        total_reflection_lm_calls = 0
        total_judge_model_calls = 0
        best_prompts = {"system_prompt": "better system", "router_prompt": "better router"}
        baseline_prompts = {"system_prompt": "baseline system", "router_prompt": "baseline router"}
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 0, "completion": 0, "total": 0}

    async def fake_optimize(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.txt").write_text("fake", encoding="utf-8")
        return FakeResult()

    def summary(score: float, passed: bool) -> dict[str, Any]:
        metrics = {
            ROUTE_TOOL_ARGS_METRIC: {
                "score": score,
                "threshold": 1.0,
                "passed": passed,
                "status": "passed" if passed else "failed",
            },
            "llm_rubric_response": {
                "score": 1.0,
                "threshold": 0.66,
                "passed": True,
                "status": "passed",
            },
        }
        return {
            "eval_set_id": "fake",
            "score": score,
            "pass_rate": 1.0 if passed else 0.0,
            "metrics": copy.deepcopy(metrics),
            "case_results": [
                {
                    "case_id": "case_1",
                    "tags": [],
                    "user": "test user",
                    "score": score,
                    "passed": passed,
                    "metrics": copy.deepcopy(metrics),
                    "actual_text": "",
                    "expected_text": "",
                    "key_trace": {
                        "invocation_id": "case_1",
                        "actual_final_response": "",
                        "expected_final_response": "",
                        "error_message": None,
                    },
                    "root_cause": "" if passed else "metric_failed",
                    "reasons": [] if passed else ["stubbed metric failure"],
                },
            ],
            "failed_case_ids": [] if passed else ["case_1"],
            "source": "AgentEvaluator",
        }

    summaries = iter(
        [
            summary(0.5, False),
            summary(0.5, False),
            summary(0.5, False),
            summary(1.0, True),
            summary(1.0, True),
            summary(1.0, True),
        ]
    )

    async def fake_run_evaluator(**_: Any) -> dict[str, Any]:
        return next(summaries)

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    monkeypatch.setattr(module, "run_evaluator", fake_run_evaluator)

    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_improved")
    report = load_report(run_dir / "optimization_report.json")

    assert report["gate_decision"]["accepted"] is True
    assert report["gate_decision"]["winner"] == "optimizer_best"
    module.validate_report_schema(report)


@pytest.mark.asyncio
async def test_online_optimizer_no_improvement_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")

    module = load_pipeline_module()
    before_system = (EXAMPLE_DIR / "agent" / "prompts" / "system.md").read_text(encoding="utf-8")
    before_router = (EXAMPLE_DIR / "agent" / "prompts" / "router.md").read_text(encoding="utf-8")

    class FakeResult:
        status = "SUCCEEDED"
        baseline_pass_rate = 0.5
        best_pass_rate = 0.5
        pass_rate_improvement = 0.0
        stop_reason = "no_improvement"
        total_llm_cost = 0.0
        total_reflection_lm_calls = 0
        total_judge_model_calls = 0
        best_prompts = {
            "system_prompt": "changed system",
            "router_prompt": "changed router",
        }
        baseline_prompts = {
            "system_prompt": "baseline system",
            "router_prompt": "baseline router",
        }
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 0, "completion": 0, "total": 0}

    async def fake_optimize(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.txt").write_text("fake", encoding="utf-8")
        return FakeResult()

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    patch_agent_evaluator(monkeypatch, score=0.5, passed=False)

    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_no_improvement")
    report = load_report(run_dir / "optimization_report.json")

    assert report["gate_decision"]["accepted"] is False
    assert report["gate_decision"]["winner"] is None
    assert "validation score did not improve" in " ".join(report["gate_decision"]["reasons"])
    assert (EXAMPLE_DIR / "agent" / "prompts" / "system.md").read_text(encoding="utf-8") == before_system
    assert (EXAMPLE_DIR / "agent" / "prompts" / "router.md").read_text(encoding="utf-8") == before_router


@pytest.mark.asyncio
async def test_online_revalidation_uses_eval_metrics_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")
    module = load_pipeline_module()
    metrics_paths: list[str] = []

    class FakeResult:
        status = "SUCCEEDED"
        baseline_pass_rate = 0.5
        best_pass_rate = 1.0
        pass_rate_improvement = 0.5
        stop_reason = "completed"
        total_llm_cost = 0.0
        total_reflection_lm_calls = 0
        total_judge_model_calls = 0
        best_prompts = {"system_prompt": "fake system", "router_prompt": "fake router"}
        baseline_prompts = {"system_prompt": "baseline system", "router_prompt": "baseline router"}
        baseline_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 0.5}
        best_metric_breakdown = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        metric_thresholds = {ROUTE_TOOL_ARGS_METRIC: 1.0}
        duration_seconds = 0.01
        total_token_usage = {"prompt": 0, "completion": 0, "total": 0}

    async def fake_optimize(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        (output_dir / "summary.txt").write_text("fake", encoding="utf-8")
        return FakeResult()

    class FakeExecuter:
        def __init__(self, eval_set_path: str) -> None:
            self.eval_set_path = Path(eval_set_path)
            self.result = None

        async def evaluate(self) -> None:
            self.result = make_evaluate_result(self.eval_set_path)

        def get_result(self):
            return self.result

    def fake_get_executer(eval_dataset_file_path_or_dir: str, **kwargs: Any) -> FakeExecuter:
        metrics_paths.append(str(kwargs["eval_metrics_file_path_or_dir"]))
        return FakeExecuter(eval_dataset_file_path_or_dir)

    import trpc_agent_sdk.evaluation as evaluation_pkg

    monkeypatch.setattr(evaluation_pkg.AgentOptimizer, "optimize", staticmethod(fake_optimize))
    monkeypatch.setattr(evaluation_pkg.AgentEvaluator, "get_executer", staticmethod(fake_get_executer))

    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_metrics_snapshot")
    report = load_report(run_dir / "optimization_report.json")

    assert metrics_paths
    assert all(path.endswith("online_eval_metrics.json") for path in metrics_paths)
    assert not any(path.endswith("optimizer.json") for path in metrics_paths)
    assert Path(report["artifacts"]["online_eval_metrics"]).is_file()


@pytest.mark.skipif(os.getenv("RUN_ONLINE_E2E") != "1", reason="online smoke is opt-in")
def test_online_e2e_smoke_with_real_api(tmp_path: Path):
    required = ["TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip("missing online env vars: " + ", ".join(missing))

    source_prompts = {
        path: path.read_text(encoding="utf-8")
        for path in (
            EXAMPLE_DIR / "agent" / "prompts" / "system.md",
            EXAMPLE_DIR / "agent" / "prompts" / "router.md",
        )
    }
    weak_system = tmp_path / "weak_system.md"
    weak_system.write_text(source_prompts[EXAMPLE_DIR / "agent" / "prompts" / "system.md"], encoding="utf-8")
    weak_router = tmp_path / "weak_router.md"
    weak_router.write_text(
        "\n".join(
            [
                "You route customer-support requests to one backend action.",
                "Output exactly one JSON object with keys route, tool, and reason.",
                "Allowed tools: create_refund_ticket, create_escalation_case, none.",
                "Baseline v0 policy:",
                "1. Prefer faq for refund requests unless the user says the refund was already approved.",
                "2. Prefer faq for account or legal complaints unless the user uses the exact phrase human agent.",
                "3. Use faq for shipping, coupon, address, and policy questions.",
                "4. Keep tool.arguments as an empty object.",
            ]
        ),
        encoding="utf-8",
    )
    before_weak_system = weak_system.read_text(encoding="utf-8")
    before_weak_router = weak_router.read_text(encoding="utf-8")
    gate_config = tmp_path / "online_gate.json"
    gate_config.write_text(
        json.dumps({"max_duration_seconds": 300}),
        encoding="utf-8",
    )

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(RUN_PIPELINE),
                "--mode",
                "online",
                "--output-dir",
                str(tmp_path),
                "--run-id",
                "online_e2e",
                "--system-prompt",
                str(weak_system),
                "--router-prompt",
                str(weak_router),
                "--gate-config",
                str(gate_config),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        run_dir = Path(proc.stdout.strip().splitlines()[-1])
        report = load_report(run_dir / "optimization_report.json")
        report_markdown = (run_dir / "optimization_report.md").read_text(encoding="utf-8")
        serialized_outputs = proc.stdout + proc.stderr + json.dumps(report) + report_markdown

        assert report["mode"] == "online"
        assert report["baseline"]["validation"]["score"] < report["candidates"][0]["validation"]["score"]
        assert report["gate_decision"]["accepted"] is True
        assert weak_system.read_text(encoding="utf-8") == before_weak_system
        assert weak_router.read_text(encoding="utf-8") == before_weak_router
        assert all(
            artifact["source_written"] is False
            for candidate in [report["baseline"], *report["candidates"]]
            for artifact in candidate["prompt_artifacts"]
        )
        load_pipeline_module().validate_report_schema(report)
        assert report["online_preflight"] == {
            "TRPC_AGENT_API_KEY": True,
            "TRPC_AGENT_BASE_URL": True,
            "TRPC_AGENT_MODEL_NAME": True,
        }
        assert os.environ["TRPC_AGENT_API_KEY"] not in serialized_outputs
    finally:
        assert {path: path.read_text(encoding="utf-8") for path in source_prompts} == source_prompts


@pytest.mark.skipif(os.getenv("RUN_ONLINE_E2E") != "1", reason="online rejection smoke is opt-in")
def test_online_e2e_rejects_perfect_default_prompts(tmp_path: Path):
    required = ["TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip("missing online env vars: " + ", ".join(missing))

    source_prompts = {
        path: path.read_text(encoding="utf-8")
        for path in (
            EXAMPLE_DIR / "agent" / "prompts" / "system.md",
            EXAMPLE_DIR / "agent" / "prompts" / "router.md",
        )
    }
    gate_config = tmp_path / "online_gate.json"
    gate_config.write_text(json.dumps({"max_duration_seconds": 300}), encoding="utf-8")

    def run_default(run_id: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        proc = subprocess.run(
            [
                sys.executable,
                str(RUN_PIPELINE),
                "--mode",
                "online",
                "--output-dir",
                str(tmp_path),
                "--run-id",
                run_id,
                "--gate-config",
                str(gate_config),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        run_dir = Path(proc.stdout.strip().splitlines()[-1])
        return proc, load_report(run_dir / "optimization_report.json")

    try:
        procs = []
        proc, report = run_default("online_e2e_default_reject")
        procs.append(proc)
        reports = [report]
        if report["baseline"]["validation"]["score"] != 1.0:
            proc, report = run_default("online_e2e_default_reject_retry")
            procs.append(proc)
            reports.append(report)

        candidate = report["candidates"][0]
        serialized_outputs = "".join(proc.stdout + proc.stderr for proc in procs) + "".join(
            json.dumps(run_report)
            + (tmp_path / run_report["run_id"] / "optimization_report.md").read_text(encoding="utf-8")
            for run_report in reports
        )

        assert report["baseline"]["validation"]["score"] == 1.0
        assert candidate["validation"]["score"] == 1.0
        assert report["gate_decision"]["accepted"] is False
        assert any(
            "validation score did not improve over baseline" in reason for reason in report["gate_decision"]["reasons"]
        )
        assert all(
            artifact["source_written"] is False
            for candidate_report in [report["baseline"], *report["candidates"]]
            for artifact in candidate_report["prompt_artifacts"]
        )
        load_pipeline_module().validate_report_schema(report)
        assert os.environ["TRPC_AGENT_API_KEY"] not in serialized_outputs
    finally:
        assert {path: path.read_text(encoding="utf-8") for path in source_prompts} == source_prompts
