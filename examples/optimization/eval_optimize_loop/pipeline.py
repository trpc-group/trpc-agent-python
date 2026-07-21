# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Preparation, candidate regression, Gate, and guarded writeback orchestration."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import CallAgent
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import OptimizeConfigFile
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.evaluation import load_optimize_config

from .analysis import build_evaluation_analysis
from .business_agent import BusinessAgent
from .artifact_writer import discover_run_artifacts
from .artifact_writer import publish_report_bundle
from .artifact_writer import write_failure_report
from .candidate_provider import AgentOptimizerCandidateProvider
from .candidate_provider import CandidateProviderError
from .candidate_provider import CandidateRequest
from .candidate_provider import FakeCandidateProviderAdapter
from .config import PipelineConfig
from .config import load_pipeline_config
from .evaluation_adapter import EvaluationAnalysisError
from .fake import DeterministicFakeModel
from .gate import evaluate_gate
from .gate import GateEvaluationError
from .prompt_workspace import PromptWorkspaceError
from .prompt_workspace import resolve_inside_example_root
from .prompt_workspace import stage_prompt_workspace
from .prompt_workspace import validate_prompt_sources
from .report_builder import build_failure_report
from .report_builder import build_optimization_report
from .schemas import InputSnapshot
from .schemas import CandidateScenario
from .schemas import EvaluationSnapshot
from .schemas import OfflineStageResult
from .schemas import ObservableValue
from .schemas import OptimizerRuntimeParameters
from .schemas import ResourceMeasurements
from .schemas import RealStageResult
from .schemas import ReportPhase
from .schemas import ReportProgress
from .schemas import TraceCandidateProposal
from .schemas import TraceInputSnapshot
from .schemas import TracePromptSnapshot
from .schemas import TraceScenarioInputSnapshot
from .schemas import TraceStageResult
from .schemas import WorkspaceSnapshot
from .schemas import WritebackResult
from .writeback import perform_writeback


class PipelinePreparationError(ValueError):
    """The example cannot safely prepare an evaluation/optimization run."""


class PipelineExecutionError(RuntimeError):
    """A prepared pipeline run could not complete safely."""


# Compatibility for callers and tests written before the real-mode stage.
PipelineStageExecutionError = PipelineExecutionError


@dataclass(frozen=True)
class PreparedRun:
    """Validated inputs and isolated prompts handed to the next pipeline phase."""

    config: PipelineConfig
    optimizer_config: OptimizeConfigFile
    input_snapshot: InputSnapshot
    workspace: WorkspaceSnapshot
    source_target: TargetPrompt
    working_target: TargetPrompt
    example_root: Path


@dataclass
class _MutableReportProgress:
    """Track the active report phase without marking it complete too early."""

    started_at: datetime
    current_phase: ReportPhase = "baseline_train"
    completed_phases: list[ReportPhase] = field(default_factory=list)

    def enter(self, phase: ReportPhase) -> None:
        if self.current_phase not in self.completed_phases and self.current_phase != phase:
            self.completed_phases.append(self.current_phase)
        self.current_phase = phase

    def snapshot(self) -> ReportProgress:
        return ReportProgress(
            started_at=self.started_at,
            current_phase=self.current_phase,
            completed_phases=list(self.completed_phases),
        )


async def _source_prompt_hashes(prepared: PreparedRun) -> dict[str, str]:
    try:
        prompts = await prepared.source_target.read_all()
    except Exception:
        # Failure evidence must remain writable even when the source itself is
        # unavailable. An empty mapping means the final source state could not
        # be observed; it must never be replaced with stale snapshot hashes.
        return {}
    return {
        name: sha256(value.encode("utf-8")).hexdigest()
        for name, value in sorted(prompts.items())
    }


async def _record_failure(
    prepared: PreparedRun,
    progress: _MutableReportProgress,
    error: Exception,
) -> None:
    run_dir = Path(prepared.workspace.run_dir)
    existing = discover_run_artifacts(run_dir)
    report = build_failure_report(
        prepared,
        progress=progress.snapshot(),
        error=error,
        source_prompt_hashes=await _source_prompt_hashes(prepared),
        existing_artifacts=existing,
        generated_at=datetime.now(timezone.utc),
    )
    write_failure_report(report, run_dir=run_dir)


async def _rollback_written_source(
    prepared: PreparedRun,
    result: OfflineStageResult | RealStageResult | TraceStageResult,
) -> None:
    """Restore the prepared source Prompt if success reporting cannot publish."""
    if result.writeback.status != "written":
        return
    baseline = {
        snapshot.field_name: snapshot.content
        for snapshot in prepared.input_snapshot.prompt_snapshots
    }
    current = await prepared.source_target.read_all()
    if current != result.candidate.prompts:
        raise PipelineExecutionError(
            "source Prompt changed after writeback; refusing reporting-failure rollback"
        )
    # Path-backed TargetPrompt.write_all performs its atomic replacements
    # synchronously, so this task does not yield between the adjacent check and
    # write. Callback-backed sources retain the caller's documented atomicity
    # responsibility, as they do for the normal writeback path.
    await prepared.source_target.write_all(baseline)
    restored = await prepared.source_target.read_all()
    if restored != baseline:
        raise PipelineExecutionError(
            "source Prompt rollback after reporting failure could not be verified"
        )


async def _handle_stage_failure(
    prepared: PreparedRun,
    progress: _MutableReportProgress,
    error: Exception,
    result: OfflineStageResult | RealStageResult | TraceStageResult | None,
) -> None:
    failure_error: Exception = error
    if progress.current_phase == "reporting" and result is not None:
        try:
            await _rollback_written_source(prepared, result)
        except Exception as rollback_exc:
            failure_error = PipelineExecutionError(
                f"{error}; additionally failed to roll back source Prompt: {rollback_exc}"
            )
    try:
        await _record_failure(prepared, progress, failure_error)
    except Exception as report_exc:
        raise PipelineExecutionError(
            f"{failure_error}; additionally failed to write failure report: {report_exc}"
        ) from error
    if failure_error is not error:
        raise failure_error from error


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _load_evalset(path: Path, label: str) -> EvalSet:
    if not path.is_file():
        raise PipelinePreparationError(f"{label} must be a file: {path}")
    try:
        return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise PipelinePreparationError(f"{label} is not UTF-8: {path}") from exc
    except Exception as exc:
        raise PipelinePreparationError(f"{label} is not a valid EvalSet: {path}: {exc}") from exc


def _validate_trace_evalset(eval_set: EvalSet, label: str) -> None:
    invalid = [
        case.eval_id
        for case in eval_set.eval_cases
        if case.eval_mode != "trace" or not case.actual_conversation
    ]
    if invalid:
        raise PipelinePreparationError(
            f"{label} requires eval_mode='trace' and actual_conversation: {invalid}"
        )


def _validate_eval_case_ids(train: EvalSet, validation: EvalSet, config: PipelineConfig) -> None:
    train_ids = [case.eval_id for case in train.eval_cases]
    validation_ids = [case.eval_id for case in validation.eval_cases]
    for label, ids in (("train", train_ids), ("validation", validation_ids)):
        if len(ids) != len(set(ids)):
            raise PipelinePreparationError(f"{label} evalset contains duplicate eval_id values")
    if set(train_ids) & set(validation_ids):
        raise PipelinePreparationError("train and validation evalsets must not share eval_id values")

    known_ids = set(train_ids) | set(validation_ids)
    labels = set(config.case_labels.hard_case_ids) | set(config.case_labels.critical_case_ids)
    unknown = sorted(labels - known_ids)
    if unknown:
        raise PipelinePreparationError(f"case_labels reference unknown eval_id values: {unknown}")


def _validate_gate_metrics(config: PipelineConfig, optimizer_config: object) -> None:
    required = config.gate.required_metrics
    if not isinstance(required, list):
        return
    available = {metric.metric_name for metric in optimizer_config.evaluate.get_eval_metrics()}
    unknown = sorted(set(required) - available)
    if unknown:
        raise PipelinePreparationError(
            f"gate.required_metrics references unknown metrics {unknown}; available metrics: {sorted(available)}")


def _resolve_inputs(example_root: Path, config: PipelineConfig) -> tuple[Path, Path, Path]:
    train_path = resolve_inside_example_root(example_root, config.inputs.train_evalset, "train_evalset")
    validation_path = resolve_inside_example_root(example_root, config.inputs.validation_evalset, "validation_evalset")
    optimizer_path = resolve_inside_example_root(example_root, config.inputs.optimizer_config, "optimizer_config")
    if train_path == validation_path:
        raise PipelinePreparationError("train_evalset and validation_evalset must be different files")
    if not optimizer_path.is_file():
        raise PipelinePreparationError(f"optimizer_config must be a file: {optimizer_path}")
    return train_path, validation_path, optimizer_path


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise PipelinePreparationError("run_id may contain only letters, numbers, underscores, and hyphens")
    return run_id


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _verify_prepared_file(path: Path, *, label: str, expected_sha256: str) -> None:
    """Reject an input whose bytes changed after ``prepare_run``."""
    try:
        actual_sha256 = _file_sha256(path)
    except OSError as exc:
        raise PipelineStageExecutionError(f"failed to reload prepared {label}: {path}: {exc}") from exc
    if actual_sha256 != expected_sha256:
        raise PipelineStageExecutionError(
            f"{label} changed after prepare_run: {path}; "
            f"expected sha256 {expected_sha256}, got {actual_sha256}"
        )


def _reload_prepared_evalset(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
) -> EvalSet:
    """Reload exactly the evalset bytes whose identity was prepared."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PipelineStageExecutionError(f"failed to reload prepared {label}: {path}: {exc}") from exc

    actual_sha256 = sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PipelineStageExecutionError(
            f"{label} changed after prepare_run: {path}; "
            f"expected sha256 {expected_sha256}, got {actual_sha256}"
        )

    try:
        return EvalSet.model_validate_json(payload)
    except Exception as exc:
        raise PipelineStageExecutionError(f"prepared {label} is no longer a valid EvalSet: {path}: {exc}") from exc


def _prepare_trace_inputs(
    example_root: Path,
    config: PipelineConfig,
    baseline_train: EvalSet,
    baseline_validation: EvalSet,
) -> TraceInputSnapshot | None:
    if config.execution.mode != "trace":
        return None
    _validate_trace_evalset(baseline_train, "baseline train trace")
    _validate_trace_evalset(baseline_validation, "baseline validation trace")
    assert config.trace_inputs is not None
    train_ids = {case.eval_id for case in baseline_train.eval_cases}
    validation_ids = {case.eval_id for case in baseline_validation.eval_cases}
    scenarios: dict[str, TraceScenarioInputSnapshot] = {}
    for scenario, inputs in config.trace_inputs.candidates.items():
        train_path = resolve_inside_example_root(
            example_root, inputs.train_evalset, f"trace {scenario} train"
        )
        validation_path = resolve_inside_example_root(
            example_root,
            inputs.validation_evalset,
            f"trace {scenario} validation",
        )
        train = _load_evalset(train_path, f"trace {scenario} train")
        validation = _load_evalset(
            validation_path, f"trace {scenario} validation"
        )
        _validate_trace_evalset(train, f"trace {scenario} train")
        _validate_trace_evalset(validation, f"trace {scenario} validation")
        if {case.eval_id for case in train.eval_cases} != train_ids:
            raise PipelinePreparationError(
                f"trace {scenario} train eval IDs must match baseline"
            )
        if {case.eval_id for case in validation.eval_cases} != validation_ids:
            raise PipelinePreparationError(
                f"trace {scenario} validation eval IDs must match baseline"
            )
        prompt_snapshots: list[TracePromptSnapshot] = []
        for prompt in inputs.prompts:
            path = resolve_inside_example_root(
                example_root, prompt.path, f"trace {scenario} prompt"
            )
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise PipelinePreparationError(
                    f"trace {scenario} prompt is invalid: {path}: {exc}"
                ) from exc
            prompt_snapshots.append(
                TracePromptSnapshot(
                    field_name=prompt.name,
                    path=str(path),
                    content=content,
                    sha256=_file_sha256(path),
                )
            )
        scenarios[scenario] = TraceScenarioInputSnapshot(
            train_evalset_path=str(train_path),
            train_evalset_sha256=_file_sha256(train_path),
            validation_evalset_path=str(validation_path),
            validation_evalset_sha256=_file_sha256(validation_path),
            prompt_snapshots=prompt_snapshots,
        )
    return TraceInputSnapshot(scenarios=scenarios)


def prepare_run(pipeline_config_path: str | Path, *, run_id: str | None = None) -> PreparedRun:
    """Prepare a run without evaluating, optimizing, reporting, or writing a source prompt.

    All configuration and input validation completes before a staging directory
    is created.  The final run directory appears only through an atomic rename,
    and an exception removes the staging directory.  This keeps failed setup
    from looking like a runnable or audited pipeline result.
    """
    config_path = Path(pipeline_config_path).resolve()
    config = load_pipeline_config(config_path)
    example_root = config_path.parent

    train_path, validation_path, optimizer_path = _resolve_inputs(example_root, config)
    train_evalset = _load_evalset(train_path, "train_evalset")
    validation_evalset = _load_evalset(validation_path, "validation_evalset")
    _validate_eval_case_ids(train_evalset, validation_evalset, config)
    trace_inputs = _prepare_trace_inputs(
        example_root, config, train_evalset, validation_evalset
    )

    try:
        optimizer_config = load_optimize_config(str(optimizer_path))
    except Exception as exc:
        raise PipelinePreparationError(f"optimizer_config is invalid: {optimizer_path}: {exc}") from exc
    if not optimizer_config.evaluate.get_eval_metrics():
        raise PipelinePreparationError("optimizer_config must define at least one evaluation metric")
    if optimizer_config.evaluate.num_runs < 1:
        raise PipelinePreparationError("optimizer_config evaluate.num_runs must be at least 1")
    if optimizer_config.optimize.eval_case_parallelism < 1:
        raise PipelinePreparationError("optimizer_config optimize.eval_case_parallelism must be at least 1")
    _validate_gate_metrics(config, optimizer_config)

    try:
        prompt_sources = validate_prompt_sources(example_root, config.prompts)
        runs_dir = resolve_inside_example_root(example_root, config.run.runs_dir, "runs_dir")
    except PromptWorkspaceError as exc:
        raise PipelinePreparationError(str(exc)) from exc

    configured_run_id = run_id if run_id is not None else config.run.run_id
    selected_run_id = _validate_run_id(configured_run_id or _new_run_id())
    runs_dir.mkdir(parents=True, exist_ok=True)
    final_run_dir = runs_dir / selected_run_id
    if final_run_dir.exists():
        raise FileExistsError(f"run directory already exists: {final_run_dir}")

    staging_run_dir = runs_dir / f".{selected_run_id}.tmp-{uuid4().hex}"
    try:
        staging_run_dir.mkdir()
        prompt_snapshots, source_target, working_target = stage_prompt_workspace(
            example_root=example_root,
            staging_run_dir=staging_run_dir,
            final_run_dir=final_run_dir,
            prompts=config.prompts,
            sources=prompt_sources,
        )
        workspace_dir = final_run_dir / "workspace"
        workspace = WorkspaceSnapshot(
            run_id=selected_run_id,
            run_dir=str(final_run_dir),
            workspace_dir=str(workspace_dir),
            prompts_dir=str(workspace_dir / "prompts"),
        )
        input_snapshot = InputSnapshot(
            pipeline_config_path=str(config_path),
            pipeline_config_sha256=_file_sha256(config_path),
            optimizer_config_path=str(optimizer_path),
            optimizer_config_sha256=_file_sha256(optimizer_path),
            train_evalset_path=str(train_path),
            train_evalset_sha256=_file_sha256(train_path),
            validation_evalset_path=str(validation_path),
            validation_evalset_sha256=_file_sha256(validation_path),
            prompt_snapshots=prompt_snapshots,
            seed=config.run.seed,
            trace_inputs=trace_inputs,
        )
        prepared = PreparedRun(
            config=config,
            optimizer_config=optimizer_config,
            input_snapshot=input_snapshot,
            workspace=workspace,
            source_target=source_target,
            working_target=working_target,
            example_root=example_root,
        )
        staging_run_dir.replace(final_run_dir)
        return prepared
    except BaseException:
        shutil.rmtree(staging_run_dir, ignore_errors=True)
        raise


def _summarize_results(
    eval_results_by_eval_id: dict[str, list[EvalCaseResult]],
) -> tuple[int, int, float | None]:
    passed_cases = 0
    scores: list[float] = []
    for runs in eval_results_by_eval_id.values():
        if runs and all(getattr(run, "final_eval_status", None) == EvalStatus.PASSED for run in runs):
            passed_cases += 1
        for run in runs:
            for metric in getattr(run, "overall_eval_metric_results", []):
                if metric.score is not None:
                    scores.append(float(metric.score))
    average_score = sum(scores) / len(scores) if scores else None
    return passed_cases, len(eval_results_by_eval_id), average_score


def _validate_results(
    *,
    eval_set: EvalSet,
    eval_results_by_eval_id: dict[str, list[EvalCaseResult]],
    num_runs: int,
    phase: Literal["baseline", "candidate"],
    split: Literal["train", "validation"],
) -> None:
    expected_ids = {case.eval_id for case in eval_set.eval_cases}
    actual_ids = set(eval_results_by_eval_id)
    if actual_ids != expected_ids:
        raise PipelineStageExecutionError(
            f"{phase} {split} evaluation returned case ids {sorted(actual_ids)}; "
            f"expected {sorted(expected_ids)}"
        )
    wrong_run_counts = {
        eval_id: len(results)
        for eval_id, results in eval_results_by_eval_id.items()
        if len(results) != num_runs
    }
    if wrong_run_counts:
        raise PipelineStageExecutionError(
            f"{phase} {split} evaluation returned unexpected run counts: {wrong_run_counts}; "
            f"expected {num_runs}"
        )


async def _evaluate_split(
    *,
    prepared: PreparedRun,
    eval_set: EvalSet,
    call_agent: CallAgent | None,
    phase: Literal["baseline", "candidate"],
    split: Literal["train", "validation"],
) -> EvaluationSnapshot:
    num_runs = prepared.optimizer_config.evaluate.num_runs
    try:
        failed_summary, details_lines, result_lines, eval_results_by_eval_id = (
            await AgentEvaluator.evaluate_eval_set(
                eval_set,
                call_agent=call_agent,
                eval_config=prepared.optimizer_config.evaluate,
                num_runs=num_runs,
                print_detailed_results=False,
                case_parallelism=prepared.optimizer_config.optimize.eval_case_parallelism,
                case_eval_parallelism=prepared.optimizer_config.optimize.eval_case_parallelism,
            )
        )
    except Exception as exc:
        raise PipelineStageExecutionError(f"{phase} {split} evaluation failed: {exc}") from exc

    _validate_results(
        eval_set=eval_set,
        eval_results_by_eval_id=eval_results_by_eval_id,
        num_runs=num_runs,
        phase=phase,
        split=split,
    )
    passed_cases, total_cases, average_score = _summarize_results(eval_results_by_eval_id)
    return EvaluationSnapshot(
        phase=phase,
        split=split,
        eval_set_id=eval_set.eval_set_id,
        failed_summary=failed_summary,
        details_lines=details_lines,
        result_lines=result_lines,
        eval_results_by_eval_id=eval_results_by_eval_id,
        passed_case_count=passed_cases,
        total_case_count=total_cases,
        average_score=average_score,
    )


async def _restore_working_baseline(
    prepared: PreparedRun,
    baseline_prompts: dict[str, str],
) -> bool:
    """Restore optimizer leftovers and prove the isolated baseline is present."""
    try:
        current = await prepared.working_target.read_all()
        was_modified = current != baseline_prompts
        if was_modified:
            await prepared.working_target.write_all(baseline_prompts)
        restored = await prepared.working_target.read_all()
    except Exception as exc:
        raise PipelineStageExecutionError(f"failed to restore optimizer working prompts: {exc}") from exc
    if restored != baseline_prompts:
        raise PipelineStageExecutionError("optimizer working prompts did not match baseline after restoration")
    return was_modified


async def _execute_offline_stage(
    prepared: PreparedRun,
    *,
    scenario: CandidateScenario | None = None,
    progress: _MutableReportProgress,
) -> OfflineStageResult:
    """Run four evaluations through SDK LlmAgent and a deterministic model.

    Source prompts are never written. Once generated, the candidate remains in
    the isolated working target on success or candidate-evaluation failure so
    the run can be inspected later.
    """
    progress.enter("baseline_train")
    if prepared.config.execution.mode != "offline":
        raise PipelineStageExecutionError(
            "run_offline_stage requires execution.mode='offline', got "
            f"{prepared.config.execution.mode!r}"
        )

    started_at = perf_counter()
    selected_scenario = scenario or prepared.config.execution.candidate_scenario
    train_evalset = _reload_prepared_evalset(
        Path(prepared.input_snapshot.train_evalset_path),
        label="train_evalset",
        expected_sha256=prepared.input_snapshot.train_evalset_sha256,
    )
    validation_evalset = _reload_prepared_evalset(
        Path(prepared.input_snapshot.validation_evalset_path),
        label="validation_evalset",
        expected_sha256=prepared.input_snapshot.validation_evalset_sha256,
    )

    try:
        baseline_prompts = await prepared.working_target.read_all()
    except Exception as exc:
        raise PipelineStageExecutionError(f"failed to read prepared working prompts: {exc}") from exc
    expected_baseline = {
        snapshot.field_name: snapshot.content for snapshot in prepared.input_snapshot.prompt_snapshots
    }
    if baseline_prompts != expected_baseline:
        raise PipelineStageExecutionError("working prompts no longer match the prepared baseline snapshot")

    agent = BusinessAgent(
        prepared.working_target,
        DeterministicFakeModel,
        agent_name="eval_optimize_offline_agent",
        app_name="eval_optimize_offline",
        user_id="offline-evaluation",
    )
    progress.enter("baseline_train")
    baseline_train = await _evaluate_split(
        prepared=prepared,
        eval_set=train_evalset,
        call_agent=agent.call_agent,
        phase="baseline",
        split="train",
    )
    progress.enter("baseline_validation")
    baseline_validation = await _evaluate_split(
        prepared=prepared,
        eval_set=validation_evalset,
        call_agent=agent.call_agent,
        phase="baseline",
        split="validation",
    )

    progress.enter("candidate_generation")
    request = CandidateRequest(
        current_prompts=baseline_prompts,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=Path(prepared.workspace.run_dir) / "fake_provider",
        seed=prepared.input_snapshot.seed,
    )
    try:
        generated = await FakeCandidateProviderAdapter(selected_scenario).propose(request)
        candidate = generated.proposal
    except Exception as exc:
        raise PipelineStageExecutionError(f"fake candidate generation failed: {exc}") from exc

    try:
        await prepared.working_target.write_all(candidate.prompts)
        written_prompts = await prepared.working_target.read_all()
    except Exception as exc:
        raise PipelineStageExecutionError(f"candidate prompt write failed: {exc}") from exc
    if written_prompts != candidate.prompts:
        raise PipelineStageExecutionError("candidate prompt readback did not match the generated proposal")

    progress.enter("candidate_train")
    candidate_train = await _evaluate_split(
        prepared=prepared,
        eval_set=train_evalset,
        call_agent=agent.call_agent,
        phase="candidate",
        split="train",
    )
    progress.enter("candidate_validation")
    candidate_validation = await _evaluate_split(
        prepared=prepared,
        eval_set=validation_evalset,
        call_agent=agent.call_agent,
        phase="candidate",
        split="validation",
    )
    progress.enter("analysis")
    try:
        analysis = build_evaluation_analysis(
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=candidate_train,
            candidate_validation=candidate_validation,
            hard_case_ids=set(prepared.config.case_labels.hard_case_ids),
            critical_case_ids=set(prepared.config.case_labels.critical_case_ids),
            severe_case_score_drop=prepared.config.gate.severe_case_score_drop,
        )
    except EvaluationAnalysisError as exc:
        raise PipelineStageExecutionError(f"stage 3a analysis failed: {exc}") from exc
    measurements = ResourceMeasurements(
        cost_usd=ObservableValue(
            status="unavailable",
            unit="USD",
            reason="Offline deterministic model does not report monetary cost.",
        ),
        total_tokens=ObservableValue(
            status="unavailable",
            unit="tokens",
            reason="Offline deterministic model does not report token usage.",
        ),
        duration_seconds=ObservableValue(
            status="available",
            value=perf_counter() - started_at,
            unit="seconds",
        ),
    )
    progress.enter("gate")
    try:
        gate_decision = evaluate_gate(
            analysis,
            prepared.config.gate,
            prepared.config.budget,
            measurements,
        )
    except GateEvaluationError as exc:
        raise PipelineStageExecutionError(f"stage 3b gate failed: {exc}") from exc
    progress.enter("writeback")
    writeback = await perform_writeback(
        decision=gate_decision,
        config=prepared.config.writeback,
        snapshots=prepared.input_snapshot.prompt_snapshots,
        source_target=prepared.source_target,
        candidate=candidate,
    )
    return OfflineStageResult(
        scenario=selected_scenario,
        candidate=candidate,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        analysis=analysis,
        measurements=measurements,
        gate_decision=gate_decision,
        writeback=writeback,
    )


async def _execute_real_stage(
    prepared: PreparedRun,
    *,
    call_agent: CallAgent,
    optimizer_parameters: OptimizerRuntimeParameters | None = None,
    progress: _MutableReportProgress,
) -> RealStageResult:
    """Generate a real optimizer candidate and run the full guarded regression."""
    progress.enter("baseline_train")
    if prepared.config.execution.mode != "real":
        raise PipelineStageExecutionError(
            f"run_real_stage requires execution.mode='real', got {prepared.config.execution.mode!r}"
        )
    started_at = perf_counter()
    _verify_prepared_file(
        Path(prepared.input_snapshot.optimizer_config_path),
        label="optimizer_config",
        expected_sha256=prepared.input_snapshot.optimizer_config_sha256,
    )
    train_evalset = _reload_prepared_evalset(
        Path(prepared.input_snapshot.train_evalset_path),
        label="train_evalset",
        expected_sha256=prepared.input_snapshot.train_evalset_sha256,
    )
    validation_evalset = _reload_prepared_evalset(
        Path(prepared.input_snapshot.validation_evalset_path),
        label="validation_evalset",
        expected_sha256=prepared.input_snapshot.validation_evalset_sha256,
    )
    try:
        baseline_prompts = await prepared.working_target.read_all()
    except Exception as exc:
        raise PipelineStageExecutionError(f"failed to read prepared working prompts: {exc}") from exc
    expected_baseline = {
        snapshot.field_name: snapshot.content for snapshot in prepared.input_snapshot.prompt_snapshots
    }
    if baseline_prompts != expected_baseline:
        raise PipelineStageExecutionError("working prompts no longer match the prepared baseline snapshot")

    progress.enter("baseline_train")
    baseline_train = await _evaluate_split(
        prepared=prepared,
        eval_set=train_evalset,
        call_agent=call_agent,
        phase="baseline",
        split="train",
    )
    progress.enter("baseline_validation")
    baseline_validation = await _evaluate_split(
        prepared=prepared,
        eval_set=validation_evalset,
        call_agent=call_agent,
        phase="baseline",
        split="validation",
    )

    progress.enter("candidate_generation")
    request = CandidateRequest(
        current_prompts=baseline_prompts,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=Path(prepared.workspace.run_dir) / "optimizer",
        seed=prepared.input_snapshot.seed,
        retain_native_artifacts=prepared.config.artifacts.retain_optimizer_native_artifacts,
        runtime_parameters=optimizer_parameters,
        expected_optimizer_sha256=prepared.input_snapshot.optimizer_config_sha256,
    )
    try:
        generated = await AgentOptimizerCandidateProvider(call_agent).propose(request)
    except CandidateProviderError as exc:
        await _restore_working_baseline(prepared, baseline_prompts)
        raise PipelineStageExecutionError(f"real candidate generation failed: {exc}") from exc

    if await _restore_working_baseline(prepared, baseline_prompts):
        raise PipelineStageExecutionError("optimizer did not restore working prompts after update_source=False")

    candidate = generated.proposal
    try:
        await prepared.working_target.write_all(candidate.prompts)
        written_prompts = await prepared.working_target.read_all()
    except Exception as exc:
        raise PipelineStageExecutionError(f"candidate prompt write failed: {exc}") from exc
    if written_prompts != candidate.prompts:
        raise PipelineStageExecutionError("candidate prompt readback did not match the generated proposal")

    progress.enter("candidate_train")
    candidate_train = await _evaluate_split(
        prepared=prepared,
        eval_set=train_evalset,
        call_agent=call_agent,
        phase="candidate",
        split="train",
    )
    progress.enter("candidate_validation")
    candidate_validation = await _evaluate_split(
        prepared=prepared,
        eval_set=validation_evalset,
        call_agent=call_agent,
        phase="candidate",
        split="validation",
    )
    progress.enter("analysis")
    try:
        analysis = build_evaluation_analysis(
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=candidate_train,
            candidate_validation=candidate_validation,
            hard_case_ids=set(prepared.config.case_labels.hard_case_ids),
            critical_case_ids=set(prepared.config.case_labels.critical_case_ids),
            severe_case_score_drop=prepared.config.gate.severe_case_score_drop,
        )
    except EvaluationAnalysisError as exc:
        raise PipelineStageExecutionError(f"stage 3a analysis failed: {exc}") from exc
    measurements = ResourceMeasurements(
        cost_usd=ObservableValue(
            status="unavailable",
            unit="USD",
            reason="The injected agent's full pipeline cost is not observable.",
        ),
        total_tokens=ObservableValue(
            status="unavailable",
            unit="tokens",
            reason="The injected agent's full pipeline token usage is not observable.",
        ),
        duration_seconds=ObservableValue(
            status="available",
            value=perf_counter() - started_at,
            unit="seconds",
        ),
    )
    progress.enter("gate")
    try:
        gate_decision = evaluate_gate(
            analysis,
            prepared.config.gate,
            prepared.config.budget,
            measurements,
        )
    except GateEvaluationError as exc:
        raise PipelineStageExecutionError(f"stage 3b gate failed: {exc}") from exc

    progress.enter("writeback")
    writeback = await perform_writeback(
        decision=gate_decision,
        config=prepared.config.writeback,
        snapshots=prepared.input_snapshot.prompt_snapshots,
        source_target=prepared.source_target,
        candidate=candidate,
    )

    assert generated.optimize_result is not None
    return RealStageResult(
        candidate=candidate,
        optimize_result=generated.optimize_result,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        analysis=analysis,
        measurements=measurements,
        gate_decision=gate_decision,
        writeback=writeback,
    )


async def run_offline_stage(
    prepared: PreparedRun,
    *,
    scenario: CandidateScenario | None = None,
) -> OfflineStageResult:
    """Run offline SDK-Agent regression and publish its audit report."""
    progress = _MutableReportProgress(started_at=datetime.now(timezone.utc))
    result: OfflineStageResult | None = None
    try:
        result = await _execute_offline_stage(
            prepared,
            scenario=scenario,
            progress=progress,
        )
        progress.enter("reporting")
        report = build_optimization_report(
            prepared,
            result,
            progress=progress.snapshot(),
            finished_at=datetime.now(timezone.utc),
        )
        publish_report_bundle(
            report,
            run_dir=Path(prepared.workspace.run_dir),
            copy_input_files=prepared.config.artifacts.copy_input_files,
        )
        return result
    except Exception as exc:
        await _handle_stage_failure(prepared, progress, exc, result)
        raise


async def _execute_trace_stage(
    prepared: PreparedRun,
    *,
    scenario: CandidateScenario | None,
    progress: _MutableReportProgress,
) -> TraceStageResult:
    if prepared.config.execution.mode != "trace":
        raise PipelineExecutionError(
            "run_trace_stage requires execution.mode='trace', got "
            f"{prepared.config.execution.mode!r}"
        )
    trace_inputs = prepared.input_snapshot.trace_inputs
    if trace_inputs is None:
        raise PipelineExecutionError("prepared trace inputs are missing")
    selected = scenario or prepared.config.execution.candidate_scenario
    candidate_inputs = trace_inputs.scenarios[selected]
    started_at = perf_counter()

    baseline_train_set = _reload_prepared_evalset(
        Path(prepared.input_snapshot.train_evalset_path),
        label="baseline train trace",
        expected_sha256=prepared.input_snapshot.train_evalset_sha256,
    )
    baseline_validation_set = _reload_prepared_evalset(
        Path(prepared.input_snapshot.validation_evalset_path),
        label="baseline validation trace",
        expected_sha256=prepared.input_snapshot.validation_evalset_sha256,
    )
    candidate_train_set = _reload_prepared_evalset(
        Path(candidate_inputs.train_evalset_path),
        label=f"candidate {selected} train trace",
        expected_sha256=candidate_inputs.train_evalset_sha256,
    )
    candidate_validation_set = _reload_prepared_evalset(
        Path(candidate_inputs.validation_evalset_path),
        label=f"candidate {selected} validation trace",
        expected_sha256=candidate_inputs.validation_evalset_sha256,
    )

    progress.enter("baseline_train")
    baseline_train = await _evaluate_split(
        prepared=prepared, eval_set=baseline_train_set, call_agent=None,
        phase="baseline", split="train",
    )
    progress.enter("baseline_validation")
    baseline_validation = await _evaluate_split(
        prepared=prepared, eval_set=baseline_validation_set, call_agent=None,
        phase="baseline", split="validation",
    )
    progress.enter("candidate_generation")
    prompts = {
        snapshot.field_name: snapshot.content
        for snapshot in candidate_inputs.prompt_snapshots
    }
    baseline_prompts = {
        snapshot.field_name: snapshot.content
        for snapshot in prepared.input_snapshot.prompt_snapshots
    }
    canonical = json.dumps(
        prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    candidate_hash = sha256(canonical.encode("utf-8")).hexdigest()
    parent_canonical = json.dumps(
        baseline_prompts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parent_hash = sha256(parent_canonical.encode("utf-8")).hexdigest()
    candidate = TraceCandidateProposal(
        scenario=selected,
        prompts=prompts,
        changed_fields=[
            name for name in baseline_prompts if baseline_prompts[name] != prompts[name]
        ],
        rationale="Replay the selected pre-recorded candidate trace.",
        parent_prompt_sha256=parent_hash,
        candidate_prompt_sha256=candidate_hash,
        candidate_id=f"trace-{selected}-{candidate_hash[:12]}",
        source_trace_sha256={
            "train": candidate_inputs.train_evalset_sha256,
            "validation": candidate_inputs.validation_evalset_sha256,
        },
    )
    progress.enter("candidate_train")
    candidate_train = await _evaluate_split(
        prepared=prepared, eval_set=candidate_train_set, call_agent=None,
        phase="candidate", split="train",
    )
    progress.enter("candidate_validation")
    candidate_validation = await _evaluate_split(
        prepared=prepared, eval_set=candidate_validation_set, call_agent=None,
        phase="candidate", split="validation",
    )
    progress.enter("analysis")
    analysis = build_evaluation_analysis(
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        hard_case_ids=set(prepared.config.case_labels.hard_case_ids),
        critical_case_ids=set(prepared.config.case_labels.critical_case_ids),
        severe_case_score_drop=prepared.config.gate.severe_case_score_drop,
    )
    measurements = ResourceMeasurements(
        cost_usd=ObservableValue(status="unavailable", unit="USD", reason="Trace replay does not call a model."),
        total_tokens=ObservableValue(status="unavailable", unit="tokens", reason="Trace replay does not call a model."),
        duration_seconds=ObservableValue(status="available", value=perf_counter() - started_at, unit="seconds"),
    )
    progress.enter("gate")
    gate_decision = evaluate_gate(
        analysis, prepared.config.gate, prepared.config.budget, measurements
    )
    progress.enter("writeback")
    writeback = WritebackResult(
        status="skipped", reason="trace_replay", attempted=False
    )
    return TraceStageResult(
        scenario=selected, candidate=candidate,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        analysis=analysis, measurements=measurements,
        gate_decision=gate_decision, writeback=writeback,
    )


async def run_trace_stage(
    prepared: PreparedRun,
    *,
    scenario: CandidateScenario | None = None,
) -> TraceStageResult:
    """回放四个 Trace EvalSet，并发布分析与 Gate 报告。"""
    progress = _MutableReportProgress(started_at=datetime.now(timezone.utc))
    result: TraceStageResult | None = None
    try:
        result = await _execute_trace_stage(
            prepared, scenario=scenario, progress=progress
        )
        progress.enter("reporting")
        report = build_optimization_report(
            prepared, result, progress=progress.snapshot(),
            finished_at=datetime.now(timezone.utc),
        )
        publish_report_bundle(
            report,
            run_dir=Path(prepared.workspace.run_dir),
            copy_input_files=prepared.config.artifacts.copy_input_files,
        )
        return result
    except Exception as exc:
        await _handle_stage_failure(prepared, progress, exc, result)
        raise


async def run_real_stage(
    prepared: PreparedRun,
    *,
    call_agent: CallAgent,
    optimizer_parameters: OptimizerRuntimeParameters | None = None,
) -> RealStageResult:
    """Run real optimization and atomically publish its audit report."""
    progress = _MutableReportProgress(started_at=datetime.now(timezone.utc))
    result: RealStageResult | None = None
    try:
        result = await _execute_real_stage(
            prepared,
            call_agent=call_agent,
            optimizer_parameters=optimizer_parameters,
            progress=progress,
        )
        progress.enter("reporting")
        report = build_optimization_report(
            prepared,
            result,
            progress=progress.snapshot(),
            finished_at=datetime.now(timezone.utc),
        )
        publish_report_bundle(
            report,
            run_dir=Path(prepared.workspace.run_dir),
            copy_input_files=prepared.config.artifacts.copy_input_files,
        )
        return result
    except Exception as exc:
        await _handle_stage_failure(prepared, progress, exc, result)
        raise
