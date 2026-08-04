# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Candidate generation, Gate evaluation, prompt workspace, and writeback."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal
from typing import Protocol

from trpc_agent_sdk.evaluation import AgentOptimizer
from trpc_agent_sdk.evaluation import CallAgent
from trpc_agent_sdk.evaluation import OptimizeResult
from trpc_agent_sdk.evaluation import TargetPrompt

from ..agent.fake import DeterministicFakeCandidateProvider
from ..data.config import BudgetConfig
from ..data.config import GateConfig
from ..data.config import PromptFieldConfig
from ..data.config import WritebackConfig
from ..data.schemas import CandidateProposal
from ..data.schemas import CandidateScenario
from ..data.schemas import CaseDiff
from ..data.schemas import CaseEvaluation
from ..data.schemas import EvaluationAnalysis
from ..data.schemas import GateDecision
from ..data.schemas import GateRuleId
from ..data.schemas import GateRuleResult
from ..data.schemas import ObservableValue
from ..data.schemas import OptimizerCandidateProposal
from ..data.schemas import OptimizerRuntimeParameters
from ..data.schemas import PromptSnapshot
from ..data.schemas import ResourceMeasurements
from ..data.schemas import WritebackResult
from .reporting import replace_persisted_sensitive_values


class CandidateProviderError(RuntimeError):
    """A provider could not produce a safe, complete candidate."""


def prompt_mapping_sha256(prompts: dict[str, str]) -> str:
    """Hash a complete prompt mapping using a stable JSON representation."""
    canonical = json.dumps(
        prompts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateRequest:
    """Validated inputs handed to one candidate provider."""

    current_prompts: dict[str, str]
    target_prompt: TargetPrompt
    optimizer_config_path: Path
    train_evalset_path: Path
    validation_evalset_path: Path
    output_dir: Path
    seed: int
    retain_native_artifacts: bool = True
    runtime_parameters: OptimizerRuntimeParameters | None = None
    expected_optimizer_sha256: str | None = None


@dataclass(frozen=True)
class CandidateGeneration:
    """A normalized proposal plus an optional native optimizer result."""

    proposal: CandidateProposal
    optimize_result: OptimizeResult | None = None


class CandidateProvider(Protocol):
    """Asynchronous candidate generation used by the pipeline orchestrator."""

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        """Return one complete proposal without updating source prompts."""


class FakeCandidateProviderAdapter:
    """Lift the pure synchronous fake provider into the common async boundary."""

    def __init__(self, scenario: CandidateScenario) -> None:
        self._scenario = scenario

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        proposal = DeterministicFakeCandidateProvider().propose(
            request.current_prompts,
            scenario=self._scenario,
            seed=request.seed,
        )
        return CandidateGeneration(proposal=proposal)


class AgentOptimizerCandidateProvider:
    """Adapt AgentOptimizer to the pipeline's review-before-write contract."""

    def __init__(self, call_agent: CallAgent) -> None:
        self._call_agent = call_agent

    @staticmethod
    def _replace_persisted_connection_values(value: object) -> object:
        """递归将可能被 SDK 复制到产物的连接值替换为环境占位符。"""
        return replace_persisted_sensitive_values(value)

    @staticmethod
    def _prepare_runtime_config(request: CandidateRequest) -> Path:
        """由已校验模板生成无明文凭据的本次运行配置。"""
        if request.runtime_parameters is None:
            return request.optimizer_config_path

        try:
            raw = request.optimizer_config_path.read_bytes()
            if (
                request.expected_optimizer_sha256 is not None
                and sha256(raw).hexdigest() != request.expected_optimizer_sha256
            ):
                raise CandidateProviderError("optimizer config changed after preparation")
            payload = AgentOptimizerCandidateProvider._replace_persisted_connection_values(
                json.loads(raw.decode("utf-8"))
            )
            algorithm = payload["optimize"]["algorithm"]
        except CandidateProviderError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CandidateProviderError(f"failed to prepare optimizer runtime config: {exc}") from exc

        parameters = request.runtime_parameters
        reflection_lm: dict[str, object] = {
            "provider_name": parameters.provider_name,
            "model_name": parameters.model_name,
            "variant": parameters.variant,
            "base_url": "${TRPC_AGENT_BASE_URL}",
            "api_key": "${TRPC_AGENT_API_KEY}",
            "generation_config": {
                "temperature": parameters.temperature,
                "max_tokens": parameters.max_tokens,
            },
        }
        if parameters.think is not None:
            reflection_lm["think"] = parameters.think
        algorithm["reflection_lm"] = reflection_lm
        algorithm["max_candidate_proposals"] = parameters.max_candidate_proposals

        runtime_path = request.output_dir.parent / "optimizer.runtime.json"
        try:
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise CandidateProviderError(f"failed to write optimizer runtime config: {exc}") from exc
        return runtime_path

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        runtime_config_path = self._prepare_runtime_config(request)
        try:
            result = await AgentOptimizer.optimize(
                config_path=str(runtime_config_path),
                call_agent=self._call_agent,
                target_prompt=request.target_prompt,
                train_dataset_path=str(request.train_evalset_path),
                validation_dataset_path=str(request.validation_evalset_path),
                output_dir=str(request.output_dir),
                update_source=False,
                verbose=0,
            )
        except Exception as exc:
            raise CandidateProviderError(f"AgentOptimizer failed: {exc}") from exc

        if result.status != "SUCCEEDED":
            raise CandidateProviderError(
                f"AgentOptimizer returned {result.status}: {result.error_message or result.finish_reason}"
            )
        expected_fields = set(request.current_prompts)
        if set(result.baseline_prompts) != expected_fields:
            raise CandidateProviderError("optimizer baseline prompt fields do not match the prepared target")
        if result.baseline_prompts != request.current_prompts:
            raise CandidateProviderError("optimizer baseline prompts do not match the prepared working prompts")
        if set(result.best_prompts) != expected_fields:
            raise CandidateProviderError("optimizer best prompt fields do not match the prepared target")
        if any(not isinstance(value, str) for value in result.best_prompts.values()):
            raise CandidateProviderError("optimizer best prompts must contain only strings")

        parent_hash = prompt_mapping_sha256(request.current_prompts)
        candidate_hash = prompt_mapping_sha256(result.best_prompts)
        changed_fields = [
            name
            for name in request.current_prompts
            if request.current_prompts[name] != result.best_prompts[name]
        ]
        retained_output_dir = str(request.output_dir) if request.retain_native_artifacts else None
        proposal = OptimizerCandidateProposal(
            prompts=dict(result.best_prompts),
            changed_fields=changed_fields,
            rationale=(
                f"AgentOptimizer selected the best candidate after {result.total_rounds} rounds "
                f"with finish_reason={result.finish_reason}."
            ),
            parent_prompt_sha256=parent_hash,
            candidate_prompt_sha256=candidate_hash,
            candidate_id=f"optimizer-{candidate_hash[:12]}",
            finish_reason=result.finish_reason,
            stop_reason=result.stop_reason,
            baseline_pass_rate=result.baseline_pass_rate,
            best_pass_rate=result.best_pass_rate,
            optimizer_output_dir=retained_output_dir,
        )
        if not request.retain_native_artifacts:
            try:
                shutil.rmtree(request.output_dir)
            except OSError as exc:
                raise CandidateProviderError(
                    f"failed to discard optimizer artifacts: {exc}"
                ) from exc
        return CandidateGeneration(proposal=proposal, optimize_result=result)


class GateEvaluationError(ValueError):
    """Stage 3a analysis is structurally unsafe for Gate evaluation."""


def _case_ids(
    cases: Sequence[CaseEvaluation | CaseDiff],
    *,
    context: str,
) -> set[str]:
    ids = [case.eval_id for case in cases]
    if len(ids) != len(set(ids)):
        raise GateEvaluationError(f"{context} contains duplicate case ids")
    return set(ids)


def _validate_analysis(analysis: EvaluationAnalysis) -> None:
    if analysis.train_diff.split != "train":
        raise GateEvaluationError("train_diff.split must be 'train'")
    if analysis.validation_diff.split != "validation":
        raise GateEvaluationError("validation_diff.split must be 'validation'")

    evaluations = (
        ("baseline_train", analysis.baseline_train, "baseline", "train"),
        ("baseline_validation", analysis.baseline_validation, "baseline", "validation"),
        ("candidate_train", analysis.candidate_train, "candidate", "train"),
        ("candidate_validation", analysis.candidate_validation, "candidate", "validation"),
    )
    for label, evaluation, expected_phase, expected_split in evaluations:
        if evaluation.phase != expected_phase or evaluation.split != expected_split:
            raise GateEvaluationError(f"{label} has an unexpected phase or split")
        _case_ids(evaluation.cases, context=label)
        for case in evaluation.cases:
            metric_names = [metric.metric_name for metric in case.metrics]
            if len(metric_names) != len(set(metric_names)):
                raise GateEvaluationError(
                    f"{label} case {case.eval_id!r} contains duplicate metric names"
                )

    train_diff_ids = _case_ids(analysis.train_diff.cases, context="train_diff")
    validation_diff_ids = _case_ids(
        analysis.validation_diff.cases,
        context="validation_diff",
    )
    candidate_train_ids = {case.eval_id for case in analysis.candidate_train.cases}
    candidate_validation_ids = {
        case.eval_id for case in analysis.candidate_validation.cases
    }
    if train_diff_ids != candidate_train_ids:
        raise GateEvaluationError("train diff and candidate evaluation case ids do not match")
    if validation_diff_ids != candidate_validation_ids:
        raise GateEvaluationError(
            "validation diff and candidate evaluation case ids do not match"
        )


def _evaluation_completeness(analysis: EvaluationAnalysis) -> GateRuleResult:
    incomplete_case_ids: set[str] = set()
    incomplete_metric_names: set[str] = set()
    evaluations = (
        analysis.baseline_train,
        analysis.baseline_validation,
        analysis.candidate_train,
        analysis.candidate_validation,
    )
    complete = True
    for evaluation in evaluations:
        if not evaluation.cases or evaluation.average_score.status != "available":
            complete = False
        for case in evaluation.cases:
            if (
                case.status == "not_evaluated"
                or case.average_score.status != "available"
                or not case.metrics
            ):
                complete = False
                incomplete_case_ids.add(case.eval_id)
            for metric in case.metrics:
                if metric.status == "not_evaluated" or metric.score.status != "available":
                    complete = False
                    incomplete_case_ids.add(case.eval_id)
                    incomplete_metric_names.add(metric.metric_name)
    return GateRuleResult(
        rule_id="evaluation_completeness",
        outcome="pass" if complete else "reject",
        message=(
            "All four evaluations contain complete case and metric results."
            if complete
            else "One or more evaluation cases or metrics are incomplete."
        ),
        case_ids=sorted(incomplete_case_ids),
        metric_names=sorted(incomplete_metric_names),
    )


def _minimum_validation_score_delta(
    analysis: EvaluationAnalysis,
    config: GateConfig,
) -> GateRuleResult:
    delta = analysis.validation_diff.score_delta
    passed = (
        delta.status == "available"
        and float(delta.value) >= config.min_validation_score_delta
    )
    return GateRuleResult(
        rule_id="minimum_validation_score_delta",
        outcome="pass" if passed else "reject",
        message=(
            "Validation score improvement meets the configured minimum."
            if passed
            else "Validation score improvement is unavailable or below the configured minimum."
        ),
        observed={"validation_score_delta": delta},
        threshold=config.min_validation_score_delta,
    )


def _validation_pass_rate(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_on_validation_pass_rate_drop:
        return GateRuleResult(
            rule_id="validation_pass_rate_non_decrease",
            outcome="skipped",
            message="Validation pass-rate protection is disabled.",
        )
    baseline_total = len(analysis.baseline_validation.cases)
    candidate_total = len(analysis.candidate_validation.cases)
    if baseline_total == 0 or candidate_total == 0:
        return GateRuleResult(
            rule_id="validation_pass_rate_non_decrease",
            outcome="reject",
            message="Validation pass rate is unavailable because an evaluation has no cases.",
        )
    baseline_rate = analysis.baseline_validation.passed_case_count / baseline_total
    candidate_rate = analysis.candidate_validation.passed_case_count / candidate_total
    passed = candidate_rate >= baseline_rate
    return GateRuleResult(
        rule_id="validation_pass_rate_non_decrease",
        outcome="pass" if passed else "reject",
        message=(
            "Validation pass rate did not decrease."
            if passed
            else "Validation pass rate decreased from baseline."
        ),
        observed={
            "baseline_validation_pass_rate": ObservableValue(
                status="available", value=baseline_rate, unit="ratio"
            ),
            "candidate_validation_pass_rate": ObservableValue(
                status="available", value=candidate_rate, unit="ratio"
            ),
        },
    )


def _all_case_diffs(analysis: EvaluationAnalysis) -> list[CaseDiff]:
    return sorted(
        [*analysis.train_diff.cases, *analysis.validation_diff.cases],
        key=lambda case: (case.split, case.eval_id),
    )


def _new_hard_failures(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_new_hard_fail:
        return GateRuleResult(
            rule_id="no_new_hard_fail",
            outcome="skipped",
            message="New hard-failure protection is disabled.",
        )
    case_ids = sorted(
        case.eval_id
        for case in _all_case_diffs(analysis)
        if case.is_hard and case.change == "newly_failed"
    )
    return GateRuleResult(
        rule_id="no_new_hard_fail",
        outcome="reject" if case_ids else "pass",
        message=(
            "New hard failures were found."
            if case_ids
            else "No new hard failures were found."
        ),
        case_ids=case_ids,
    )


def _critical_regressions(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    if not config.reject_critical_regression:
        return GateRuleResult(
            rule_id="no_critical_regression",
            outcome="skipped",
            message="Critical-case regression protection is disabled.",
        )
    case_ids = sorted(
        case.eval_id
        for case in _all_case_diffs(analysis)
        if case.is_critical and case.change in {"newly_failed", "regressed"}
    )
    return GateRuleResult(
        rule_id="no_critical_regression",
        outcome="reject" if case_ids else "pass",
        message=(
            "Critical-case regressions were found."
            if case_ids
            else "No critical-case regressions were found."
        ),
        case_ids=case_ids,
    )


def _severe_regressions(analysis: EvaluationAnalysis) -> GateRuleResult:
    case_ids = sorted(
        case.eval_id for case in _all_case_diffs(analysis) if case.severe_regression
    )
    return GateRuleResult(
        rule_id="no_severe_regression",
        outcome="reject" if case_ids else "pass",
        message=(
            "Severe case regressions were found."
            if case_ids
            else "No severe case regressions were found."
        ),
        case_ids=case_ids,
    )


def _required_metrics(analysis: EvaluationAnalysis, config: GateConfig) -> GateRuleResult:
    failed_case_ids: set[str] = set()
    failed_metric_names: set[str] = set()
    for evaluation in (analysis.candidate_train, analysis.candidate_validation):
        for case in evaluation.cases:
            metric_map = {metric.metric_name: metric for metric in case.metrics}
            if config.required_metrics == "all":
                required_names = sorted(metric_map)
                if not required_names:
                    failed_case_ids.add(case.eval_id)
                    continue
            else:
                required_names = sorted(config.required_metrics)
            for name in required_names:
                metric = metric_map.get(name)
                if (
                    metric is None
                    or metric.status != "passed"
                    or metric.score.status != "available"
                ):
                    failed_case_ids.add(case.eval_id)
                    failed_metric_names.add(name)
    return GateRuleResult(
        rule_id="required_metrics",
        outcome="reject" if failed_case_ids else "pass",
        message=(
            "Required metrics are missing, unavailable, or below threshold."
            if failed_case_ids
            else "All required candidate metrics are available and passed."
        ),
        case_ids=sorted(failed_case_ids),
        metric_names=sorted(failed_metric_names),
    )


def _overfitting(analysis: EvaluationAnalysis) -> GateRuleResult:
    passed = analysis.overfit_status == "not_detected"
    return GateRuleResult(
        rule_id="no_overfitting",
        outcome="pass" if passed else "reject",
        message=(
            "No train-improvement/validation-regression pattern was detected."
            if passed
            else f"Overfit status is {analysis.overfit_status!r}: {analysis.overfit_reason}"
        ),
    )


def _budget_result(
    rule_id: GateRuleId,
    measurement_name: str,
    measurement: ObservableValue,
    limit: float | int | None,
    on_unavailable: Literal["reject", "warning"],
) -> GateRuleResult:
    if limit is None:
        return GateRuleResult(
            rule_id=rule_id,
            outcome="skipped",
            message=f"{measurement_name} budget is not configured.",
        )
    if measurement.status != "available":
        return GateRuleResult(
            rule_id=rule_id,
            outcome=on_unavailable,
            message=(
                f"{measurement_name} is unavailable; policy is {on_unavailable}."
            ),
            observed={measurement_name: measurement},
            threshold=float(limit),
        )
    passed = float(measurement.value) <= float(limit)
    return GateRuleResult(
        rule_id=rule_id,
        outcome="pass" if passed else "reject",
        message=(
            f"{measurement_name} is within the configured budget."
            if passed
            else f"{measurement_name} exceeds the configured budget."
        ),
        observed={measurement_name: measurement},
        threshold=float(limit),
    )


def evaluate_gate(
    analysis: EvaluationAnalysis,
    gate_config: GateConfig,
    budget_config: BudgetConfig,
    measurements: ResourceMeasurements,
) -> GateDecision:
    """Evaluate every configured rule and return one complete decision."""
    _validate_analysis(analysis)
    quality_results = [
        _evaluation_completeness(analysis),
        _minimum_validation_score_delta(analysis, gate_config),
        _validation_pass_rate(analysis, gate_config),
        _new_hard_failures(analysis, gate_config),
        _critical_regressions(analysis, gate_config),
        _severe_regressions(analysis),
        _required_metrics(analysis, gate_config),
        _overfitting(analysis),
    ]
    results = quality_results + [
        _budget_result(
            "cost_budget",
            "cost_usd",
            measurements.cost_usd,
            budget_config.max_cost_usd,
            budget_config.on_unavailable,
        ),
        _budget_result(
            "token_budget",
            "total_tokens",
            measurements.total_tokens,
            budget_config.max_tokens,
            budget_config.on_unavailable,
        ),
        _budget_result(
            "duration_budget",
            "duration_seconds",
            measurements.duration_seconds,
            budget_config.max_duration_seconds,
            budget_config.on_unavailable,
        ),
    ]
    rejection_reasons = [result.message for result in results if result.outcome == "reject"]
    warnings = [result.message for result in results if result.outcome == "warning"]
    return GateDecision(
        decision="reject" if rejection_reasons else "accept",
        rule_results=results,
        rejection_reasons=rejection_reasons,
        warnings=warnings,
    )


class PromptWorkspaceError(ValueError):
    """A prompt source cannot safely participate in an isolated run."""


class SourcePromptDriftError(RuntimeError):
    """One or more source prompts changed after the baseline snapshot."""


def resolve_inside_example_root(example_root: Path, relative_path: str, label: str) -> Path:
    """Resolve a configured path and reject traversal or symlink escape."""
    root = example_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PromptWorkspaceError(f"{label} escapes the example root: {relative_path}") from exc
    return candidate


def validate_prompt_sources(example_root: Path, prompts: list[PromptFieldConfig]) -> list[Path]:
    """Validate path-backed, UTF-8 prompt files and return resolved sources."""
    sources: list[Path] = []
    seen_paths: set[Path] = set()
    for prompt in prompts:
        source = resolve_inside_example_root(example_root, prompt.path, f"prompt {prompt.name!r}")
        raw_source = example_root.resolve() / prompt.path
        if raw_source.is_symlink():
            raise PromptWorkspaceError(f"prompt {prompt.name!r} must not be a symlink")
        if not source.is_file():
            raise PromptWorkspaceError(f"prompt {prompt.name!r} is not a regular file: {prompt.path}")
        try:
            source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PromptWorkspaceError(f"prompt {prompt.name!r} is not UTF-8: {prompt.path}") from exc
        if source in seen_paths:
            raise PromptWorkspaceError(f"multiple prompt fields reference {prompt.path}")
        seen_paths.add(source)
        sources.append(source)
    return sources


def verify_source_hashes(snapshots: list[PromptSnapshot]) -> None:
    """Fail if a source prompt no longer matches its preparation snapshot.

    Later writeback code must call this immediately before an ACCEPT write.  It
    is useful in stage one as a read-only concurrency guard; this module does
    not expose a source-writing operation.
    """
    drifted: list[str] = []
    for snapshot in snapshots:
        source = Path(snapshot.source_path)
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            drifted.append(snapshot.field_name)
            continue
        digest = sha256(content.encode("utf-8")).hexdigest()
        if digest != snapshot.sha256:
            drifted.append(snapshot.field_name)
    if drifted:
        raise SourcePromptDriftError(f"source prompt hash changed for fields: {sorted(drifted)}")


def stage_prompt_workspace(
    *,
    example_root: Path,
    staging_run_dir: Path,
    final_run_dir: Path,
    prompts: list[PromptFieldConfig],
    sources: list[Path],
) -> tuple[list[PromptSnapshot], TargetPrompt, TargetPrompt]:
    """Copy prompt sources into a staging run and build source/working targets.

    The returned working target intentionally points at *final* paths.  The
    caller atomically renames ``staging_run_dir`` into ``final_run_dir`` only
    once every source has been copied, so no later phase can observe a partial
    prompt workspace.
    """
    prompts_dir = staging_run_dir / "workspace" / "prompts"
    prompts_dir.mkdir(parents=True)

    source_target = TargetPrompt()
    working_target = TargetPrompt()
    snapshots: list[PromptSnapshot] = []

    for index, (prompt, source) in enumerate(zip(prompts, sources, strict=True), start=1):
        content = source.read_text(encoding="utf-8")
        suffix = source.suffix or ".txt"
        working_name = f"{index:02d}_{prompt.name}{suffix}"
        staged_path = prompts_dir / working_name
        final_path = final_run_dir / "workspace" / "prompts" / working_name
        staged_path.write_text(content, encoding="utf-8")

        source_target.add_path(prompt.name, str(source))
        working_target.add_path(prompt.name, str(final_path))
        snapshots.append(
            PromptSnapshot(
                field_name=prompt.name,
                source_path=str(source),
                working_path=str(final_path),
                content=content,
                sha256=sha256(content.encode("utf-8")).hexdigest(),
            ))

    return snapshots, source_target, working_target


class WritebackIntegrityError(RuntimeError):
    """The pipeline cannot prove that source prompts remain in a safe state."""


def _field_hashes(prompts: dict[str, str]) -> dict[str, str]:
    return {
        name: sha256(content.encode("utf-8")).hexdigest()
        for name, content in prompts.items()
    }


async def _blocked_for_drift(
    source_target: TargetPrompt,
    message: str,
) -> WritebackResult:
    try:
        observed = await source_target.read_all()
    except Exception:
        observed = {}
    return WritebackResult(
        status="blocked",
        reason="source_drift",
        source_hashes_before=_field_hashes(observed),
        error_message=message,
    )


async def _restore_and_verify(
    source_target: TargetPrompt,
    baseline: dict[str, str],
) -> dict[str, str]:
    """Restore only when needed, then prove the exact baseline is present."""
    try:
        current = await source_target.read_all()
    except Exception:
        current = None
    if current != baseline:
        try:
            await source_target.write_all(baseline)
        except Exception as exc:
            raise WritebackIntegrityError(f"source prompt rollback failed: {exc}") from exc
    try:
        restored = await source_target.read_all()
    except Exception as exc:
        raise WritebackIntegrityError(f"failed to verify source prompt rollback: {exc}") from exc
    if restored != baseline:
        raise WritebackIntegrityError("source prompts do not match the pre-write snapshot after rollback")
    return restored


async def perform_writeback(
    *,
    decision: GateDecision,
    config: WritebackConfig,
    snapshots: list[PromptSnapshot],
    source_target: TargetPrompt,
    candidate: CandidateProposal,
) -> WritebackResult:
    """Apply a candidate only after ACCEPT and return a structured outcome."""
    if decision.decision == "reject":
        return WritebackResult(status="skipped", reason="gate_rejected")
    if not config.enabled:
        return WritebackResult(status="skipped", reason="disabled")
    if not config.require_source_hash_match:
        raise WritebackIntegrityError("enabled writeback requires source hash verification")
    if prompt_mapping_sha256(candidate.prompts) != candidate.candidate_prompt_sha256:
        raise WritebackIntegrityError("candidate prompt hash does not match its prompt payload")

    try:
        verify_source_hashes(snapshots)
    except SourcePromptDriftError as exc:
        return await _blocked_for_drift(source_target, str(exc))

    try:
        baseline = await source_target.read_all()
    except Exception as exc:
        return WritebackResult(
            status="failed",
            reason="write_error",
            error_message=f"failed to read source prompts before writeback: {exc}",
        )
    expected_baseline = {snapshot.field_name: snapshot.content for snapshot in snapshots}
    if baseline != expected_baseline:
        return await _blocked_for_drift(
            source_target,
            "source prompts changed after the initial hash check",
        )
    hashes_before = _field_hashes(baseline)

    # This synchronous check is intentionally adjacent to the path-backed
    # write. It narrows the compare/write window after the awaited read above.
    try:
        verify_source_hashes(snapshots)
    except SourcePromptDriftError as exc:
        return await _blocked_for_drift(source_target, str(exc))

    try:
        await source_target.write_all(candidate.prompts)
    except Exception as exc:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="write_error",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message=str(exc),
        )

    try:
        written = await source_target.read_all()
    except Exception as exc:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="readback_mismatch",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message=f"failed to read source prompts after writeback: {exc}",
        )
    if written != candidate.prompts:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="readback_mismatch",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message="source prompt readback did not match the accepted candidate",
        )

    return WritebackResult(
        status="written",
        reason="written",
        attempted=True,
        changed_fields=list(candidate.changed_fields),
        source_hashes_before=hashes_before,
        source_hashes_after=_field_hashes(written),
    )
