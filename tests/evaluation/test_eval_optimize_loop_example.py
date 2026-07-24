# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Regression test for the eval_optimize_loop optimization example."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import types
from importlib import util
from pathlib import Path

import pytest


def _load_pipeline_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "examples" / "optimization" / "eval_optimize_loop" / "run_pipeline.py"
    spec = util.spec_from_file_location("eval_optimize_loop_run_pipeline", module_path)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_example(tmp_path: Path, *, mode: str = "fake", scenario: str = "overfit") -> tuple[Path, dict]:
    repo_root = Path(__file__).resolve().parents[2]
    example_dir = repo_root / "examples" / "optimization" / "eval_optimize_loop"
    output_dir = tmp_path / f"eval_optimize_loop_{mode}_{scenario}"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env.pop("TRPC_AGENT_API_KEY", None)
    env.pop("TRPC_AGENT_BASE_URL", None)
    env.pop("TRPC_AGENT_MODEL_NAME", None)

    subprocess.run(
        [
            sys.executable,
            str(example_dir / "run_pipeline.py"),
            "--mode",
            mode,
            "--scenario",
            scenario,
            "--output-dir",
            str(output_dir),
        ],
        cwd=str(example_dir),
        env=env,
        check=True,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return output_dir, json.loads((output_dir / "optimization_report.json").read_text(encoding="utf-8"))


def test_eval_optimize_loop_fake_mode_generates_rejected_report(tmp_path: Path) -> None:
    output_dir, report = _run_example(tmp_path, mode="fake", scenario="overfit")
    assert report["gate"]["decision"] == "rejected"
    assert report["gate"]["overfitting_guard_triggered"] is True
    assert report["delta"]["train_score_delta"] > 0
    assert report["delta"]["validation_score_delta"] <= 0
    assert any(not check["passed"] for check in report["gate"]["checks"])
    assert len(report["baseline"]["train"]["cases"]) == 5
    assert len(report["baseline"]["validation"]["cases"]) == 5
    assert any(delta["outcome"] == "new_pass" for delta in report["delta"]["validation_case_deltas"])
    assert any(delta["outcome"] == "new_fail" for delta in report["delta"]["validation_case_deltas"])
    assert len(report["optimization_rounds"]) >= 2
    assert report["optimization_rounds"][0]["kind"] == "baseline"
    assert report["optimization_rounds"][0]["duration_seconds"] == 0.0
    assert report["optimization_rounds"][0]["evaluation_results"]["validation"]["total"] == 5
    assert report["optimization_rounds"][1]["evaluation_result_refs"]["validation"] == "eval_validation_candidate"
    assert report["optimization_rounds"][1]["candidate_prompts"]["system_prompt"]
    assert set(report["prompt_audit"]) == {"router_prompt", "system_prompt", "skill_prompt"}
    assert set(report["optimization_rounds"][1]["candidate_prompts"]) == {
        "router_prompt",
        "system_prompt",
        "skill_prompt",
    }
    assert report["inputs"]["case_meta"] == "case_meta.json"
    assert set(report["input_audit"]["files"]) == {
        "train_evalset",
        "validation_evalset",
        "optimizer_config",
        "case_meta",
    }
    assert report["input_audit"]["files"]["train_evalset"]["sha256"]
    assert report["failure_attribution"]["self_check"]["accuracy"] >= 0.75
    assert {
        "final_response_mismatch",
        "tool_call_error",
        "parameter_error",
        "llm_rubric_not_met",
        "knowledge_recall_insufficient",
        "format_violation",
    }.issubset(report["failure_attribution"]["stats"])
    assert all(
        case["failure_types"]
        for split in ("train", "validation")
        for case in report["baseline"][split]["cases"]
        if not case["passed"]
    )
    assert not str(report["inputs"]["train_evalset"]).startswith(str(Path(__file__).resolve().parents[2]))
    assert report["prompt_audit"]["router_prompt"]["diff"]["line_count"] > 0
    assert report["prompt_audit"]["router_prompt"]["diff"]["preview"]
    assert (output_dir / "optimization_report.md").exists()


def test_eval_optimize_loop_fake_mode_can_accept_good_candidate(tmp_path: Path) -> None:
    _, report = _run_example(tmp_path, mode="fake", scenario="accepted")
    assert report["gate"]["decision"] == "accepted"
    assert report["gate"]["overfitting_guard_triggered"] is False
    assert report["delta"]["train_score_delta"] > 0
    assert report["delta"]["validation_score_delta"] > 0
    assert all(check["passed"] for check in report["gate"]["checks"])
    assert all(delta["outcome"] != "new_fail" for delta in report["delta"]["validation_case_deltas"])


def test_eval_optimize_loop_fake_mode_rejects_costly_candidate(tmp_path: Path) -> None:
    _, report = _run_example(tmp_path, mode="fake", scenario="cost_exceeded")
    assert report["gate"]["decision"] == "rejected"
    assert report["delta"]["validation_score_delta"] > 0
    checks = {check["name"]: check for check in report["gate"]["checks"]}
    assert checks["max_cost_usd"]["passed"] is False
    assert checks["min_validation_score_delta"]["passed"] is True
    assert report["audit"]["cost"]["estimated_usd"] > report["audit"]["cost"]["max_budget_usd"]
    assert report["audit"]["cost"]["total_usd"] == report["audit"]["cost"]["estimated_usd"]
    assert report["audit"]["cost"]["optimizer_usd"] > 0


def test_eval_optimize_loop_trace_mode_replays_actual_conversation(tmp_path: Path) -> None:
    output_dir, report = _run_example(tmp_path, mode="trace", scenario="overfit")
    assert report["run"]["mode"] == "trace"
    assert report["inputs"]["trace_evalsets"]
    trace_dir = output_dir / "trace_evalsets"
    trace_file = trace_dir / "validation_candidate.evalset.json"
    payload = json.loads(trace_file.read_text(encoding="utf-8"))
    assert payload["eval_cases"][0]["eval_mode"] == "trace"
    assert payload["eval_cases"][0]["actual_conversation"]
    assert report["gate"]["decision"] == "rejected"


def test_eval_optimize_loop_input_validation_contract() -> None:
    pipeline = _load_pipeline_module()
    config = json.loads((pipeline.CONFIG_PATH).read_text(encoding="utf-8"))
    pipeline.validate_inputs(config)

    missing_gate_key = copy.deepcopy(config)
    del missing_gate_key["gate"]["max_cost_usd"]
    with pytest.raises(ValueError, match="max_cost_usd"):
        pipeline.validate_inputs(missing_gate_key)

    unknown_critical = copy.deepcopy(config)
    unknown_critical["gate"]["critical_case_ids"] = ["not_in_validation"]
    with pytest.raises(ValueError, match="critical_case_ids"):
        pipeline.validate_inputs(unknown_critical)

    same_evalset = copy.deepcopy(config)
    with pytest.raises(ValueError, match="different files"):
        pipeline.validate_inputs(
            same_evalset,
            train_path=pipeline.TRAIN_PATH,
            val_path=pipeline.TRAIN_PATH,
        )


def test_eval_optimize_loop_ci_exit_code_contract(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    example_dir = repo_root / "examples" / "optimization" / "eval_optimize_loop"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env.pop("TRPC_AGENT_API_KEY", None)
    env.pop("TRPC_AGENT_BASE_URL", None)
    env.pop("TRPC_AGENT_MODEL_NAME", None)

    accepted_dir = tmp_path / "ci_accepted"
    accepted = subprocess.run(
        [
            sys.executable,
            str(example_dir / "run_pipeline.py"),
            "--mode",
            "fake",
            "--scenario",
            "accepted",
            "--output-dir",
            str(accepted_dir),
            "--ci-exit-code",
        ],
        cwd=str(example_dir),
        env=env,
        check=False,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert accepted.returncode == 0
    assert (accepted_dir / "optimization_report.json").exists()

    rejected_dir = tmp_path / "ci_rejected"
    rejected = subprocess.run(
        [
            sys.executable,
            str(example_dir / "run_pipeline.py"),
            "--mode",
            "fake",
            "--scenario",
            "overfit",
            "--output-dir",
            str(rejected_dir),
            "--ci-exit-code",
        ],
        cwd=str(example_dir),
        env=env,
        check=False,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert rejected.returncode == 1
    assert (rejected_dir / "optimization_report.json").exists()


def test_eval_optimize_loop_optimizer_mode_can_be_mocked_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _load_pipeline_module()
    captured: dict[str, object] = {}

    async def fake_optimize(**kwargs):
        target = kwargs["target_prompt"]
        baseline_prompts = await target.read_all()
        captured["field_names"] = set(baseline_prompts)
        candidate_prompts = dict(baseline_prompts)
        candidate_prompts["router_prompt"] = baseline_prompts["router_prompt"].rstrip() + """

Mock optimizer routing update:
- Route double charge and VIP refund requests to billing handling.
- Preserve production outage handling as technical p1 escalation.
"""
        candidate_prompts["system_prompt"] = baseline_prompts["system_prompt"].rstrip() + """

Mock optimizer system update:
- Keep the response as compact JSON.
"""
        candidate_prompts["skill_prompt"] = baseline_prompts["skill_prompt"].rstrip() + """

Mock optimizer skill update:
- Treat double charge and VIP refund requests as billing issues.
- For refund requests, choose action refund_review.
- For VIP refund requests, use priority p1.
"""
        return types.SimpleNamespace(
            best_prompts=candidate_prompts,
            status="completed",
            finish_reason="mocked_for_example_test",
            total_llm_cost=0.001,
            total_token_usage={"prompt": 10, "completion": 5, "total": 15},
            rounds=[
                types.SimpleNamespace(
                    round=1,
                    kind="mock_optimizer",
                    optimized_field_names=["router_prompt", "system_prompt", "skill_prompt"],
                    candidate_prompts=candidate_prompts,
                    validation_pass_rate=1.0,
                    metric_breakdown={"final_response_avg_score": 1.0},
                    accepted=True,
                    acceptance_reason="mock optimizer candidate accepted by adapter",
                    failed_case_ids=[],
                    round_llm_cost=0.001,
                    duration_seconds=0.01,
                )
            ],
        )

    monkeypatch.setattr(pipeline.AgentOptimizer, "optimize", fake_optimize)
    output_dir = tmp_path / "optimizer_mock"
    args = types.SimpleNamespace(
        mode="optimizer",
        scenario="overfit",
        output_dir=str(output_dir),
        update_sample_outputs=False,
        ci_exit_code=False,
    )

    result_dir = asyncio.run(pipeline.run_pipeline(args))
    report = json.loads((result_dir / "optimization_report.json").read_text(encoding="utf-8"))
    assert captured["field_names"] == {"router_prompt", "system_prompt", "skill_prompt"}
    assert report["run"]["mode"] == "optimizer"
    assert report["candidate"]["candidate_id"] == "agent_optimizer_best"
    assert report["audit"]["cost"]["optimizer_usd"] == 0.001
    assert report["audit"]["token_usage"]["total"] == 15
    assert report["optimization_rounds"][0]["kind"] == "mock_optimizer"
    assert report["optimization_rounds"][0]["duration_seconds"] == 0.01


def test_eval_optimize_loop_gate_decision_matrix() -> None:
    pipeline = _load_pipeline_module()
    gate_config = {
        "min_validation_score_delta": 0.1,
        "allow_new_hard_fail": False,
        "critical_case_ids": ["critical"],
        "max_cost_usd": 0.01,
    }
    base_delta = {
        "case_id": "ordinary",
        "outcome": "new_pass",
        "baseline_score": 0.0,
        "candidate_score": 1.0,
    }

    accepted = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[base_delta],
        cost_usd=0.0,
    )
    assert accepted["decision"] == "accepted"
    assert all(check["passed"] for check in accepted["checks"])

    threshold_boundary = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.1,
        validation_case_deltas=[{**base_delta, "outcome": "score_improved"}],
        cost_usd=0.01,
    )
    assert threshold_boundary["decision"] == "accepted"
    assert all(check["passed"] for check in threshold_boundary["checks"])

    min_delta_fail = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.0,
        validation_delta=0.0,
        validation_case_deltas=[{**base_delta, "outcome": "unchanged"}],
        cost_usd=0.0,
    )
    assert min_delta_fail["decision"] == "rejected"
    assert {check["name"]: check for check in min_delta_fail["checks"]}["min_validation_score_delta"]["passed"] is False

    new_hard_fail = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[{**base_delta, "outcome": "new_fail"}],
        cost_usd=0.0,
    )
    assert new_hard_fail["decision"] == "rejected"
    assert {check["name"]: check for check in new_hard_fail["checks"]}["no_new_hard_fail"]["passed"] is False

    allowed_new_hard_fail = pipeline.apply_gate(
        gate_config={**gate_config, "allow_new_hard_fail": True},
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[{**base_delta, "outcome": "new_fail"}],
        cost_usd=0.0,
    )
    assert allowed_new_hard_fail["decision"] == "accepted"
    assert {check["name"]: check for check in allowed_new_hard_fail["checks"]}["no_new_hard_fail"]["passed"] is True

    critical_regression = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[{
            "case_id": "critical",
            "outcome": "score_regressed",
            "baseline_score": 1.0,
            "candidate_score": 0.5,
        }],
        cost_usd=0.0,
    )
    assert critical_regression["decision"] == "rejected"
    assert {check["name"]: check for check in critical_regression["checks"]}["critical_case_regression"]["passed"] is False

    overfit = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.0,
        validation_case_deltas=[{**base_delta, "outcome": "unchanged"}],
        cost_usd=0.0,
    )
    assert overfit["decision"] == "rejected"
    assert {check["name"]: check for check in overfit["checks"]}["overfitting_guard"]["passed"] is False

    validation_improved_not_overfit = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[{**base_delta, "outcome": "score_improved"}],
        cost_usd=0.0,
    )
    assert validation_improved_not_overfit["decision"] == "accepted"
    assert {check["name"]: check for check in validation_improved_not_overfit["checks"]}["overfitting_guard"]["passed"] is True

    cost_fail = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[base_delta],
        cost_usd=0.02,
    )
    assert cost_fail["decision"] == "rejected"
    assert {check["name"]: check for check in cost_fail["checks"]}["max_cost_usd"]["passed"] is False


def test_eval_optimize_loop_failure_attribution_matrix() -> None:
    pipeline = _load_pipeline_module()
    expected = '{"action":"refund_review","category":"billing","priority":"p1"}'

    cases = [
        ("not json", "format_violation"),
        ('{"action":"refund_review","category":"billing"}', "format_violation"),
        ('{"action":"refund_review","category":"account","priority":"p1"}', "knowledge_recall_insufficient"),
        ('{"action":"refund_review","category":"billing","priority":"p2"}', "parameter_error"),
        ('{"action":"answer","category":"billing","priority":"p1"}', "final_response_mismatch"),
    ]
    for actual, expected_type in cases:
        failure_types, reason = pipeline.attribute_failure(expected=expected, actual=actual)
        assert expected_type in failure_types
        assert reason

    failure_types, reason = pipeline.attribute_failure(
        expected=expected,
        actual='{"action":"refund_review","category":"billing","priority":"p2"}',
        case_id="rubric_case",
        case_meta={"rubric_case": {"fake_attribution_hints": ["llm_rubric_not_met"]}},
    )
    assert "llm_rubric_not_met" in failure_types
    assert "deterministic attribution hint" in reason

    failure_types, reason = pipeline.attribute_failure(
        expected=expected,
        actual='{"action":"answer","category":"billing"}',
        case_id="tool_case",
        case_meta={"tool_case": {"fake_attribution_hints": ["tool_call_error"]}},
    )
    assert {"tool_call_error", "format_violation"}.issubset(failure_types)
    assert reason


def test_eval_optimize_loop_hidden_like_gate_and_attribution_generalize() -> None:
    """Unknown case IDs should still be judged by rules, not public case names."""
    pipeline = _load_pipeline_module()
    gate_config = {
        "min_validation_score_delta": 0.1,
        "allow_new_hard_fail": False,
        "critical_case_ids": ["hidden_critical_payment_outage"],
        "max_cost_usd": 0.01,
    }

    hidden_accept = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[
            {
                "case_id": "hidden_refund_variant",
                "outcome": "new_pass",
                "baseline_score": 0.0,
                "candidate_score": 1.0,
            },
            {
                "case_id": "hidden_critical_payment_outage",
                "outcome": "unchanged",
                "baseline_score": 1.0,
                "candidate_score": 1.0,
            },
        ],
        cost_usd=0.0,
    )
    assert hidden_accept["decision"] == "accepted"

    hidden_new_fail = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[
            {
                "case_id": "hidden_unseen_regression",
                "outcome": "new_fail",
                "baseline_score": 1.0,
                "candidate_score": 0.0,
            }
        ],
        cost_usd=0.0,
    )
    assert hidden_new_fail["decision"] == "rejected"
    assert {check["name"]: check for check in hidden_new_fail["checks"]}["no_new_hard_fail"]["passed"] is False

    hidden_critical_regression = pipeline.apply_gate(
        gate_config=gate_config,
        train_delta=0.2,
        validation_delta=0.2,
        validation_case_deltas=[
            {
                "case_id": "hidden_critical_payment_outage",
                "outcome": "score_regressed",
                "baseline_score": 1.0,
                "candidate_score": 0.5,
            }
        ],
        cost_usd=0.0,
    )
    assert hidden_critical_regression["decision"] == "rejected"
    assert (
        {check["name"]: check for check in hidden_critical_regression["checks"]}[
            "critical_case_regression"
        ]["passed"]
        is False
    )

    expected = '{"action":"refund_review","category":"billing","priority":"p1"}'
    actual = '{"action":"answer","category":"account","priority":"p2"}'
    failure_types, reason = pipeline.attribute_failure(
        expected=expected,
        actual=actual,
        case_id="hidden_unlabeled_refund",
        case_meta={},
    )
    assert {
        "final_response_mismatch",
        "knowledge_recall_insufficient",
        "parameter_error",
    }.issubset(failure_types)
    assert reason

    failure_types, reason = pipeline.attribute_failure(
        expected=expected,
        actual="plain text from hidden case",
        case_id="hidden_unlabeled_format_case",
        case_meta={},
    )
    assert failure_types == ["format_violation"]
    assert reason
