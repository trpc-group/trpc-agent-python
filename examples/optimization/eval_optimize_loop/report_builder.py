"""Pure builders for serializable optimization reports."""

from __future__ import annotations

from datetime import datetime
import os
import re
from typing import TYPE_CHECKING

from .schemas import FailureReport, OptimizerResourceObservation, OptimizerResourceValue
from .schemas import OptimizationReport
from .schemas import PipelineStageResult, RealStageResult, ReportProgress

if TYPE_CHECKING:
    from .pipeline import PreparedRun

_OPTIMIZER_SCOPE = (
    "Optimizer-only observation; excludes complete business Agent evaluation usage."
)
_FAKE_OPTIMIZER_REASON = "Fake mode does not run AgentOptimizer."
_MISSING_COST_REASON = (
    "Reflection LM calls were observed but optimizer cost was not reported."
)
_MISSING_TOKEN_REASON = (
    "Reflection LM calls were observed but optimizer token usage was not reported."
)
_INVALID_TOKEN_REASON = "Optimizer token usage was malformed or inconsistent."
_REDACTED = "[REDACTED]"
_SENSITIVE_ENV_NAMES = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL")
_SENSITIVE_KEY_VALUE = re.compile(
    r"(?P<prefix>[\"']?(?:api[_-]?key|base[_-]?url|authorization)[\"']?\s*[:=]\s*)"
    r"(?P<value>[\"'][^\"']*[\"']|(?:(?:bearer|basic|token)\s+)?[^\s,;}\]]+)",
    re.IGNORECASE,
)
_HTTP_URL = re.compile(r"https?://[^\s,;}\]<>\"']+", re.IGNORECASE)
_BEARER_VALUE = re.compile(
    r"\bbearer(?:\s+|\s*[:=]\s*)[\"']?[^\s,;}\]\"']+[\"']?",
    re.IGNORECASE,
)


def _not_applicable_optimizer_value(
    unit: str,
) -> OptimizerResourceValue[object]:
    return OptimizerResourceValue[object](
        status="not_applicable",
        unit=unit,
        reason=_FAKE_OPTIMIZER_REASON,
    )


def _redact_error_message(error: Exception) -> str:
    message = str(error)
    environment_values = {
        os.environ.get(name, "")
        for name in _SENSITIVE_ENV_NAMES
        if os.environ.get(name, "")
    }
    for sensitive_value in sorted(environment_values, key=len, reverse=True):
        message = message.replace(sensitive_value, _REDACTED)
    message = _SENSITIVE_KEY_VALUE.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED}",
        message,
    )
    message = _BEARER_VALUE.sub(f"Bearer {_REDACTED}", message)
    return _HTTP_URL.sub(_REDACTED, message)


def _is_complete_token_usage(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("prompt", "completion", "total")
    if not all(key in value for key in required):
        return False
    if not all(type(value[key]) is int and value[key] >= 0 for key in required):
        return False
    return value["total"] == value["prompt"] + value["completion"]


def _optimizer_resources(result: PipelineStageResult) -> OptimizerResourceObservation:
    if not isinstance(result, RealStageResult):
        return OptimizerResourceObservation(
            scope_note=_FAKE_OPTIMIZER_REASON,
            total_rounds=_not_applicable_optimizer_value("rounds"),
            reflection_lm_calls=_not_applicable_optimizer_value("calls"),
            cost_usd=_not_applicable_optimizer_value("USD"),
            token_usage=_not_applicable_optimizer_value("tokens"),
            duration_seconds=_not_applicable_optimizer_value("seconds"),
        )
    native = result.optimize_result
    reflection_calls = native.total_reflection_lm_calls
    cost_missing = reflection_calls > 0 and native.total_llm_cost <= 0
    token_usage = native.total_token_usage
    token_usage_valid = _is_complete_token_usage(token_usage)
    tokens_missing = (
        not token_usage_valid
        or (reflection_calls > 0 and token_usage["total"] <= 0)
    )
    return OptimizerResourceObservation(
        scope_note=_OPTIMIZER_SCOPE,
        total_rounds=OptimizerResourceValue[int](
            status="available", value=native.total_rounds, unit="rounds",
        ),
        reflection_lm_calls=OptimizerResourceValue[int](
            status="available", value=reflection_calls, unit="calls",
        ),
        cost_usd=OptimizerResourceValue[float](
            status="unavailable" if cost_missing else "available",
            value=None if cost_missing else native.total_llm_cost,
            unit="USD",
            reason=_MISSING_COST_REASON if cost_missing else None,
        ),
        token_usage=OptimizerResourceValue[dict[str, int]](
            status="unavailable" if tokens_missing else "available",
            value=None if tokens_missing else token_usage,
            unit="tokens",
            reason=(
                _INVALID_TOKEN_REASON
                if tokens_missing and not token_usage_valid
                else _MISSING_TOKEN_REASON if tokens_missing else None
            ),
        ),
        duration_seconds=OptimizerResourceValue[float](
            status="available", value=native.duration_seconds, unit="seconds",
        ),
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
        error_message=_redact_error_message(error), generated_at=generated_at,
        input_snapshot=prepared.input_snapshot,
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
                  "", "## Optimizer Resources"])
    for label, observation in (
        ("Rounds", report.optimizer_resources.total_rounds),
        ("Reflection calls", report.optimizer_resources.reflection_lm_calls),
        ("Cost", report.optimizer_resources.cost_usd),
        ("Token usage", report.optimizer_resources.token_usage),
        ("Duration", report.optimizer_resources.duration_seconds),
    ):
        line = f"- {label}: {observation.status}; unit={observation.unit}"
        if observation.value is not None:
            value = observation.value
            if isinstance(value, dict):
                value = ", ".join(
                    f"{key}={item}" for key, item in sorted(value.items())
                )
            line += f"; value={value}"
        if observation.reason is not None:
            line += f"; reason={observation.reason}"
        lines.append(line)
    lines.extend(["", "## Optimizer Scope", f"- {report.optimizer_resources.scope_note}"])
    return "\n".join(lines) + "\n"
