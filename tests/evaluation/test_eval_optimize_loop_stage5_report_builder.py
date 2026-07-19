"""Tests for Stage 5 serializable optimization reports."""

from datetime import datetime, timezone
import shutil
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.pipeline import prepare_run, run_fake_stage
from examples.optimization.eval_optimize_loop.report_builder import (
    build_failure_report, build_optimization_report, render_optimization_markdown,
)
from examples.optimization.eval_optimize_loop.schemas import (
    ArtifactIndex, ArtifactReference, OptimizationReport, OptimizerResourceValue, ReportProgress,
)
from examples.optimization.eval_optimize_loop.schemas import OptimizerCandidateProposal, RealStageResult
from trpc_agent_sdk.evaluation import OptimizeResult

_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _ROOT / "examples" / "optimization" / "eval_optimize_loop"

def _copy_example(tmp_path: Path, name: str = "eval_optimize_loop") -> Path:
    target = tmp_path / name
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target

def _progress() -> ReportProgress:
    return ReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc), current_phase="reporting",
        completed_phases=[
            "baseline_train", "baseline_validation", "candidate_generation",
            "candidate_train", "candidate_validation", "analysis", "gate", "writeback",
        ],
    )


def _optimize_result(baseline_prompts: dict[str, str], best_prompts: dict[str, str]) -> OptimizeResult:
    return OptimizeResult(
        algorithm="gepa_reflective", status="SUCCEEDED", finish_reason="completed", error_message="",
        baseline_pass_rate=1 / 3, best_pass_rate=1.0, pass_rate_improvement=2 / 3,
        baseline_prompts=baseline_prompts, best_prompts=best_prompts, total_rounds=1, rounds=[],
        total_reflection_lm_calls=1, total_llm_cost=0.25,
        total_token_usage={"prompt": 10, "completion": 5, "total": 15}, duration_seconds=0.5,
        started_at="2026-07-17T00:00:00Z", finished_at="2026-07-17T00:00:01Z",
    )

@pytest.mark.asyncio
async def test_fake_report_is_serializable_and_marks_optimizer_resources_not_applicable(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_fake")
    report = build_optimization_report(
        prepared, await run_fake_stage(prepared, scenario="improve"), progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    assert report.status == "completed"
    assert report.execution_mode == "fake"
    optimizer_resources = report.optimizer_resources
    for observation in (
        optimizer_resources.total_rounds,
        optimizer_resources.reflection_lm_calls,
        optimizer_resources.cost_usd,
        optimizer_resources.token_usage,
        optimizer_resources.duration_seconds,
    ):
        assert observation.status == "not_applicable"
        assert observation.value is None
        assert observation.reason == "Fake mode does not run AgentOptimizer."
    assert report.pipeline_resources.total_tokens.status == "unavailable"
    assert OptimizationReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.asyncio
async def test_real_report_includes_optimizer_resource_observations(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_real")
    fake_result = await run_fake_stage(prepared, scenario="improve")
    candidate = OptimizerCandidateProposal.model_validate({
        **fake_result.candidate.model_dump(exclude={"scenario", "seed"}), "provider": "agent_optimizer",
        "optimizer_status": "SUCCEEDED", "finish_reason": "completed",
        "baseline_pass_rate": 1 / 3, "best_pass_rate": 1.0,
        "candidate_id": f"optimizer-{fake_result.candidate.candidate_id[-12:]}",
    })
    result = RealStageResult(
        **fake_result.model_dump(exclude={"scenario", "candidate"}), candidate=candidate,
        optimize_result=_optimize_result(await prepared.source_target.read_all(), candidate.prompts),
    )
    report = build_optimization_report(
        prepared, result, progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    assert report.optimizer_resources.total_rounds.status == "available"
    assert report.optimizer_resources.total_rounds.value == 1
    assert report.optimizer_resources.total_rounds.unit == "rounds"
    assert report.optimizer_resources.reflection_lm_calls.status == "available"
    assert report.optimizer_resources.reflection_lm_calls.value == 1
    assert report.optimizer_resources.cost_usd.status == "available"
    assert report.optimizer_resources.token_usage.status == "available"
    assert report.optimizer_resources.duration_seconds.status == "available"
    assert report.optimizer_resources.scope_note == (
        "Optimizer-only observation; excludes complete business Agent evaluation usage."
    )


@pytest.mark.asyncio
async def test_real_report_marks_incomplete_cost_and_token_telemetry_unavailable(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_real_incomplete")
    fake_result = await run_fake_stage(prepared, scenario="improve")
    candidate = OptimizerCandidateProposal.model_validate({
        **fake_result.candidate.model_dump(exclude={"scenario", "seed"}), "provider": "agent_optimizer",
        "optimizer_status": "SUCCEEDED", "finish_reason": "completed",
        "baseline_pass_rate": 1 / 3, "best_pass_rate": 1.0,
        "candidate_id": f"optimizer-{fake_result.candidate.candidate_id[-12:]}",
    })
    native = _optimize_result(
        await prepared.source_target.read_all(), candidate.prompts,
    ).model_copy(update={
        "total_reflection_lm_calls": 2,
        "total_llm_cost": 0.0,
        "total_token_usage": {"prompt": 0, "completion": 0, "total": 0},
    })
    result = RealStageResult(
        **fake_result.model_dump(exclude={"scenario", "candidate"}), candidate=candidate,
        optimize_result=native,
    )

    report = build_optimization_report(
        prepared, result, progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    assert report.optimizer_resources.total_rounds.status == "available"
    assert report.optimizer_resources.total_rounds.value == 1
    assert report.optimizer_resources.reflection_lm_calls.status == "available"
    assert report.optimizer_resources.reflection_lm_calls.value == 2
    assert report.optimizer_resources.duration_seconds.status == "available"
    assert report.optimizer_resources.duration_seconds.value == 0.5
    assert report.optimizer_resources.cost_usd.status == "unavailable"
    assert report.optimizer_resources.cost_usd.value is None
    assert report.optimizer_resources.cost_usd.reason
    assert report.optimizer_resources.token_usage.status == "unavailable"
    assert report.optimizer_resources.token_usage.value is None
    assert report.optimizer_resources.token_usage.reason

    markdown = render_optimization_markdown(report)
    assert "Rounds: available" in markdown
    assert "Reflection calls: available" in markdown
    assert "Cost: unavailable" in markdown
    assert "Token usage: unavailable" in markdown
    assert "Duration: available" in markdown

@pytest.mark.asyncio
async def test_markdown_includes_accept_and_reject_report_evidence(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_markdown")
    accepted = build_optimization_report(
        prepared, await run_fake_stage(prepared, scenario="improve"), progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    rejected_root = _copy_example(tmp_path, "eval_optimize_loop_reject")
    rejected_prepared = prepare_run(rejected_root / "pipeline.json", run_id="report_markdown_reject")
    rejected = build_optimization_report(
        rejected_prepared, await run_fake_stage(rejected_prepared, scenario="no_improvement"), progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    markdown = render_optimization_markdown(accepted)
    assert "# Optimization Report" in markdown
    assert "Gate decision: ACCEPT" in markdown
    assert "Baseline train" in markdown
    assert "Candidate validation" in markdown
    assert "Writeback" in markdown
    assert "unavailable" in markdown
    assert "Gate decision: REJECT" in render_optimization_markdown(rejected)

@pytest.mark.asyncio
async def test_failure_report_is_deterministic_and_records_only_error_identity(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_failure")
    report = build_failure_report(
        prepared, progress=_progress(), error=RuntimeError("pipeline failed"),
        source_prompt_hashes={"z": "last", "a": "first"}, existing_artifacts=["z.json", "a.json"],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    assert report.status == "failed"
    assert report.failed_phase == "reporting"
    assert report.exception_type == "RuntimeError"
    assert report.error_message == "pipeline failed"
    assert list(report.source_prompt_hashes) == ["a", "z"]
    assert report.existing_artifacts == ["a.json", "z.json"]


@pytest.mark.asyncio
async def test_failure_report_redacts_environment_values_and_common_secret_forms(tmp_path, monkeypatch):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_failure_redacted")
    api_key = "env-api-key-secret"
    base_url = "https://env-base-url-secret.example/v1"
    monkeypatch.setenv("TRPC_AGENT_API_KEY", api_key)
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", base_url)
    error = RuntimeError(
        f"timeout contacting {base_url} with {api_key}; "
        "api_key=snake-secret apiKey:'camel-secret' "
        "Authorization: Bearer authorization-secret Bearer loose-bearer-secret "
        "base_url=https://snake-url-secret.example/v1 "
        'baseUrl="https://camel-url-secret.example/v1"; retryable=true'
    )

    report = build_failure_report(
        prepared, progress=_progress(), error=error,
        source_prompt_hashes={}, existing_artifacts=[],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    for secret in (
        api_key,
        base_url,
        "snake-secret",
        "camel-secret",
        "authorization-secret",
        "loose-bearer-secret",
        "https://snake-url-secret.example/v1",
        "https://camel-url-secret.example/v1",
    ):
        assert secret not in report.error_message
    assert report.error_message.count("[REDACTED]") >= 8
    assert "timeout contacting" in report.error_message
    assert "retryable=true" in report.error_message


def test_failure_report_redacts_hyphenated_keys_and_bare_urls(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_failure_hyphenated")
    error = RuntimeError(
        "X-API-Key: hyphen-api-secret x-api-key=lower-hyphen-secret; "
        "endpoint https://private.example/v1?token=secret and http://10.0.0.8:8080/run"
    )

    report = build_failure_report(
        prepared, progress=_progress(), error=error,
        source_prompt_hashes={}, existing_artifacts=[],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    assert "hyphen-api-secret" not in report.error_message
    assert "lower-hyphen-secret" not in report.error_message
    assert "https://private.example/v1?token=secret" not in report.error_message
    assert "http://10.0.0.8:8080/run" not in report.error_message
    assert "endpoint [REDACTED]" in report.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_usage",
    [
        {},
        {"prompt": 1, "completion": 2},
        {"prompt": 1, "completion": "2", "total": 3},
        {"prompt": True, "completion": 2, "total": 3},
        {"prompt": -1, "completion": 2, "total": 1},
        {"prompt": 1, "completion": 2, "total": 4},
        ["not", "a", "dict"],
    ],
)
async def test_real_report_marks_malformed_token_usage_unavailable(tmp_path, token_usage):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_real_bad_tokens")
    fake_result = await run_fake_stage(prepared, scenario="improve")
    candidate = OptimizerCandidateProposal.model_validate({
        **fake_result.candidate.model_dump(exclude={"scenario", "seed"}), "provider": "agent_optimizer",
        "optimizer_status": "SUCCEEDED", "finish_reason": "completed",
        "baseline_pass_rate": 1 / 3, "best_pass_rate": 1.0,
        "candidate_id": f"optimizer-{fake_result.candidate.candidate_id[-12:]}",
    })
    native = _optimize_result(
        await prepared.source_target.read_all(), candidate.prompts,
    ).model_copy(update={"total_token_usage": token_usage})
    result = RealStageResult(
        **fake_result.model_dump(exclude={"scenario", "candidate"}), candidate=candidate,
        optimize_result=native,
    )

    report = build_optimization_report(
        prepared, result, progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    assert report.optimizer_resources.token_usage.status == "unavailable"
    assert report.optimizer_resources.token_usage.value is None


@pytest.mark.asyncio
async def test_real_report_allows_consistent_zero_token_usage_without_reflection_calls(tmp_path):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="report_real_zero_tokens")
    fake_result = await run_fake_stage(prepared, scenario="improve")
    candidate = OptimizerCandidateProposal.model_validate({
        **fake_result.candidate.model_dump(exclude={"scenario", "seed"}), "provider": "agent_optimizer",
        "optimizer_status": "SUCCEEDED", "finish_reason": "completed",
        "baseline_pass_rate": 1 / 3, "best_pass_rate": 1.0,
        "candidate_id": f"optimizer-{fake_result.candidate.candidate_id[-12:]}",
    })
    native = _optimize_result(
        await prepared.source_target.read_all(), candidate.prompts,
    ).model_copy(update={
        "total_reflection_lm_calls": 0,
        "total_token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached": 7},
    })
    result = RealStageResult(
        **fake_result.model_dump(exclude={"scenario", "candidate"}), candidate=candidate,
        optimize_result=native,
    )

    report = build_optimization_report(
        prepared, result, progress=_progress(),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    assert report.optimizer_resources.token_usage.status == "available"
    assert report.optimizer_resources.token_usage.value == {
        "prompt": 0, "completion": 0, "total": 0, "cached": 7,
    }


def test_report_dto_invariants_reject_inconsistent_artifact_states():
    with pytest.raises(ValueError, match="available artifacts require"):
        ArtifactReference(
            artifact_id="input", artifact_type="input", required=True,
            produced_by="baseline_train", status="available",
        )
    with pytest.raises(ValueError, match="artifact IDs must be unique"):
        ArtifactIndex(
            run_id="report", generated_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            artifacts=[
                ArtifactReference(
                    artifact_id="input", artifact_type="input", required=True,
                    produced_by="baseline_train", status="unavailable", unavailable_reason="not found",
                ),
                ArtifactReference(
                    artifact_id="input", artifact_type="prompt", required=True,
                    produced_by="candidate_generation", status="unavailable", unavailable_reason="not found",
                ),
            ],
        )


def test_optimizer_resource_value_rejects_negative_numeric_value():
    with pytest.raises(ValueError, match="non-negative"):
        OptimizerResourceValue[int](
            status="available", value=-1, unit="calls",
        )
