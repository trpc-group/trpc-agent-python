"""Sole composition root and lifecycle coordinator for the loop example."""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from trpc_agent_sdk.evaluation import TargetPrompt

from .artifacts import AuditPersistenceError, AuditSink
from .attribution import attribute_failures
from .backends import CallAgent, TraceEvaluationBackend, create_backends
from .candidate_runtime import generate_candidate, prepare_candidate_inputs
from .configuration import ValidatedRunConfig
from .contracts import CandidateGenerator, EvaluationBackend
from .costing import CostLedger
from .evaluation import compare_snapshots, dataset_contract_payload
from .evaluation_runtime import create_evaluation_runtime
from .gate import evaluate_gate
from .models import (
    AttributionPair,
    CandidateProposal,
    ComparisonPair,
    Decision,
    GateDecision,
    InnerSplit,
    OptimizationReport,
    Phase,
    RunError,
    SnapshotPair,
)
from .preflight import preflight_run
from .prompt_workspace import (
    PromptRestoreError,
    PromptRunLock,
    PromptWorkspace,
    prompt_hashes,
)
from .reporting import (
    build_optimization_report,
    create_report_context,
    persist_report,
    persist_terminal_report,
    utc_now,
)
from .schema import add_exception_note, sanitized_exception_message, sanitized_text

FaultInjector = Callable[[str], None]


def _fault(stage: str, injector: Optional[FaultInjector]) -> None:
    if injector:
        injector(stage)


async def run_pipeline(
    root_dir: str,
    *,
    config_path: Optional[str] = None,
    train_path: Optional[str] = None,
    validation_path: Optional[str] = None,
    mode: Optional[str] = None,
    run_id: Optional[str] = None,
    apply_candidate: Optional[bool] = None,
    call_agent: Optional[CallAgent] = None,
    callback_spec: Optional[str] = None,
    backend: Optional[EvaluationBackend] = None,
    candidate_generator: Optional[CandidateGenerator] = None,
    fault_injector: Optional[FaultInjector] = None,
) -> OptimizationReport:
    """Run the audited lifecycle; REJECT returns normally and ERROR is reported."""

    started_at = utc_now()
    started_clock = time.monotonic()
    validated = preflight_run(
        root_dir,
        config_path=config_path,
        train_path=train_path,
        validation_path=validation_path,
        mode=mode,
        run_id=run_id,
        apply_candidate=apply_candidate,
        call_agent=call_agent,
        callback_spec=callback_spec,
        backend=backend,
        candidate_generator=candidate_generator,
    )
    run_lock = PromptRunLock(tuple(validated.prompt_paths.values()), )
    with run_lock:
        return await _run_validated_pipeline(
            validated,
            started_at=started_at,
            started_clock=started_clock,
            backend=backend,
            candidate_generator=candidate_generator,
            fault_injector=fault_injector,
        )


async def _run_validated_pipeline(
    validated: ValidatedRunConfig,
    *,
    started_at: str,
    started_clock: float,
    backend: Optional[EvaluationBackend],
    candidate_generator: Optional[CandidateGenerator],
    fault_injector: Optional[FaultInjector],
) -> OptimizationReport:
    """Execute the lifecycle while the caller owns the prompt-set lock."""

    settings = validated.config.pipeline
    custom_backend = backend is not None
    custom_components = custom_backend or candidate_generator is not None
    if backend is None or candidate_generator is None:
        default_backend, default_generator = create_backends(
            settings.mode,
            live_adapter=validated.live_adapter,
            trace_fixture_path=validated.trace_fixture_path,
            trace_fixture_hash=validated.input_hashes.get("trace"),
            dataset_hashes={
                "train": validated.input_hashes["train"],
                "validation": validated.input_hashes["validation"],
            },
            optimizer_shutdown_timeout_seconds=settings.optimizer_shutdown_timeout_seconds,
        )
        backend = backend or default_backend
        candidate_generator = candidate_generator or default_generator
    if custom_backend and isinstance(backend, TraceEvaluationBackend):
        backend.validate_fixture(validated.train, validated.validation)

    target = TargetPrompt()
    for name, path in validated.prompt_paths.items():
        target.add_path(name, path)
    workspace = PromptWorkspace(target)
    await workspace.initialize()
    if workspace.baseline_hashes != validated.prompt_hashes:
        raise ValueError("prompt changed between preflight and workspace initialization")
    sink = AuditSink(
        validated.artifact_root,
        validated.run_id,
        publication_root=validated.root_dir,
        max_file_bytes=settings.max_audit_file_bytes,
        max_import_files=settings.max_import_files,
        max_import_file_bytes=settings.max_import_file_bytes,
        max_import_total_bytes=settings.max_import_total_bytes,
    )
    sink.create()
    ledger = CostLedger()
    evaluation_runtime = create_evaluation_runtime(
        validated=validated,
        backend=backend,
        custom_backend=custom_backend,
        sink=sink,
        ledger=ledger,
    )
    stage = "audit_config"
    applied = False
    candidate_started = False
    baseline_pair = candidate_pair = comparisons = None
    baseline_attribution_pair = candidate_attribution_pair = None
    proposal: Optional[CandidateProposal] = None
    inner: Optional[InnerSplit] = None
    gate_decision = None
    report_context = create_report_context(
        validated,
        started_at=started_at,
        started_clock=started_clock,
        baseline_prompts=workspace.baseline,
        baseline_hashes=workspace.baseline_hashes,
        callback_spec=(validated.live_adapter.import_path if validated.live_adapter else None),
        programmatic_component=custom_components,
    )

    def build_report(status: Decision, report_stage: str, errors: tuple[RunError, ...] = ()) -> OptimizationReport:
        candidate_hashes = prompt_hashes(proposal.prompts) if proposal else None
        return build_optimization_report(
            report_context,
            status=status,
            stage=report_stage,
            applied=applied,
            proposal=proposal,
            candidate_hashes=candidate_hashes,
            baseline=baseline_pair,
            candidate=candidate_pair,
            delta=comparisons,
            baseline_attribution=baseline_attribution_pair,
            candidate_attribution=candidate_attribution_pair,
            inner=inner,
            cost=ledger.summary(),
            gate_decision=gate_decision,
            artifacts=sink.records(include_report_files=False),
            errors=errors,
        )

    def enter_stage(next_stage: str) -> None:
        nonlocal stage
        stage = next_stage
        _fault(stage, fault_injector)

    def retain_progress(phase: Phase, progress: SnapshotPair) -> None:
        nonlocal baseline_pair, candidate_pair
        nonlocal baseline_attribution_pair, candidate_attribution_pair
        if phase == Phase.BASELINE:
            baseline_pair = progress
        else:
            candidate_pair = progress
        attribution = AttributionPair(
            train=(attribute_failures(
                progress.train,
                settings.attribution,
                max_text_chars=settings.max_text_chars,
            ) if progress.train is not None else None),
            validation=(attribute_failures(
                progress.validation,
                settings.attribution,
                max_text_chars=settings.max_text_chars,
            ) if progress.validation is not None else None),
        )
        if phase == Phase.BASELINE:
            baseline_attribution_pair = attribution
        else:
            candidate_attribution_pair = attribution

    try:
        enter_stage(stage)
        sink.write_json("config.json", validated.config.model_dump(mode="json", by_alias=True))
        sink.write_json("train.evalset.json", dataset_contract_payload(validated.train))
        sink.write_json("val.evalset.json", dataset_contract_payload(validated.validation))
        for name, content in workspace.baseline.items():
            sink.write_text(f"baseline_prompts/{name}.md", content)

        baseline_pair = await evaluation_runtime.evaluate_phase(
            train=validated.train,
            validation=validated.validation,
            prompts=workspace.baseline,
            phase=Phase.BASELINE,
            on_stage=enter_stage,
            on_progress=retain_progress,
        )
        if baseline_attribution_pair is None:
            raise RuntimeError("baseline attribution was not retained")
        baseline_train = baseline_pair.train
        baseline_validation = baseline_pair.validation
        baseline_train_attr = baseline_attribution_pair.train
        baseline_val_attr = baseline_attribution_pair.validation
        assert baseline_train and baseline_validation and baseline_train_attr and baseline_val_attr

        enter_stage("inner_split")
        inner, inner_train_attr, inner_train_path, inner_selection_path = prepare_candidate_inputs(
            train=validated.train,
            baseline=baseline_train,
            attribution=baseline_train_attr,
            seed=settings.seed,
            selection_ratio=settings.inner_selection_ratio,
            sink=sink,
        )

        enter_stage("candidate_generation")
        candidate_started = True
        proposal = await generate_candidate(
            generator=candidate_generator,
            workspace=workspace,
            sink=sink,
            eval_config=validated.config.evaluate,
            optimize_config=validated.config.optimize,
            train_attribution=inner_train_attr,
            inner_train_path=inner_train_path,
            inner_selection_path=inner_selection_path,
        )
        for source in proposal.cost_sources:
            ledger.record(
                f"{stage}.{source.name}",
                cost_usd=source.cost_usd,
                upper_bound=source.upper_bound,
                model_calls=source.model_calls,
                metric_calls=source.metric_calls,
                token_usage=source.token_usage,
            )
        sink.write_json("candidate.json", proposal.model_dump(mode="json", by_alias=True))
        if proposal.adapter_error:
            raise RuntimeError(f"candidate adapter rejected optimizer result: {proposal.adapter_error}")
        if proposal.status == "CANCELED":
            raise asyncio.CancelledError(proposal.error_message or "AgentOptimizer canceled")
        if proposal.status == "FAILED":
            raise RuntimeError(f"AgentOptimizer candidate generation failed: {proposal.error_message}")
        for name, content in proposal.prompts.items():
            sink.write_text(f"candidate_prompts/{name}.md", content)

        async with workspace.temporary(proposal.prompts):
            candidate_pair = await evaluation_runtime.evaluate_phase(
                train=validated.train,
                validation=validated.validation,
                prompts=proposal.prompts,
                phase=Phase.CANDIDATE,
                on_stage=enter_stage,
                on_progress=retain_progress,
            )
        if candidate_attribution_pair is None:
            raise RuntimeError("candidate attribution was not retained")
        candidate_train = candidate_pair.train
        candidate_validation = candidate_pair.validation
        candidate_train_attr = candidate_attribution_pair.train
        candidate_val_attr = candidate_attribution_pair.validation
        assert candidate_train and candidate_validation and candidate_train_attr and candidate_val_attr

        enter_stage("comparison")
        train_comparison = compare_snapshots(
            baseline_train,
            candidate_train,
            epsilon=settings.gate.epsilon,
            baseline_attribution=baseline_train_attr,
            candidate_attribution=candidate_train_attr,
        )
        comparisons = ComparisonPair(train=train_comparison)
        validation_comparison = compare_snapshots(
            baseline_validation,
            candidate_validation,
            epsilon=settings.gate.epsilon,
            critical_case_ids=settings.critical_case_ids,
            hard_case_ids=settings.hard_case_ids,
            baseline_attribution=baseline_val_attr,
            candidate_attribution=candidate_val_attr,
        )
        comparisons = ComparisonPair(train=train_comparison, validation=validation_comparison)
        sink.write_json("comparison.json", comparisons.model_dump(mode="json", by_alias=True))

        def decide(duration_seconds: float) -> GateDecision:
            return evaluate_gate(
                train=train_comparison,
                validation=validation_comparison,
                candidate=proposal,
                cost=ledger.summary(),
                duration_seconds=duration_seconds,
                baseline_prompt_hashes=workspace.baseline_hashes,
                candidate_prompt_hashes=prompt_hashes(proposal.prompts),
                config=settings.gate,
            )

        enter_stage("gate")
        gate_decision = decide(time.monotonic() - started_clock)
        if gate_decision.decision == Decision.ERROR:
            raise ValueError("gate rejected structurally invalid regression inputs")

        enter_stage("pre_apply_report")
        pre_apply_report = build_report(gate_decision.decision, stage)
        persist_report(sink, pre_apply_report)

        if gate_decision.accepted and settings.apply_candidate:
            enter_stage("apply")
            await workspace.apply(proposal.prompts)
            applied = True

        enter_stage("final_report")
        terminal_duration = time.monotonic() - started_clock
        gate_decision = decide(terminal_duration)
        if gate_decision.decision == Decision.ERROR:
            raise ValueError("terminal gate rejected structurally invalid regression inputs")
        if applied and not gate_decision.accepted:
            await workspace.restore()
            applied = False
            terminal_duration = time.monotonic() - started_clock
            gate_decision = decide(terminal_duration)
        final_report = build_report(gate_decision.decision, "complete")
        return persist_terminal_report(sink, final_report, duration_seconds=terminal_duration)
    except BaseException as error:
        if candidate_started and proposal is None:
            ledger.record(
                "candidate_generation.unreported_failure",
                cost_usd=None,
                model_calls=None,
                metric_calls=None,
            )
        fatal_restore: Optional[PromptRestoreError] = None
        try:
            await workspace.restore()
            applied = False
        except PromptRestoreError as restore_error:
            original_stage = stage
            original_message = sanitized_text(str(error), max_text_chars=settings.max_text_chars)
            add_exception_note(
                restore_error,
                f"pipeline failure before final restoration at stage {original_stage!r}: "
                f"{type(error).__name__}: {original_message}",
            )
            for note in getattr(error, "__notes__", ()):
                add_exception_note(
                    restore_error,
                    f"prior pipeline diagnostic: {sanitized_text(str(note), max_text_chars=settings.max_text_chars)}",
                )
            fatal_restore = restore_error
            stage = "restore"
            error = restore_error
        message = sanitized_exception_message(
            error,
            max_text_chars=settings.max_text_chars,
        )
        run_error = RunError(
            stage=stage,
            error_type=type(error).__name__,
            message=message,
        )
        error_report = build_report(Decision.ERROR, stage, (run_error, ))
        persistence_error: Optional[BaseException] = None
        try:
            error_report = persist_terminal_report(
                sink,
                error_report,
                duration_seconds=time.monotonic() - started_clock,
            )
        except BaseException as persist_error:
            persistence_error = persist_error
        if fatal_restore is not None:
            if persistence_error is not None:
                add_exception_note(fatal_restore, f"terminal audit persistence also failed: {persistence_error}")
            raise fatal_restore
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            if persistence_error is not None:
                add_exception_note(error, f"terminal audit persistence also failed: {persistence_error}")
            raise
        if persistence_error is not None:
            audit_error = AuditPersistenceError(
                f"terminal audit persistence failed at stage {stage!r}: {persistence_error}")
            add_exception_note(audit_error, f"original pipeline error: {type(error).__name__}: {message}")
            raise audit_error from persistence_error
        return error_report
