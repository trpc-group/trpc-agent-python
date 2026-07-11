from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from examples.optimization.eval_optimize_loop.pipeline.models import CaseSnapshot, ToolCallSnapshot


def _case(**overrides: object) -> CaseSnapshot:
    values: dict[str, object] = {
        "eval_id": "case",
        "split": "validation",
        "run_count": 1,
        "passed": False,
        "hard_failed": False,
        "aggregate_score": 0.0,
        "metric_scores": {},
        "metric_thresholds": {},
        "metric_passed": {},
        "trace_digest": "sha256:test",
    }
    values.update(overrides)
    return CaseSnapshot.model_validate(values)


def _attribute(case: CaseSnapshot, judge: Callable | None = None):
    from examples.optimization.eval_optimize_loop.pipeline.attribution import attribute_case

    return attribute_case(case, judge=judge)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (_case(failure_reasons=["request timeout after 30 seconds"]), "timeout"),
        (_case(execution_errors=["backend quota exhausted"]), "execution_error"),
        (_case(failure_reasons=["connection refused while executing request"]), "execution_error"),
        (_case(tool_calls=[], expected_tool_calls=[ToolCallSnapshot(name="lookup_order")]), "tool_selection_error"),
        (_case(tool_calls=[ToolCallSnapshot(name="lookup_order", arguments={"order_id": 7})], expected_tool_calls=[ToolCallSnapshot(name="lookup_order", arguments={"order_id": "A100"})]), "tool_argument_error"),
        (_case(failure_reasons=["tool response was empty"]), "tool_execution_error"),
        (_case(failure_reasons=["actual response: invalid JSON response"]), "format_violation"),
        (_case(metric_passed={"knowledge_recall_score": False}), "knowledge_recall_insufficient"),
        (_case(metric_passed={"fake_rubric_score": False}), "llm_rubric_not_met"),
        (_case(metric_passed={"final_response_avg_score": False}), "final_response_mismatch"),
        (_case(), "unknown"),
    ],
    ids=(
        "timeout",
        "recorded-execution-error",
        "execution-error",
        "tool-selection",
        "tool-arguments",
        "tool-execution",
        "format",
        "knowledge",
        "rubric",
        "final-response",
        "no-rule",
    ),
)
def test_rule_first_attribution_covers_each_precedence_category(case: CaseSnapshot, expected: str) -> None:
    attribution = _attribute(case)

    assert attribution.primary_type.value == expected
    assert attribution.evidence


def test_judge_is_used_only_when_no_rule_matches() -> None:
    calls: list[str] = []

    def judge(_case: CaseSnapshot) -> dict[str, object]:
        calls.append("judge")
        return {"primary_type": "safety_violation", "confidence": 0.8, "evidence": ["judge-only signal"]}

    attribution = _attribute(_case(), judge=judge)

    assert calls == ["judge"]
    assert attribution.primary_type.value == "safety_violation"
    assert attribution.source == "judge"


def test_invalid_judge_output_falls_back_to_unknown() -> None:
    attribution = _attribute(_case(), judge=lambda _case: {})

    assert attribution.primary_type.value == "unknown"
    assert attribution.source == "fallback"
    assert attribution.evidence


def test_structural_rule_wins_over_conflicting_judge() -> None:
    calls: list[str] = []

    def judge(_case: CaseSnapshot) -> dict[str, object]:
        calls.append("judge")
        return {"primary_type": "safety_violation", "confidence": 1.0, "evidence": ["incorrect override"]}

    attribution = _attribute(
        _case(tool_calls=[], expected_tool_calls=[ToolCallSnapshot(name="lookup_order")]), judge=judge
    )

    assert attribution.primary_type.value == "tool_selection_error"
    assert attribution.source == "rule"
    assert calls == []


@pytest.mark.asyncio
async def test_trace_mode_evaluates_recorded_conversations_without_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    from examples.optimization.eval_optimize_loop.run_pipeline import run_trace_pipeline

    report = await run_trace_pipeline(output_dir=tmp_path)

    assert report.mode == "trace"
    assert report.selected_candidate_id is None
    assert report.baseline_validation is not None
    failures = [case for case in report.baseline_validation.cases if not case.passed]
    assert {case.failure_attribution.primary_type.value for case in failures if case.failure_attribution} == {
        "tool_selection_error",
        "tool_argument_error",
        "format_violation",
    }
    assert all(case.failure_attribution and case.failure_attribution.evidence for case in failures)
    assert sum(case.passed for case in report.baseline_validation.cases) == 1
    assert (tmp_path / "optimization_report.json").is_file()
    assert (tmp_path / "optimization_report.md").is_file()
    assert (tmp_path / "trace_raw_results.json").is_file()
    assert (tmp_path / "trace_normalized_cases.json").is_file()
    assert json.loads((tmp_path / "trace_raw_results.json").read_text(encoding="utf-8"))["raw_evaluator_ran"] is True


def test_trace_cli_writes_report_paths(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "examples/optimization/eval_optimize_loop/run_pipeline.py",
            "--mode",
            "trace",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "JSON report:" in result.stdout
    assert "Markdown report:" in result.stdout
