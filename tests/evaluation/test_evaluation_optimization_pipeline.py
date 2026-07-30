# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the auditable Evaluation + Optimization regression loop."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalMetricResultDetails
from trpc_agent_sdk.evaluation import EvalMetricResultPerInvocation
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvaluationOptimizationPipeline
from trpc_agent_sdk.evaluation import IntermediateData
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import OptimizationPipelineConfig
from trpc_agent_sdk.evaluation import OptimizeResult
from trpc_agent_sdk.evaluation import RoundRecord
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.evaluation._evaluation_optimization_pipeline import (
    _build_case_evaluation,
)
from trpc_agent_sdk.evaluation._evaluation_optimization_config import (
    load_evaluation_optimization_config,
)
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

from fake_runtime import BALANCED_GUIDANCE  # noqa: E402
from fake_runtime import OVERFIT_GUIDANCE  # noqa: E402
from fake_runtime import build_fake_call_agent  # noqa: E402
from fake_runtime import fake_optimizer_runner  # noqa: E402


def _content(role: str, text: str) -> Content:
    return Content(role=role, parts=[Part.from_text(text=text)])


def _invocation(
    *,
    response: str,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
) -> Invocation:
    intermediate_data = None
    if tool_name is not None:
        intermediate_data = IntermediateData(
            tool_uses=[FunctionCall(name=tool_name, args=tool_args or {})],
        )
    return Invocation(
        user_content=_content("user", "query"),
        final_response=_content("model", response),
        intermediate_data=intermediate_data,
    )


def _failed_case(
    *,
    metric_name: str,
    actual: Invocation,
    expected: Invocation,
    reason: str | None = None,
    error_message: str | None = None,
) -> EvalCaseResult:
    details = EvalMetricResultDetails(reason=reason) if reason else None
    metric = EvalMetricResult(
        metric_name=metric_name,
        threshold=1.0,
        score=0.0,
        eval_status=EvalStatus.FAILED,
        details=details,
    )
    return EvalCaseResult(
        eval_set_id="set",
        eval_id="case",
        final_eval_status=EvalStatus.FAILED,
        error_message=error_message,
        overall_eval_metric_results=[metric],
        eval_metric_result_per_invocation=[
            EvalMetricResultPerInvocation(
                actual_invocation=actual,
                expected_invocation=expected,
                eval_metric_results=[metric],
            ),
        ],
        session_id="session",
    )


def _copy_example_config(
    tmp_path: Path,
    *,
    update_source: bool = False,
    max_total_cost_usd: float = 0.01,
    mode: str = "fake",
    report_language: str = "zh-CN",
    reflection_lm_updates: dict[str, Any] | None = None,
) -> Path:
    payload = json.loads((_EXAMPLE_DIR / "optimizer.json").read_text(encoding="utf-8"))
    payload["pipeline"]["update_source"] = update_source
    payload["pipeline"]["mode"] = mode
    payload["pipeline"]["report_language"] = report_language
    payload["pipeline"]["gate"]["max_total_cost_usd"] = max_total_cost_usd
    if reflection_lm_updates:
        payload["optimize"]["algorithm"]["reflection_lm"].update(
            reflection_lm_updates,
        )
    path = tmp_path / "optimizer.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


async def _run_example(
    tmp_path: Path,
    *,
    update_source: bool = False,
    max_total_cost_usd: float = 0.01,
    optimizer_runner=fake_optimizer_runner,
    report_language: str = "zh-CN",
    reflection_lm_updates: dict[str, Any] | None = None,
):
    prompt_path = tmp_path / "system.md"
    baseline = (_EXAMPLE_DIR / "prompts" / "system.md").read_text(encoding="utf-8")
    prompt_path.write_text(baseline, encoding="utf-8")
    config_path = _copy_example_config(
        tmp_path,
        update_source=update_source,
        max_total_cost_usd=max_total_cost_usd,
        report_language=report_language,
        reflection_lm_updates=reflection_lm_updates,
    )
    report = await EvaluationOptimizationPipeline.run(
        config_path=str(config_path),
        target_prompt=TargetPrompt().add_path("system_prompt", str(prompt_path)),
        train_dataset_path=str(_EXAMPLE_DIR / "train.evalset.json"),
        validation_dataset_path=str(_EXAMPLE_DIR / "val.evalset.json"),
        output_dir=str(tmp_path / "output"),
        call_agent=build_fake_call_agent(prompt_path),
        optimizer_runner=optimizer_runner,
        verbose=0,
    )
    return report, prompt_path, baseline


def test_pipeline_config_is_strict_and_has_safe_gate_defaults():
    config = OptimizationPipelineConfig()
    assert config.report_language == "en"
    assert config.gate.min_validation_score_delta > 0
    assert config.gate.reject_new_hard_fail is True
    assert config.gate.reject_overfitting is True
    assert config.update_source is False
    with pytest.raises(Exception):
        OptimizationPipelineConfig.model_validate({
            "hard_fail_case_ids": ["duplicate", "duplicate"],
        })
    with pytest.raises(Exception):
        OptimizationPipelineConfig.model_validate({"unknown": True})


@pytest.mark.parametrize(
    "payload",
    [
        {"gate": {"critical_case_ids": [""]}},
        {"gate": {"critical_case_ids": ["duplicate", "duplicate"]}},
        {"hard_fail_case_ids": [""]},
        {"hard_fail_categories": ["execution_error", "execution_error"]},
    ],
)
def test_pipeline_config_rejects_ambiguous_case_policies(payload):
    with pytest.raises(Exception):
        OptimizationPipelineConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("metric_name", "actual", "expected", "reason", "error_message", "category"),
    [
        (
            "final_response_avg_score",
            _invocation(response="wrong"),
            _invocation(response="right"),
            None,
            None,
            "final_response_mismatch",
        ),
        (
            "final_response_avg_score",
            _invocation(response="gold"),
            _invocation(response='{"tier":"gold"}'),
            None,
            None,
            "format_violation",
        ),
        (
            "tool_trajectory_avg_score",
            _invocation(response="x", tool_name="search"),
            _invocation(response="x", tool_name="lookup"),
            None,
            None,
            "tool_call_error",
        ),
        (
            "tool_trajectory_avg_score",
            _invocation(response="x", tool_name="search", tool_args={"q": "a"}),
            _invocation(response="x", tool_name="search", tool_args={"q": "b"}),
            None,
            None,
            "tool_argument_error",
        ),
        (
            "llm_rubric_response",
            _invocation(response="weak"),
            _invocation(response="strong"),
            "The answer does not meet the quality rubric.",
            None,
            "llm_rubric_failure",
        ),
        (
            "llm_rubric_knowledge_recall",
            _invocation(response="unsupported"),
            _invocation(response="grounded"),
            "Retrieved evidence does not support the answer.",
            None,
            "knowledge_recall_failure",
        ),
        (
            "final_response_avg_score",
            _invocation(response=""),
            _invocation(response="answer"),
            None,
            "model endpoint timed out",
            "execution_error",
        ),
    ],
)
def test_failure_attribution_covers_all_explainable_categories(
    metric_name,
    actual,
    expected,
    reason,
    error_message,
    category,
):
    result = _failed_case(
        metric_name=metric_name,
        actual=actual,
        expected=expected,
        reason=reason,
        error_message=error_message,
    )
    case = _build_case_evaluation(
        eval_set_id="set",
        case_id="case",
        runs=[result],
        pipeline_config=OptimizationPipelineConfig(),
    )
    assert category in case.failure_categories
    assert case.failure_reasons
    assert case.key_trace


@pytest.mark.asyncio
async def test_six_case_fake_pipeline_generates_complete_reports_under_three_minutes(tmp_path):
    started = time.perf_counter()
    report, prompt_path, baseline = await _run_example(tmp_path)
    elapsed = time.perf_counter() - started

    assert elapsed < 180
    assert report.baseline.train.case_count == 3
    assert report.baseline.validation.case_count == 3
    assert report.baseline.train.score == pytest.approx(0.0)
    assert report.baseline.validation.score == pytest.approx(1 / 3, abs=1e-6)
    assert report.candidate.validation.score == pytest.approx(2 / 3, abs=1e-6)
    assert report.gate_decision.accepted is True
    assert report.candidate.round == 2

    first, second = report.rounds
    assert first.gate_decision.accepted is False
    assert first.gate_decision.overfitting_detected is True
    assert first.validation_delta.newly_passed == ["val_json_invoice"]
    assert first.validation_delta.newly_failed == ["val_system_prompt_safety"]
    assert first.gate_decision.critical_regression_case_ids == [
        "val_system_prompt_safety"
    ]
    assert second.gate_decision.accepted is True
    assert second.validation_delta.newly_passed == ["val_json_invoice"]
    assert second.validation_delta.newly_failed == []
    assert "val_live_inventory" in second.validation_delta.unchanged

    assert prompt_path.read_text(encoding="utf-8") == baseline
    assert report.audit.source_updated is False
    assert report.audit.random_seed == 91
    assert report.audit.config_snapshot["optimize"]["algorithm"]["reflectionLm"][
        "apiKey"
    ] == "***REDACTED***"

    output = tmp_path / "output"
    json_path = output / "optimization_report.json"
    markdown_path = output / "optimization_report.md"
    assert json_path.is_file()
    assert markdown_path.is_file()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# 评测与提示词优化报告" in markdown
    assert "**最终决策：接受**" in markdown
    assert "建议接受第 2 轮候选提示词" in markdown
    assert "新增通过：val_json_invoice；新增失败：无" in markdown
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert {
        "baseline",
        "candidate",
        "delta",
        "gateDecision",
        "failureAttribution",
        "rounds",
        "audit",
    }.issubset(payload)
    assert payload["rounds"][0]["validationDelta"]["cases"]
    assert payload["rounds"][0]["validation"]["cases"][0]["keyTrace"]
    assert (output / "candidates" / "round_001" / "system_prompt.md").is_file()
    assert (output / "candidates" / "round_002" / "system_prompt.md").is_file()
    assert (output / "rounds" / "round_001.json").is_file()
    assert (output / "config.snapshot.json").is_file()

    original_json = json_path.read_text(encoding="utf-8")
    report.write(str(output))
    assert json_path.read_text(encoding="utf-8") == original_json


@pytest.mark.asyncio
async def test_nested_provider_credentials_are_redacted_from_all_audit_snapshots(
    tmp_path,
):
    secrets = {
        "Bearer audit-secret-123",
        "nested-x-api-key-123",
        "sk-provider-secret-123",
    }

    async def optimizer_with_raw_snapshot(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        config = Path(kwargs["config_path"]).read_text(encoding="utf-8")
        (output_dir / "config.snapshot.json").write_text(
            config,
            encoding="utf-8",
        )
        return await fake_optimizer_runner(**kwargs)

    report, _, _ = await _run_example(
        tmp_path,
        optimizer_runner=optimizer_with_raw_snapshot,
        reflection_lm_updates={
            "extra_fields": {
                "Authorization": "Bearer audit-secret-123",
                "nested": [{
                    "x-api-key": "nested-x-api-key-123",
                }],
                "normal_option": "retained",
            },
            "generation_config": {
                "credential": "sk-provider-secret-123",
                "temperature": 0.2,
                "max_tokens": 64,
            },
        },
    )

    output = tmp_path / "output"
    serialized_snapshots = [
        json.dumps(report.audit.config_snapshot, ensure_ascii=False),
        (output / "config.snapshot.json").read_text(encoding="utf-8"),
        (output / "optimization_report.json").read_text(encoding="utf-8"),
        (output / "optimizer" / "config.snapshot.json").read_text(
            encoding="utf-8",
        ),
    ]
    for snapshot in serialized_snapshots:
        assert "***REDACTED***" in snapshot
        assert all(secret not in snapshot for secret in secrets)

    reflection_lm = report.audit.config_snapshot["optimize"]["algorithm"][
        "reflectionLm"
    ]
    assert reflection_lm["extraFields"]["normal_option"] == "retained"
    assert reflection_lm["generationConfig"]["temperature"] == 0.2
    assert reflection_lm["generationConfig"]["max_tokens"] == 64


@pytest.mark.asyncio
async def test_accepted_candidate_is_written_only_when_configured(tmp_path):
    report, prompt_path, baseline = await _run_example(
        tmp_path,
        update_source=True,
    )
    assert report.gate_decision.accepted is True
    assert report.audit.source_updated is True
    updated = prompt_path.read_text(encoding="utf-8")
    assert updated != baseline
    assert BALANCED_GUIDANCE in updated
    assert OVERFIT_GUIDANCE not in updated


@pytest.mark.asyncio
async def test_train_improves_but_validation_regresses_is_rejected_and_not_written(tmp_path):
    async def overfit_only_optimizer(**kwargs):
        result = await fake_optimizer_runner(**kwargs)
        first = result.rounds[0]
        return result.model_copy(
            update={
                "best_prompts": first.candidate_prompts,
                "best_pass_rate": 1 / 3,
                "pass_rate_improvement": 0.0,
                "total_rounds": 1,
                "rounds": [first],
                "total_llm_cost": first.round_llm_cost,
            },
        )

    report, prompt_path, baseline = await _run_example(
        tmp_path,
        update_source=True,
        optimizer_runner=overfit_only_optimizer,
    )
    assert report.delta.train.score_delta > 0
    assert report.delta.validation.newly_failed == ["val_system_prompt_safety"]
    assert report.gate_decision.overfitting_detected is True
    assert report.gate_decision.accepted is False
    assert report.audit.source_updated is False
    assert prompt_path.read_text(encoding="utf-8") == baseline


@pytest.mark.asyncio
async def test_cost_budget_rejects_otherwise_acceptable_candidate(tmp_path):
    report, prompt_path, baseline = await _run_example(
        tmp_path,
        update_source=True,
        max_total_cost_usd=0.003,
    )
    assert report.audit.total_cost_usd == pytest.approx(0.004)
    assert report.gate_decision.accepted is False
    failed_checks = {
        check.name for check in report.gate_decision.checks if not check.passed
    }
    assert failed_checks == {"total_cost_budget"}
    assert prompt_path.read_text(encoding="utf-8") == baseline


def _write_trace_evalset(path: Path, eval_set_id: str, case_id: str) -> None:
    payload = {
        "eval_set_id": eval_set_id,
        "eval_cases": [
            {
                "eval_id": case_id,
                "eval_mode": "trace",
                "actual_conversation": [
                    {
                        "invocation_id": "actual",
                        "user_content": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                        "final_response": {
                            "role": "model",
                            "parts": [{"text": "hello"}],
                        },
                    }
                ],
                "conversation": [
                    {
                        "invocation_id": "expected",
                        "user_content": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                        "final_response": {
                            "role": "model",
                            "parts": [{"text": "hello"}],
                        },
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _trace_optimizer_result(baseline: dict[str, str]) -> OptimizeResult:
    candidate = {
        name: f"{content.rstrip()}\n\nTRACE_CANDIDATE\n"
        for name, content in baseline.items()
    }
    round_record = RoundRecord(
        round=1,
        optimized_field_names=list(candidate),
        candidate_prompts=candidate,
        train_pass_rate=1.0,
        validation_pass_rate=1.0,
        metric_breakdown={"final_response_avg_score": 1.0},
        accepted=True,
        acceptance_reason="static trace candidate",
        started_at="2026-01-01T00:00:00+00:00",
        duration_seconds=0.0,
    )
    return OptimizeResult(
        algorithm="trace_fake_optimizer",
        status="SUCCEEDED",
        finish_reason="completed",
        stop_reason="completed",
        baseline_pass_rate=1.0,
        best_pass_rate=1.0,
        pass_rate_improvement=0.0,
        baseline_prompts=baseline,
        best_prompts=candidate,
        total_rounds=1,
        rounds=[round_record],
        total_reflection_lm_calls=0,
        total_judge_model_calls=0,
        duration_seconds=0.0,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:00+00:00",
    )


def test_candidate_limit_preserves_optimizer_best_prompt():
    baseline = {"system_prompt": "baseline"}
    result = _trace_optimizer_result(baseline)
    template = result.rounds[0]
    rounds = [
        template.model_copy(
            update={
                "round": round_number,
                "candidate_prompts": {
                    "system_prompt": f"candidate-{round_number}",
                },
                "accepted": round_number == 3,
            },
        ) for round_number in range(1, 4)
    ]
    result = result.model_copy(
        update={
            "best_prompts": rounds[-1].candidate_prompts,
            "total_rounds": len(rounds),
            "rounds": rounds,
        },
    )

    specs = EvaluationOptimizationPipeline._candidate_specs(
        optimize_result=result,
        baseline_prompts=baseline,
        prompt_names=set(baseline),
        max_candidates=2,
    )

    assert [spec.round for spec in specs] == [1, 3]
    assert specs[-1].prompts == result.best_prompts


@pytest.mark.asyncio
async def test_empty_optimizer_result_uses_rejected_baseline_evidence(tmp_path):
    async def empty_optimizer(**kwargs):
        baseline = await kwargs["target_prompt"].read_all()
        result = _trace_optimizer_result(baseline)
        return result.model_copy(
            update={
                "algorithm": "empty_fake_optimizer",
                "best_prompts": {},
                "total_rounds": 0,
                "rounds": [],
                "total_llm_cost": 0.0,
            },
        )

    report, prompt_path, baseline = await _run_example(
        tmp_path,
        update_source=True,
        optimizer_runner=empty_optimizer,
        report_language="en",
    )

    assert len(report.rounds) == 1
    assert report.rounds[0].round == 0
    assert report.rounds[0].prompts == {"system_prompt": baseline}
    assert report.rounds[0].optimizer_accepted is False
    assert report.delta.validation.score_delta == 0.0
    assert report.gate_decision.accepted is False
    assert report.audit.source_updated is False
    assert prompt_path.read_text(encoding="utf-8") == baseline

    markdown = report.to_markdown()
    assert "# Evaluation + Optimization Report" in markdown
    assert "**Decision: REJECT**" in markdown
    assert "## Gate checks" in markdown
    assert "## Validation case deltas" in markdown
    assert "## Failure attribution" in markdown


@pytest.mark.asyncio
async def test_trace_mode_runs_without_call_agent_or_api_key(tmp_path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    _write_trace_evalset(train_path, "trace_train", "trace_train_case")
    _write_trace_evalset(val_path, "trace_val", "trace_val_case")

    config_payload = json.loads(
        (_EXAMPLE_DIR / "optimizer.json").read_text(encoding="utf-8"),
    )
    config_payload["pipeline"]["mode"] = "trace"
    config_payload["pipeline"]["gate"]["critical_case_ids"] = []
    config_payload["pipeline"]["hard_fail_case_ids"] = []
    config_payload["pipeline"]["gate"]["min_validation_score_delta"] = 0.01
    config_payload["pipeline"]["gate"]["min_validation_pass_rate_delta"] = 0.0
    config_path = tmp_path / "trace.optimizer.json"
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")

    async def trace_optimizer(**kwargs):
        return _trace_optimizer_result(await kwargs["target_prompt"].read_all())

    report = await EvaluationOptimizationPipeline.run(
        config_path=str(config_path),
        target_prompt=TargetPrompt().add_path("system_prompt", str(prompt_path)),
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "trace-output"),
        call_agent=None,
        optimizer_runner=trace_optimizer,
    )
    assert report.audit.mode == "trace"
    assert report.baseline.train.pass_rate == 1.0
    assert report.candidate.validation.pass_rate == 1.0
    assert report.delta.validation.score_delta == 0.0
    assert report.gate_decision.accepted is False


def test_example_config_and_case_counts_are_valid():
    config = load_evaluation_optimization_config(
        str(_EXAMPLE_DIR / "optimizer.json"),
    )
    assert config.pipeline.mode == "fake"
    assert config.pipeline.report_language == "zh-CN"
    assert config.pipeline.gate.critical_case_ids == [
        "val_system_prompt_safety"
    ]
    train = json.loads((_EXAMPLE_DIR / "train.evalset.json").read_text(encoding="utf-8"))
    validation = json.loads(
        (_EXAMPLE_DIR / "val.evalset.json").read_text(encoding="utf-8"),
    )
    assert len(train["eval_cases"]) == 3
    assert len(validation["eval_cases"]) == 3
