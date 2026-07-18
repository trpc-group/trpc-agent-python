"""Pure builders for serializable optimization reports."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .schemas import FailureReport, OptimizerResourceObservation, OptimizationReport
from .schemas import PipelineStageResult, RealStageResult, ReportProgress

if TYPE_CHECKING:
    from .pipeline import PreparedRun

_OPTIMIZER_SCOPE = (
    "Optimizer-only observation; excludes complete business Agent evaluation usage."
)

def _optimizer_resources(result: PipelineStageResult) -> OptimizerResourceObservation:
    if not isinstance(result, RealStageResult):
        return OptimizerResourceObservation(
            status="not_applicable", scope_note="Fake mode does not run AgentOptimizer.",
        )
    native = result.optimize_result
    return OptimizerResourceObservation(
        status="available", scope_note=_OPTIMIZER_SCOPE, total_rounds=native.total_rounds,
        reflection_lm_calls=native.total_reflection_lm_calls, cost_usd=native.total_llm_cost,
        token_usage=dict(native.total_token_usage), duration_seconds=native.duration_seconds,
    )

def build_optimization_report(
    prepared: PreparedRun, result: PipelineStageResult, *, progress: ReportProgress, finished_at: datetime,
) -> OptimizationReport:
    return OptimizationReport(
        run_id=prepared.workspace.run_id, execution_mode=prepared.config.execution.mode,
        seed=prepared.input_snapshot.seed, started_at=progress.started_at, finished_at=finished_at,
        input_snapshot=prepared.input_snapshot, candidate=result.candidate,
        baseline_train=result.baseline_train, baseline_validation=result.baseline_validation,
        candidate_train=result.candidate_train, candidate_validation=result.candidate_validation,
        analysis=result.analysis, pipeline_resources=result.measurements,
        optimizer_resources=_optimizer_resources(result), gate_decision=result.gate_decision,
        writeback=result.writeback,
    )

def build_failure_report(
    prepared: PreparedRun, *, progress: ReportProgress, error: Exception,
    source_prompt_hashes: dict[str, str], existing_artifacts: list[str], generated_at: datetime,
) -> FailureReport:
    return FailureReport(
        run_id=prepared.workspace.run_id, execution_mode=prepared.config.execution.mode,
        failed_phase=progress.current_phase, exception_type=type(error).__name__,
        error_message=str(error), generated_at=generated_at, input_snapshot=prepared.input_snapshot,
        source_prompt_hashes=dict(sorted(source_prompt_hashes.items())),
        completed_phases=progress.completed_phases, existing_artifacts=sorted(existing_artifacts),
    )


def render_optimization_markdown(report: OptimizationReport) -> str:
    decision = report.gate_decision.decision.upper()
    lines = [
        "# Optimization Report",
        "",
        f"- Run: `{report.run_id}`",
        f"- Mode: `{report.execution_mode}`",
        f"- Gate decision: {decision}",
        f"- Candidate: `{report.candidate.candidate_id}`",
        "",
        "## Full Evaluations",
        "",
    ]
    for label, snapshot in (
        ("Baseline train", report.baseline_train),
        ("Baseline validation", report.baseline_validation),
        ("Candidate train", report.candidate_train),
        ("Candidate validation", report.candidate_validation),
    ):
        score = snapshot.average_score if snapshot.average_score is not None else "unavailable"
        lines.append(
            f"- {label}: {snapshot.passed_case_count}/{snapshot.total_case_count} passed; "
            f"average score={score}"
        )
    lines.extend(["", "## Gate", ""])
    lines.extend(f"- Rejection: {reason}" for reason in report.gate_decision.rejection_reasons)
    lines.extend(f"- Warning: {warning}" for warning in report.gate_decision.warnings)
    if not report.gate_decision.rejection_reasons and not report.gate_decision.warnings:
        lines.append("- No rejection reasons or warnings.")
    lines.extend(["", "## Candidate Changes", ""])
    changed = report.candidate.changed_fields or ["none"]
    lines.extend(f"- {field}" for field in changed)
    lines.extend(["", "## Overfit", f"- Status: {report.analysis.overfit_status}",
                  f"- Reason: {report.analysis.overfit_reason}", "", "## Writeback",
                  f"- Status: {report.writeback.status}", f"- Reason: {report.writeback.reason}",
                  "", "## Pipeline Observations",
                  f"- Cost: {report.pipeline_resources.cost_usd.status}",
                  f"- Tokens: {report.pipeline_resources.total_tokens.status}",
                  f"- Duration: {report.pipeline_resources.duration_seconds.status}",
                  "", "## Optimizer Scope",
                  f"- Status: {report.optimizer_resources.status}",
                  f"- {report.optimizer_resources.scope_note}"])
    return "\n".join(lines) + "\n"
