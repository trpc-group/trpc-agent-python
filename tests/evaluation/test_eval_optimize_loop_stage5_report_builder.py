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
    ArtifactIndex, ArtifactReference, OptimizationReport, OptimizerResourceObservation, ReportProgress,
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
    assert report.optimizer_resources.status == "not_applicable"
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
    assert report.optimizer_resources.status == "available"
    assert report.optimizer_resources.total_rounds == 1
    assert report.optimizer_resources.reflection_lm_calls == 1
    assert report.optimizer_resources.scope_note == (
        "Optimizer-only observation; excludes complete business Agent evaluation usage."
    )

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

def test_report_dto_invariants_reject_inconsistent_resource_and_artifact_states():
    with pytest.raises(ValueError, match="available optimizer observations"):
        OptimizerResourceObservation(status="available", scope_note="missing values")
    with pytest.raises(ValueError, match="non-available optimizer observations"):
        OptimizerResourceObservation(status="unavailable", scope_note="none", total_rounds=1)
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
