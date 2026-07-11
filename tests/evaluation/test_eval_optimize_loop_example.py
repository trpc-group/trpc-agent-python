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
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    extra_case["case_results"].append(
        {"case_id": "c", "score": 1.0, "passed": True, "tags": []}
    )
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
            {"case_id": "new_fail", "score": 0.0, "passed": False, "actual_text": "c2", "root_cause": "format_error", "reasons": ["bad"]},
            {"case_id": "up", "score": 0.6, "passed": True, "actual_text": "c3", "root_cause": "", "reasons": []},
            {"case_id": "down", "score": 0.6, "passed": True, "actual_text": "c4", "root_cause": "", "reasons": []},
            {"case_id": "same", "score": 1.0, "passed": True, "actual_text": "c5", "root_cause": "", "reasons": []},
        ]
    }

    by_id = {
        item["case_id"]: item
        for item in module.build_case_deltas(baseline, candidate)
    }

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
    actual_invocation = SimpleNamespace(final_response={
        "parts": [
            {"text": "internal chain of thought", "thought": True},
            {"text": visible_final, "thought": False},
        ]
    })
    expected_invocation = SimpleNamespace(final_response={
        "parts": [{"text": visible_final, "thought": False}]
    })
    secret = "ASIA_SECRET_SESSION_TOKEN"
    run = SimpleNamespace(
        eval_metric_result_per_invocation=[SimpleNamespace(
            actual_invocation=actual_invocation,
            expected_invocation=expected_invocation,
        )],
        final_eval_status="failed",
        error_message=f"request failed: X-Amz-Security-Token: {secret}; retry later",
        overall_eval_metric_results=[
            SimpleNamespace(
                metric_name="provider_metric",
                score=0.0,
                eval_status="failed",
                details=SimpleNamespace(
                    reason=f"provider headers: X-Amz-Security-Token: {secret}"
                ),
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
    result = SimpleNamespace(results_by_eval_set_id={
        payload["eval_set_id"]: SimpleNamespace(
            eval_results_by_eval_id={case["eval_id"]: [run]},
        )
    })

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
    result = SimpleNamespace(results_by_eval_set_id={
        payload["eval_set_id"]: SimpleNamespace(
            eval_results_by_eval_id={case["eval_id"]: []},
        )
    })

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
        [{
            "case_id": "a",
            "score": 0.0,
            "passed": False,
            "tags": [],
            "reasons": [float("nan")],
        }],
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
    assert {case.eval_id for case in optimizer_dev.eval_cases}.isdisjoint(
        {case.eval_id for case in val.eval_cases}
    )

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


def test_sample_report_validates_against_schema_and_required_fields_are_enforced():
    module = load_pipeline_module()
    sample = load_report(EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json")

    module.validate_report_schema(sample)

    broken = dict(sample)
    broken.pop("environment_snapshot", None)
    with pytest.raises(ValidationError):
        module.validate_report_schema(broken)


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
        candidate
        for candidate in report["candidates"]
        if candidate["id"] == report["gate_decision"]["winner"]
    )
    rejected = next(
        candidate
        for candidate in report["candidates"]
        if candidate["id"] == "candidate_overfit"
    )
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
        expected_text=(
            '{"route":"faq","tool":{"name":"none","arguments":{}},'
            '"reason":"expected"}'
        ),
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
    run_dir = await module.run_online(seed=7, output_dir=tmp_path, run_id="online_wiring")

    assert captured["config_path"].endswith("optimizer.json")
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
    _assert_candidate_audit(report["candidates"][0], 7)
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
    assert report["cost"]["optimizer"]["token_usage"]["total"] == 10
    assert report["cost"]["final_revalidation"]["model_calls"] > 0


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

    prompt_paths = [
        Path(record["prompt_paths"]["system_prompt"])
        for record in records
    ]
    assert len(set(prompt_paths)) == 2
    for record, prompt_path in zip(records, prompt_paths):
        content = prompt_path.read_text(encoding="utf-8")
        assert content == ("first round prompt" if record is records[0] else "duplicate round prompt")
        assert record["prompt_sha256"]["system_prompt"] == module.sha256_text(content)


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
        return {
            "eval_set_id": "fake",
            "score": score,
            "pass_rate": 1.0 if passed else 0.5,
            "metrics": {
                ROUTE_TOOL_ARGS_METRIC: {"score": score, "threshold": 1.0, "passed": passed, "status": "passed" if passed else "failed"},
                "llm_rubric_response": {"score": 1.0, "threshold": 0.66, "passed": True, "status": "passed"},
            },
            "case_results": [
                {"case_id": "case_1", "score": score, "passed": passed, "tags": [], "metrics": {}, "root_cause": "", "reasons": []},
            ],
            "failed_case_ids": [] if passed else ["case_1"],
            "source": "AgentEvaluator",
        }

    summaries = iter([
        summary(0.5, False),
        summary(0.5, False),
        summary(0.5, False),
        summary(1.0, True),
        summary(1.0, True),
        summary(1.0, True),
    ])

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

    weak_router = tmp_path / "weak_router.md"
    weak_router.write_text(
        "\n".join([
            "You route customer-support requests to one backend action.",
            "Output exactly one JSON object with keys route, tool, and reason.",
            "Allowed tools: create_refund_ticket, create_escalation_case, none.",
            "Baseline v0 policy:",
            "1. Prefer faq for refund requests unless the user says the refund was already approved.",
            "2. Prefer faq for account or legal complaints unless the user uses the exact phrase human agent.",
            "3. Use faq for shipping, coupon, address, and policy questions.",
            "4. Keep tool.arguments as an empty object.",
        ]),
        encoding="utf-8",
    )
    before_weak_router = weak_router.read_text(encoding="utf-8")
    gate_config = tmp_path / "online_gate.json"
    gate_config.write_text(
        json.dumps({"max_duration_seconds": 300}),
        encoding="utf-8",
    )

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

    assert report["mode"] == "online"
    assert report["gate_decision"]["accepted"] is True
    assert (run_dir / "optimization_report.md").is_file()
    assert "DeepSeek only supports JSON object response_format" not in proc.stderr
    assert "SSEDecoder._aiter_chunks" not in proc.stderr
    assert weak_router.read_text(encoding="utf-8") == before_weak_router
    load_pipeline_module().validate_report_schema(report)
    assert report["online_preflight"] == {
        "TRPC_AGENT_API_KEY": True,
        "TRPC_AGENT_BASE_URL": True,
        "TRPC_AGENT_MODEL_NAME": True,
    }
