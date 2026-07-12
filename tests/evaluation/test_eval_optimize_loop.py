"""Behavior tests for the trust-aware eval/optimization loop."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.optimization.eval_optimize_loop.pipeline.diagnosis import (
    InfrastructureFailure,
    attribute_from_evidence,
    build_failure_digest,
    classify_non_agent_failure,
    select_target_prompts,
)
from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate
from examples.optimization.eval_optimize_loop.pipeline.input_audit import audit_eval_cases
from examples.optimization.eval_optimize_loop.pipeline.models import CounterfactualEvidence
from examples.optimization.eval_optimize_loop.fake.model import generate_trace
from examples.optimization.eval_optimize_loop.pipeline.optimizer import (
    apply_if_accepted,
    run_real_optimizer,
)
from examples.optimization.eval_optimize_loop.pipeline.pipeline import run_pipeline
from examples.optimization.eval_optimize_loop.pipeline.pipeline import _evaluate_with_triage
from examples.optimization.eval_optimize_loop.pipeline.probe import build_probe_cases


def _evidence(name: str, passed: bool, repaired: list[str]) -> CounterfactualEvidence:
    return CounterfactualEvidence(
        intervention=name,
        valid=True,
        status="constructed",
        changed_fail_to_pass=passed,
        repaired_metrics=repaired,
        unchanged_metrics=[],
        before_metrics={"tool_trajectory_avg_score": 0.0},
        after_metrics={"tool_trajectory_avg_score": 1.0 if passed else 0.0},
    )


def test_attribution_ignores_reason_case_id_and_evidence_order():
    evidence = [
        _evidence("replace_tool_arguments", False, []),
        _evidence("replace_tool_name", True, ["tool_trajectory_avg_score"]),
    ]
    first = attribute_from_evidence("anything", evidence, failure_reason="tool arguments were wrong")
    second = attribute_from_evidence("renamed", list(reversed(evidence)), failure_reason="")
    assert first.primary_category == second.primary_category == "tool_selection_error"
    assert first.confidence == second.confidence


def test_compound_and_parameter_failures_are_distinct():
    parameter = attribute_from_evidence("x", [_evidence("replace_tool_arguments", True, ["tool_trajectory_avg_score"])])
    compound = attribute_from_evidence(
        "y",
        [
            _evidence("replace_tool_name", False, []),
            _evidence("replace_tool_arguments", False, []),
            _evidence("replace_tool_name_and_arguments", True, ["tool_trajectory_avg_score"]),
        ],
    )
    assert parameter.primary_category == "tool_parameter_error"
    assert compound.primary_category == "compound_failure"


def test_attribution_accuracy_against_test_only_oracle():
    """The oracle lives only in tests; production receives intervention evidence."""
    cases = {
        "final": (
            [_evidence("replace_final_response", True, ["final_response_avg_score"])],
            "final_response_mismatch",
        ),
        "tool": (
            [_evidence("replace_tool_name", True, ["tool_trajectory_avg_score"])],
            "tool_selection_error",
        ),
        "arguments": (
            [_evidence("replace_tool_arguments", True, ["tool_trajectory_avg_score"])],
            "tool_parameter_error",
        ),
        "compound": (
            [
                _evidence("replace_tool_name", False, []),
                _evidence("replace_tool_arguments", False, []),
                _evidence(
                    "replace_tool_name_and_arguments",
                    True,
                    ["tool_trajectory_avg_score"],
                ),
            ],
            "compound_failure",
        ),
        "sequence": (
            [
                CounterfactualEvidence(
                    "replace_tool_name",
                    False,
                    "tool_call_count_mismatch",
                    False,
                    [],
                    [],
                    {"tool_trajectory_avg_score": 0.0},
                    {},
                    structurally_valid=False,
                )
            ],
            "tool_sequence_error",
        ),
        "rubric": (
            [_evidence("replace_final_response", False, [])],
            "llm_rubric_not_met",
        ),
        "knowledge": (
            [_evidence("replace_final_response", False, [])],
            "knowledge_recall_insufficient",
        ),
        "format": (
            [_evidence("normalize_format", False, [])],
            "format_violation",
        ),
    }
    cases["rubric"][0][0].before_metrics = {"llm_rubric_response": 0.0}
    cases["knowledge"][0][0].before_metrics = {"llm_rubric_knowledge_recall": 0.0}
    cases["format"][0][0].before_metrics = {"format_compliance_score": 0.0}

    predictions = {
        name: attribute_from_evidence(name, evidence).primary_category for name, (evidence, _) in cases.items()
    }
    correct = sum(predictions[name] == expected for name, (_, expected) in cases.items())
    accuracy = correct / len(cases)

    assert accuracy >= 0.75, {
        name: {"predicted": predictions[name], "expected": expected}
        for name, (_, expected) in cases.items()
        if predictions[name] != expected
    }


def test_incoherent_counterfactual_evidence_reduces_confidence():
    coherent = _evidence("replace_tool_name", True, ["tool_trajectory_avg_score"])
    incoherent = _evidence("replace_tool_name", True, ["tool_trajectory_avg_score"])
    incoherent.semantically_coherent = False
    incoherent.coherence_warnings = ["tool_response_matches_original_call"]
    assert attribute_from_evidence("a", [incoherent]).confidence < attribute_from_evidence("b", [coherent]).confidence


def test_missing_reference_is_eval_data_and_timeout_is_infrastructure():
    missing = classify_non_agent_failure("c", reliability="invalid", issues=["missing_reference"])
    timeout = classify_non_agent_failure("d", error=TimeoutError("slow"))
    assert missing.failure_domain == "evaluation_data_failure"
    assert missing.prompt_actionable is False
    assert timeout.failure_domain == "infrastructure_failure"
    assert timeout.primary_category == "model_timeout"
    assert timeout.prompt_actionable is False


def test_evaluator_and_tool_runtime_failures_are_non_actionable():
    evaluator = classify_non_agent_failure("e", error=ValueError("metric exploded"))
    runtime = classify_non_agent_failure("i", error=InfrastructureFailure("tool_runtime_error", "tool crashed"))
    data = classify_non_agent_failure("d", reliability="invalid", issues=["missing_reference"])
    agent = attribute_from_evidence("a", [_evidence("replace_tool_name", True, ["tool_trajectory_avg_score"])])
    assert evaluator.failure_domain == "evaluator_failure"
    assert runtime.failure_domain == "infrastructure_failure"
    assert runtime.primary_category == "tool_runtime_error"
    assert select_target_prompts([evaluator, runtime]) == []
    digest = build_failure_digest([agent, data, evaluator, runtime])
    assert [x["case_id"] for x in digest["actionable_failures"]] == ["a"]
    assert {x["failure_domain"] for x in digest["excluded_failures"]} == {
        "evaluator_failure",
        "infrastructure_failure",
        "evaluation_data_failure",
    }


def test_invalid_cases_do_not_drive_targets():
    cases = [{"case_id": "bad", "status": "invalid", "issues": ["missing_reference"]}]
    audit = audit_eval_cases(cases, overlapping_inputs=set())
    assert audit[0]["status"] == "invalid"
    assert select_target_prompts([]) == []


def test_counterfactual_budget_limits_evidence():
    evidence = [_evidence("replace_tool_name", False, []), _evidence("replace_tool_arguments", False, [])]
    result = attribute_from_evidence("x", evidence[:1], evaluations_used=1, budget=1)
    assert result.evaluations_used == 1
    assert "counterfactual_budget_exhausted" in result.evidence


def _gate_input(**overrides):
    value = {
        "baseline_train": 0.3,
        "candidate_train": 0.8,
        "baseline_validation": 0.5,
        "candidate_validation": 0.7,
        "trusted_baseline": 0.5,
        "trusted_candidate": 0.7,
        "new_hard_fails": [],
        "protected_regressions": [],
        "severity_escalations": [],
        "cost": 0.1,
        "duration_seconds": 1.0,
        "evidence_sufficient": True,
    }
    value.update(overrides)
    return value


def test_gate_rejects_train_only_improvement_and_new_hard_fail():
    no_val = evaluate_gate(
        _gate_input(candidate_validation=0.5), {"min_validation_delta": 0.01, "max_cost": 1, "max_latency_seconds": 10}
    )
    hard = evaluate_gate(
        _gate_input(new_hard_fails=["v3"]), {"min_validation_delta": 0.01, "max_cost": 1, "max_latency_seconds": 10}
    )
    assert no_val["accepted"] is False
    assert "TRAIN_ONLY_IMPROVEMENT" in no_val["reason_codes"]
    assert hard["accepted"] is False
    assert "NEW_HARD_FAIL" in hard["reason_codes"]


def test_gate_rejects_format_to_tool_selection_severity_escalation():
    result = evaluate_gate(
        _gate_input(severity_escalations=[{"before": "format_violation", "after": "tool_selection_error"}]),
        {"min_validation_delta": 0.01, "max_cost": 1, "max_latency_seconds": 10},
    )
    assert result["accepted"] is False
    assert "SEVERITY_ESCALATION" in result["reason_codes"]


def test_gate_accepts_safe_improvement_and_is_monotonic_when_tightened():
    config = {"min_validation_delta": 0.01, "max_cost": 1, "max_latency_seconds": 10}
    accepted = evaluate_gate(_gate_input(), config)
    ineffective = evaluate_gate(_gate_input(candidate_train=0.3, candidate_validation=0.5), config)
    tightened = evaluate_gate(_gate_input(), {**config, "min_validation_delta": 0.3})
    protected = evaluate_gate(_gate_input(protected_regressions=["critical"]), config)
    expensive = evaluate_gate(_gate_input(cost=2.0), config)
    slow = evaluate_gate(_gate_input(duration_seconds=11.0), config)
    assert accepted["accepted"] is True
    assert ineffective["accepted"] is False
    assert tightened["accepted"] is False
    assert protected["accepted"] is False
    assert expensive["accepted"] is False
    assert slow["accepted"] is False
    assert "LATENCY_BUDGET" in slow["reason_codes"]


def test_fake_model_handles_unknown_semantics_without_case_id_branching():
    case = build_probe_cases()[0]
    case.eval_id = "arbitrary-hidden-id"
    case.conversation[0].user_content.parts[0].text = "Please explain the account policy."

    generated = generate_trace(
        case,
        {"router_prompt": "", "skill_prompt": "", "system_prompt": ""},
    )

    assert generated.eval_id == "arbitrary-hidden-id"
    assert generated.actual_conversation
    assert generated.actual_conversation[0].final_response is not None


@pytest.mark.asyncio
async def test_apply_only_writes_when_accepted_and_explicit(tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("baseline", encoding="utf-8")
    rejected = await apply_if_accepted(
        {"accepted": False}, False, {"system_prompt": "candidate"}, {"system_prompt": prompt}
    )
    implicit = await apply_if_accepted(
        {"accepted": True}, False, {"system_prompt": "candidate"}, {"system_prompt": prompt}
    )
    assert rejected["applied"] is False
    assert implicit["applied"] is False
    assert prompt.read_text(encoding="utf-8") == "baseline"

    applied = await apply_if_accepted(
        {"accepted": True}, True, {"system_prompt": "candidate"}, {"system_prompt": prompt}
    )
    assert applied["applied"] is True
    assert applied["baseline_hashes"] != applied["final_hashes"]
    assert prompt.read_text(encoding="utf-8") == "candidate"


@pytest.mark.asyncio
async def test_fake_and_trace_pipeline_have_same_schema_without_api_key(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if "API_KEY" in key:
            monkeypatch.delenv(key, raising=False)
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"
    fake = await run_pipeline(base, "fake", tmp_path / "fake")
    trace = await run_pipeline(base, "trace", tmp_path / "trace")
    assert fake.keys() == trace.keys()
    assert fake["schema_version"] == "1.0"
    assert fake["gate"]["accepted"] is False
    assert [x["change"] for x in fake["candidate_validation"]["case_deltas"]] == ["new_pass", "unchanged", "new_fail"]
    assert fake["regression_diagnosis"]["items"][0]["counterfactual_evidence"]["intervention"] == "replace_tool_name"
    assert fake["audit"]["write_back"]["applied"] is False


@pytest.mark.asyncio
async def test_real_evaluations_drive_accept_overfit_and_ineffective_decisions(tmp_path):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"
    accepted = await run_pipeline(base, "fake", tmp_path / "accepted", candidate_profile="accepted")
    overfit = await run_pipeline(base, "fake", tmp_path / "overfit", candidate_profile="overfit")
    ineffective = await run_pipeline(base, "fake", tmp_path / "ineffective", candidate_profile="ineffective")
    assert accepted["gate"]["accepted"] is True
    assert all(check["passed"] for check in accepted["gate"]["checks"])
    assert accepted["candidate_validation"]["validation_score"] > accepted["baseline"]["validation"]["score"]
    assert overfit["gate"]["accepted"] is False
    assert any(x["change"] == "new_fail" for x in overfit["candidate_validation"]["case_deltas"])
    assert ineffective["gate"]["accepted"] is False
    assert ineffective["candidate_validation"]["validation_score"] == ineffective["baseline"]["validation"]["score"]


@pytest.mark.asyncio
async def test_real_optimizer_receives_filtered_train_and_never_updates_source(tmp_path, monkeypatch):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"
    captured = {}

    async def spy(**kwargs):
        captured.update(kwargs)
        filtered = json.loads(Path(kwargs["train_dataset_path"]).read_text(encoding="utf-8"))
        captured["filtered_ids"] = [x["evalId"] for x in filtered["evalCases"]]
        validation = json.loads(Path(kwargs["validation_dataset_path"]).read_text(encoding="utf-8"))
        validation_cases = validation.get("evalCases", validation.get("eval_cases", []))
        captured["validation_ids"] = [x.get("evalId", x.get("eval_id")) for x in validation_cases]
        captured["target_names"] = tuple(kwargs["target_prompt"].names())
        return SimpleNamespace(
            algorithm="gepa_reflective",
            total_rounds=2,
            baseline_pass_rate=0.25,
            best_pass_rate=0.75,
            pass_rate_improvement=0.5,
            baseline_metric_breakdown={"tool_trajectory_avg_score": 0.25},
            best_metric_breakdown={"tool_trajectory_avg_score": 0.75},
            metric_thresholds={"tool_trajectory_avg_score": 1.0},
            baseline_prompts={"router_prompt": "baseline"},
            best_prompts={"router_prompt": "candidate"},
            total_llm_cost=0.2,
            total_token_usage={"total": 42},
            duration_seconds=1.5,
            status="SUCCEEDED",
            rounds=[{"round": 1}, {"round": 2}],
        )

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.optimizer.AgentOptimizer.optimize",
        spy,
    )
    result = await run_real_optimizer(
        config_path=base / "optimizer.json",
        call_agent=lambda _: "ok",
        prompt_paths={"router_prompt": base / "prompts" / "router.md"},
        train_path=base / "train.evalset.json",
        actionable_case_ids={"train_route"},
        validation_path=base / "val.evalset.json",
        output_dir=tmp_path,
    )
    assert captured["filtered_ids"] == ["train_route"]
    assert captured["validation_ids"] == ["val_refund", "val_shipping", "val_billing"]
    assert captured["target_names"] == ("router_prompt",)
    assert captured["update_source"] is False
    assert result == {
        "algorithm": "gepa_reflective",
        "total_rounds": 2,
        "rounds": [{"round": 1}, {"round": 2}],
        "best_prompts": {"router_prompt": "candidate"},
        "baseline_prompts": {"router_prompt": "baseline"},
        "baseline_pass_rate": 0.25,
        "best_pass_rate": 0.75,
        "pass_rate_improvement": 0.5,
        "baseline_metric_breakdown": {"tool_trajectory_avg_score": 0.25},
        "best_metric_breakdown": {"tool_trajectory_avg_score": 0.75},
        "metric_thresholds": {"tool_trajectory_avg_score": 1.0},
        "cost": 0.2,
        "tokens": {"total": 42},
        "duration_seconds": 1.5,
        "seed": 42,
        "status": "SUCCEEDED",
    }


@pytest.mark.asyncio
async def test_real_optimizer_restores_prompt_if_optimizer_mutates_then_fails(tmp_path, monkeypatch):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"
    prompt = tmp_path / "router.md"
    prompt.write_text("baseline", encoding="utf-8")

    async def broken_optimizer(**kwargs):
        await kwargs["target_prompt"].write_all({"router_prompt": "mutated"})
        raise RuntimeError("optimizer failed")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.optimizer.AgentOptimizer.optimize",
        broken_optimizer,
    )

    with pytest.raises(RuntimeError, match="optimizer failed"):
        await run_real_optimizer(
            config_path=base / "optimizer.json",
            call_agent=lambda _: "ok",
            prompt_paths={"router_prompt": prompt},
            train_path=base / "train.evalset.json",
            actionable_case_ids={"train_route"},
            validation_path=base / "val.evalset.json",
            output_dir=tmp_path / "optimizer-output",
        )

    assert prompt.read_text(encoding="utf-8") == "baseline"


@pytest.mark.asyncio
async def test_pipeline_rejects_unsupported_real_mode(tmp_path):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"

    with pytest.raises(ValueError, match="fake or trace"):
        await run_pipeline(base, "real", tmp_path)


@pytest.mark.asyncio
async def test_report_contains_trace_status_evidence_and_limitations(tmp_path):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"
    report = await run_pipeline(base, "trace", tmp_path)
    case = report["baseline"]["train"]["case_results"][0]
    assert {"case_id", "passed", "metrics", "failure_reason", "trace_summary"} <= case.keys()
    assert report["candidate"]["validation"]["case_results"]
    assert report["delta"]["case_deltas"] == report["candidate_validation"]["case_deltas"]
    evidence = report["failure_attribution"]["items"][0]["evidence"][0]
    assert {"structurally_valid", "semantically_coherent", "coherence_warnings"} <= evidence.keys()
    assert report["known_limitations"]


@pytest.mark.asyncio
async def test_fake_round_audit_contains_candidate_prompts_and_evaluation(tmp_path):
    base = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop"

    report = await run_pipeline(base, "fake", tmp_path, candidate_profile="accepted")

    round_record = report["optimization"]["rounds"][0]
    assert round_record["candidate_prompts"] == report["optimization"]["best_prompts"]
    assert round_record["validation_score"] == report["candidate_validation"]["validation_score"]
    assert set(round_record["metric_breakdown"]) == {
        "final_response_avg_score",
        "tool_trajectory_avg_score",
    }
    assert round_record["accepted"] is True


def test_committed_sample_output_has_current_report_contract():
    sample = Path(__file__).parents[2] / "examples" / "optimization" / "eval_optimize_loop" / "sample_output"
    report = json.loads((sample / "optimization_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["baseline"]["train"]["case_results"]
    assert report["known_limitations"]


@pytest.mark.asyncio
async def test_pipeline_evaluation_triages_timeout_without_dropping_case(tmp_path, monkeypatch):
    cases = build_probe_cases()[:2]
    calls = 0

    async def evaluator(case_batch, workspace):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("backend timed out")
        case = case_batch[0]
        return {case.eval_id: {"tool_trajectory_avg_score": 1.0}}

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.pipeline.evaluate_trace_cases",
        evaluator,
    )
    metrics, failures = await _evaluate_with_triage(cases, tmp_path)
    assert cases[0].eval_id in failures
    assert failures[cases[0].eval_id].failure_domain == "infrastructure_failure"
    assert cases[1].eval_id in metrics
