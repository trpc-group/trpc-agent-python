# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Auditable evaluation, optimization, and regression-gate pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from statistics import mean
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from ._agent_evaluator import AgentEvaluator
from ._agent_optimizer import AgentOptimizer
from ._eval_callbacks import Callbacks
from ._eval_case import get_all_tool_calls
from ._eval_metrics import EvalStatus
from ._eval_result import EvalCaseResult
from ._eval_result import EvalMetricResult
from ._eval_result import EvalSetAggregateResult
from ._eval_result import EvaluateResult
from ._eval_set import EvalSet
from ._evaluation_optimization_config import EvaluationOptimizationConfigFile
from ._evaluation_optimization_config import FailureCategoryName
from ._evaluation_optimization_config import OptimizationGateConfig
from ._evaluation_optimization_config import OptimizationPipelineConfig
from ._evaluation_optimization_config import load_evaluation_optimization_config
from ._evaluation_optimization_result import BaselineReport
from ._evaluation_optimization_result import CandidateReport
from ._evaluation_optimization_result import CandidateRoundReport
from ._evaluation_optimization_result import CaseDelta
from ._evaluation_optimization_result import CaseEvaluation
from ._evaluation_optimization_result import DeltaReport
from ._evaluation_optimization_result import EvaluationSnapshot
from ._evaluation_optimization_result import FailureAttributionReport
from ._evaluation_optimization_result import FailureAttributionSummary
from ._evaluation_optimization_result import GateCheck
from ._evaluation_optimization_result import GateDecision
from ._evaluation_optimization_result import InputArtifact
from ._evaluation_optimization_result import MetricEvaluation
from ._evaluation_optimization_result import OptimizationReport
from ._evaluation_optimization_result import PipelineAudit
from ._evaluation_optimization_result import SplitDelta
from ._evaluation_optimization_result import _atomic_write_text
from ._optimize_config import OptimizeConfigFile
from ._optimize_result import OptimizeResult
from ._remote_eval_service import CallAgent
from ._target_prompt import TargetPrompt

PromptOptimizerRunner = Callable[..., Awaitable[OptimizeResult]]

_EPSILON = 1e-9
_SECRET_KEYS = frozenset({
    "api_key",
    "apiKey",
    "authorization",
    "password",
    "secret",
    "token",
})
_CATEGORY_REASON = {
    "final_response_mismatch": "Final response did not satisfy the reference criterion.",
    "tool_call_error": "Tool selection or call sequence did not match the expected trajectory.",
    "tool_argument_error": "Tool arguments did not match the expected trajectory.",
    "llm_rubric_failure": "The response did not satisfy the configured LLM rubric.",
    "knowledge_recall_failure": "Retrieved knowledge was insufficient for the expected answer.",
    "format_violation": "The response did not satisfy the required output format.",
    "execution_error": "Agent inference or evaluation raised an execution error.",
    "unknown_failure": "The case failed without metric-specific diagnostic details.",
}


@dataclass(frozen=True)
class _CandidateSpec:
    round: int
    prompts: dict[str, str]
    optimizer_accepted: bool
    optimizer_acceptance_reason: str
    optimizer_cost_usd: float
    optimizer_duration_seconds: float


class EvaluationOptimizationPipeline:
    """Run baseline evaluation, prompt search, regression, gate, and audit.

    Candidate generation is delegated to :class:`AgentOptimizer` by default.
    Tests and offline examples may inject an equivalent async optimizer runner;
    every candidate still goes through the same real :class:`AgentEvaluator`
    regression and acceptance policy.
    """

    @classmethod
    async def run(
        cls,
        *,
        config_path: str,
        target_prompt: TargetPrompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
        call_agent: Optional[CallAgent] = None,
        optimizer_runner: Optional[PromptOptimizerRunner] = None,
        callbacks: Optional[Callbacks] = None,
        verbose: int = 0,
    ) -> OptimizationReport:
        """Execute the closed loop and return its persisted report.

        ``optimizer_runner`` follows the keyword interface of
        :meth:`AgentOptimizer.optimize`. It exists for deterministic fake
        model runs and third-party optimizers; omitting it uses AgentOptimizer.
        Source prompts are always restored after search and candidate
        evaluation, then written only if the final pipeline gate accepts and
        ``pipeline.update_source`` is true.
        """
        started_perf = time.perf_counter()
        started_at = _utc_now()
        cls._validate_paths(
            config_path=config_path,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
            output_dir=output_dir,
        )
        config = load_evaluation_optimization_config(config_path)
        if not target_prompt.names():
            raise ValueError("target_prompt must register at least one prompt field")
        using_default_optimizer = optimizer_runner is None
        if call_agent is None and config.pipeline.mode != "trace":
            raise ValueError("call_agent is required unless pipeline.mode is 'trace'")
        if using_default_optimizer and call_agent is None:
            raise ValueError("trace mode requires an injected optimizer_runner; AgentOptimizer needs call_agent")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        optimizer_config = OptimizeConfigFile(
            evaluate=config.evaluate,
            optimize=config.optimize,
        )
        baseline_prompts = await target_prompt.read_all()
        prompt_names = set(target_prompt.names())
        metrics_path = output_path / "evaluation_config.snapshot.json"
        _atomic_write_text(
            metrics_path,
            config.evaluate.model_dump_json(indent=2, by_alias=True) + "\n",
        )

        baseline_train = await cls._evaluate(
            split="train",
            dataset_path=train_dataset_path,
            config=config,
            call_agent=call_agent,
            callbacks=callbacks,
        )
        baseline_validation = await cls._evaluate(
            split="validation",
            dataset_path=validation_dataset_path,
            config=config,
            call_agent=call_agent,
            callbacks=callbacks,
        )

        runner = optimizer_runner or AgentOptimizer.optimize
        optimizer_output_dir = output_path / "optimizer"
        optimizer_config_path = cls._temporary_optimizer_config(optimizer_config)
        try:
            optimize_result = await runner(
                config_path=str(optimizer_config_path),
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=train_dataset_path,
                validation_dataset_path=validation_dataset_path,
                output_dir=str(optimizer_output_dir),
                callbacks=callbacks,
                update_source=False,
                verbose=verbose,
            )
        finally:
            await target_prompt.write_all(baseline_prompts)
            optimizer_config_path.unlink(missing_ok=True)
            cls._redact_optimizer_config_snapshot(
                optimizer_output_dir=optimizer_output_dir,
                optimizer_config=optimizer_config,
            )
        if not isinstance(optimize_result, OptimizeResult):
            raise TypeError("optimizer_runner must return OptimizeResult")

        specs = cls._candidate_specs(
            optimize_result=optimize_result,
            baseline_prompts=baseline_prompts,
            prompt_names=prompt_names,
            max_candidates=config.pipeline.max_candidates,
        )
        evaluated: list[tuple[_CandidateSpec, EvaluationSnapshot, EvaluationSnapshot, float]] = []
        try:
            for spec in specs:
                candidate_started = time.perf_counter()
                await target_prompt.write_all(spec.prompts)
                train = await cls._evaluate(
                    split="train",
                    dataset_path=train_dataset_path,
                    config=config,
                    call_agent=call_agent,
                    callbacks=callbacks,
                )
                validation = await cls._evaluate(
                    split="validation",
                    dataset_path=validation_dataset_path,
                    config=config,
                    call_agent=call_agent,
                    callbacks=callbacks,
                )
                evaluated.append((
                    spec,
                    train,
                    validation,
                    _rounded(time.perf_counter() - candidate_started),
                ))
        finally:
            await target_prompt.write_all(baseline_prompts)

        evaluation_case_runs = (
            baseline_train.case_run_count
            + baseline_validation.case_run_count
            + sum(train.case_run_count + validation.case_run_count for _, train, validation, _ in evaluated)
        )
        estimated_evaluation_cost = _rounded(
            evaluation_case_runs * config.pipeline.evaluation_case_cost_usd)
        total_cost = _rounded(optimize_result.total_llm_cost + estimated_evaluation_cost)

        rounds: list[CandidateRoundReport] = []
        for spec, train, validation, evaluation_duration in evaluated:
            train_delta = _compare_snapshots(baseline_train, train)
            validation_delta = _compare_snapshots(baseline_validation, validation)
            decision = _apply_gate(
                gate=config.pipeline.gate,
                train_delta=train_delta,
                validation_delta=validation_delta,
                baseline_validation=baseline_validation,
                candidate_validation=validation,
                optimizer_status=optimize_result.status,
                total_cost_usd=total_cost,
            )
            rounds.append(
                CandidateRoundReport(
                    round=spec.round,
                    prompts=spec.prompts,
                    optimizer_accepted=spec.optimizer_accepted,
                    optimizer_acceptance_reason=spec.optimizer_acceptance_reason,
                    optimizer_cost_usd=_rounded(spec.optimizer_cost_usd),
                    optimizer_duration_seconds=_rounded(spec.optimizer_duration_seconds),
                    evaluation_duration_seconds=evaluation_duration,
                    train=train,
                    validation=validation,
                    train_delta=train_delta,
                    validation_delta=validation_delta,
                    gate_decision=decision,
                ))

        selected = cls._select_candidate(rounds)
        finished_at = _utc_now()
        duration = _rounded(time.perf_counter() - started_perf)
        inputs = cls._input_audit(
            config_path=config_path,
            train_dataset_path=train_dataset_path,
            validation_dataset_path=validation_dataset_path,
        )
        prompt_inputs = cls._prompt_input_audit(
            target_prompt=target_prompt,
            baseline_prompts=baseline_prompts,
            config_path=config_path,
        )
        report = OptimizationReport(
            baseline=BaselineReport(
                train=baseline_train,
                validation=baseline_validation,
            ),
            candidate=CandidateReport(
                round=selected.round,
                prompts=selected.prompts,
                train=selected.train,
                validation=selected.validation,
            ),
            delta=DeltaReport(
                train=selected.train_delta,
                validation=selected.validation_delta,
            ),
            gate_decision=selected.gate_decision,
            failure_attribution=FailureAttributionReport(
                baseline_train=_summarize_attribution(baseline_train),
                baseline_validation=_summarize_attribution(baseline_validation),
                candidate_train=_summarize_attribution(selected.train),
                candidate_validation=_summarize_attribution(selected.validation),
            ),
            rounds=rounds,
            audit=PipelineAudit(
                mode=config.pipeline.mode,
                report_language=config.pipeline.report_language,
                random_seed=config.optimize.algorithm.seed,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                optimizer_algorithm=optimize_result.algorithm,
                optimizer_status=optimize_result.status,
                optimizer_cost_usd=_rounded(optimize_result.total_llm_cost),
                estimated_evaluation_cost_usd=estimated_evaluation_cost,
                total_cost_usd=total_cost,
                evaluation_cost_is_estimated=True,
                evaluation_case_runs=evaluation_case_runs,
                source_updated=False,
                inputs=inputs,
                prompt_inputs=prompt_inputs,
                config_snapshot=_redact(
                    config.model_dump(mode="json", by_alias=True)),
            ),
        )

        cls._persist_artifacts(
            output_path=output_path,
            report=report,
            optimize_result=optimize_result,
            baseline_prompts=baseline_prompts,
        )
        if report.gate_decision.accepted and config.pipeline.update_source:
            try:
                await target_prompt.write_all(report.candidate.prompts)
                report.audit.source_updated = True
                report.write(str(output_path))
            except BaseException:
                await target_prompt.write_all(baseline_prompts)
                report.audit.source_updated = False
                report.write(str(output_path))
                raise
        return report

    @staticmethod
    async def _evaluate(
        *,
        split: str,
        dataset_path: str,
        config: EvaluationOptimizationConfigFile,
        call_agent: Optional[CallAgent],
        callbacks: Optional[Callbacks],
    ) -> EvaluationSnapshot:
        with open(dataset_path, "r", encoding="utf-8") as file:
            eval_set = EvalSet.model_validate_json(file.read())
        _, _, _, eval_results_by_eval_id = await AgentEvaluator.evaluate_eval_set(
            eval_set,
            call_agent=call_agent,
            eval_config=config.evaluate,
            callbacks=callbacks,
            num_runs=config.evaluate.num_runs,
            print_detailed_results=False,
            case_parallelism=config.optimize.eval_case_parallelism,
        )
        result = EvaluateResult(
            results_by_eval_set_id={
                eval_set.eval_set_id: EvalSetAggregateResult(
                    eval_results_by_eval_id=eval_results_by_eval_id,
                    num_runs=config.evaluate.num_runs,
                ),
            }
        )
        return _build_snapshot(
            split=split,
            result=result,
            pipeline_config=config.pipeline,
        )

    @staticmethod
    def _candidate_specs(
        *,
        optimize_result: OptimizeResult,
        baseline_prompts: dict[str, str],
        prompt_names: set[str],
        max_candidates: int,
    ) -> list[_CandidateSpec]:
        specs: list[_CandidateSpec] = []
        seen: set[str] = set()

        def append(
            round_number: int,
            prompts: dict[str, str],
            optimizer_accepted: bool,
            reason: str,
            cost: float,
            duration: float,
        ) -> None:
            if set(prompts) != prompt_names:
                raise ValueError(
                    f"optimizer candidate prompt keys mismatch: expected "
                    f"{sorted(prompt_names)}, got {sorted(prompts)}")
            fingerprint = json.dumps(prompts, ensure_ascii=False, sort_keys=True)
            if fingerprint in seen:
                return
            seen.add(fingerprint)
            specs.append(
                _CandidateSpec(
                    round=round_number,
                    prompts=dict(prompts),
                    optimizer_accepted=optimizer_accepted,
                    optimizer_acceptance_reason=reason,
                    optimizer_cost_usd=cost,
                    optimizer_duration_seconds=duration,
                ))

        for record in optimize_result.rounds:
            append(
                record.round,
                record.candidate_prompts,
                record.accepted,
                record.acceptance_reason,
                record.round_llm_cost,
                record.duration_seconds,
            )

        best_prompts = optimize_result.best_prompts or baseline_prompts
        best_round = max((spec.round for spec in specs), default=0) + 1
        append(
            best_round,
            best_prompts,
            True,
            "optimizer best_prompts",
            0.0,
            0.0,
        )
        if not specs:
            append(
                0,
                baseline_prompts,
                False,
                "optimizer returned no candidate; baseline used for gate evidence",
                0.0,
                0.0,
            )
        if len(specs) <= max_candidates:
            return specs
        best_fingerprint = json.dumps(best_prompts, ensure_ascii=False, sort_keys=True)
        selected = specs[:max_candidates]
        if all(
                json.dumps(spec.prompts, ensure_ascii=False, sort_keys=True) != best_fingerprint
                for spec in selected):
            best_spec = next(
                spec for spec in specs
                if json.dumps(spec.prompts, ensure_ascii=False, sort_keys=True) == best_fingerprint)
            selected[-1] = best_spec
        return selected

    @staticmethod
    def _select_candidate(rounds: list[CandidateRoundReport]) -> CandidateRoundReport:
        if not rounds:
            raise RuntimeError("optimizer candidate collection produced no rounds")
        accepted = [record for record in rounds if record.gate_decision.accepted]
        pool = accepted or rounds
        return max(
            pool,
            key=lambda record: (
                record.validation.score,
                record.validation.pass_rate,
                record.train.score,
                -record.round,
            ),
        )

    @staticmethod
    def _persist_artifacts(
        *,
        output_path: Path,
        report: OptimizationReport,
        optimize_result: OptimizeResult,
        baseline_prompts: dict[str, str],
    ) -> None:
        baseline_dir = output_path / "baseline_prompts"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        for name, content in baseline_prompts.items():
            _atomic_write_text(baseline_dir / f"{_safe_filename(name)}.md", content)

        candidates_dir = output_path / "candidates"
        rounds_dir = output_path / "rounds"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        rounds_dir.mkdir(parents=True, exist_ok=True)
        for record in report.rounds:
            candidate_dir = candidates_dir / f"round_{record.round:03d}"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            for name, content in record.prompts.items():
                _atomic_write_text(candidate_dir / f"{_safe_filename(name)}.md", content)
            payload = json.dumps(
                record.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            _atomic_write_text(rounds_dir / f"round_{record.round:03d}.json", payload + "\n")

        optimizer_payload = optimize_result.model_dump_json(indent=2, by_alias=True)
        _atomic_write_text(output_path / "optimizer_result.json", optimizer_payload + "\n")
        config_payload = json.dumps(
            report.audit.config_snapshot,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(output_path / "config.snapshot.json", config_payload + "\n")
        report.write(str(output_path))

    @staticmethod
    def _temporary_optimizer_config(
        config: OptimizeConfigFile,
    ) -> Path:
        """Write a standard optimizer config to a short-lived UTF-8 file."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="trpc-eval-optimize-",
            delete=False,
        ) as file:
            file.write(config.model_dump_json(indent=2, by_alias=True))
            file.write("\n")
            return Path(file.name)

    @staticmethod
    def _redact_optimizer_config_snapshot(
        *,
        optimizer_output_dir: Path,
        optimizer_config: OptimizeConfigFile,
    ) -> None:
        """Replace an optimizer-owned config copy with a redacted snapshot."""
        snapshot = optimizer_output_dir / "config.snapshot.json"
        if not snapshot.is_file():
            return
        payload = json.dumps(
            _redact(optimizer_config.model_dump(mode="json", by_alias=True)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(snapshot, payload + "\n")

    @staticmethod
    def _validate_paths(
        *,
        config_path: str,
        train_dataset_path: str,
        validation_dataset_path: str,
        output_dir: str,
    ) -> None:
        for label, path in (
            ("config_path", config_path),
            ("train_dataset_path", train_dataset_path),
            ("validation_dataset_path", validation_dataset_path),
        ):
            if not path or not Path(path).is_file():
                raise FileNotFoundError(f"{label} is not a file: {path}")
        train = os.path.normcase(os.path.abspath(train_dataset_path))
        validation = os.path.normcase(os.path.abspath(validation_dataset_path))
        if train == validation:
            raise ValueError("train and validation datasets must be different files")
        if not output_dir:
            raise ValueError("output_dir must be a non-empty path")

    @staticmethod
    def _input_audit(
        *,
        config_path: str,
        train_dataset_path: str,
        validation_dataset_path: str,
    ) -> dict[str, InputArtifact]:
        base = Path(config_path).resolve().parent
        return {
            "config": _file_artifact(config_path, base),
            "train": _file_artifact(train_dataset_path, base),
            "validation": _file_artifact(validation_dataset_path, base),
        }

    @staticmethod
    def _prompt_input_audit(
        *,
        target_prompt: TargetPrompt,
        baseline_prompts: dict[str, str],
        config_path: str,
    ) -> dict[str, InputArtifact]:
        base = Path(config_path).resolve().parent
        artifacts: dict[str, InputArtifact] = {}
        for name, content in baseline_prompts.items():
            source = target_prompt.describe_source(name)
            path = source
            if source != "<callback>":
                path = _portable_path(Path(source), base)
            artifacts[name] = InputArtifact(
                path=path,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        return artifacts


def _build_snapshot(
    *,
    split: str,
    result: EvaluateResult,
    pipeline_config: OptimizationPipelineConfig,
) -> EvaluationSnapshot:
    cases: list[CaseEvaluation] = []
    for eval_set_id, set_result in sorted(result.results_by_eval_set_id.items()):
        for case_id, runs in sorted(set_result.eval_results_by_eval_id.items()):
            cases.append(
                _build_case_evaluation(
                    eval_set_id=eval_set_id,
                    case_id=case_id,
                    runs=runs,
                    pipeline_config=pipeline_config,
                ))

    metric_scores: dict[str, list[float]] = {}
    for case in cases:
        for metric in case.metrics:
            if metric.score is not None:
                metric_scores.setdefault(metric.metric_name, []).append(metric.score)
    metric_breakdown = {
        name: _rounded(mean(scores))
        for name, scores in sorted(metric_scores.items())
        if scores
    }
    case_count = len(cases)
    return EvaluationSnapshot(
        split=split,
        score=_rounded(mean(case.score for case in cases)) if cases else 0.0,
        pass_rate=_rounded(sum(case.passed for case in cases) / case_count) if case_count else 0.0,
        case_count=case_count,
        case_run_count=sum(
            len(runs)
            for set_result in result.results_by_eval_set_id.values()
            for runs in set_result.eval_results_by_eval_id.values()),
        metric_breakdown=metric_breakdown,
        cases=cases,
    )


def _build_case_evaluation(
    *,
    eval_set_id: str,
    case_id: str,
    runs: list[EvalCaseResult],
    pipeline_config: OptimizationPipelineConfig,
) -> CaseEvaluation:
    values_by_metric: dict[str, list[float]] = {}
    thresholds: dict[str, float] = {}
    statuses_by_metric: dict[str, list[bool]] = {}
    reasons_by_metric: dict[str, list[str]] = {}
    categories: set[FailureCategoryName] = set()
    reasons: list[str] = []

    for run in runs:
        if run.error_message:
            categories.add("execution_error")
            reasons.append(run.error_message)
        for metric in run.overall_eval_metric_results:
            thresholds.setdefault(metric.metric_name, metric.threshold)
            statuses_by_metric.setdefault(metric.metric_name, []).append(
                metric.eval_status == EvalStatus.PASSED)
            if metric.score is not None:
                values_by_metric.setdefault(metric.metric_name, []).append(metric.score)
            if metric.details and metric.details.reason:
                reasons_by_metric.setdefault(metric.metric_name, []).append(metric.details.reason)
            if metric.eval_status != EvalStatus.PASSED:
                category = _classify_failure(metric, run)
                categories.add(category)
                if metric.details and metric.details.reason:
                    reasons.append(metric.details.reason)
                else:
                    reasons.append(_CATEGORY_REASON[category])

    passed = bool(runs) and all(run.final_eval_status == EvalStatus.PASSED for run in runs)
    if not passed and case_id in pipeline_config.failure_category_overrides:
        categories = {pipeline_config.failure_category_overrides[case_id]}
    if not passed and not categories:
        categories.add("unknown_failure")
        reasons.append(_CATEGORY_REASON["unknown_failure"])

    metrics: list[MetricEvaluation] = []
    all_metric_scores: list[float] = []
    metric_names = sorted(set(thresholds) | set(values_by_metric) | set(statuses_by_metric))
    for name in metric_names:
        values = values_by_metric.get(name, [])
        score = _rounded(mean(values)) if values else None
        if score is not None:
            all_metric_scores.append(score)
        metrics.append(
            MetricEvaluation(
                metric_name=name,
                score=score,
                threshold=thresholds.get(name, 0.0),
                passed=bool(statuses_by_metric.get(name)) and all(statuses_by_metric[name]),
                reasons=_dedupe(reasons_by_metric.get(name, [])),
            ))
    score = _rounded(mean(all_metric_scores)) if all_metric_scores else float(passed)
    hard_fail = (not passed and (
        case_id in pipeline_config.hard_fail_case_ids
        or bool(categories.intersection(pipeline_config.hard_fail_categories))))
    key_trace = _trace_from_run(runs[-1]) if runs else []
    return CaseEvaluation(
        eval_set_id=eval_set_id,
        case_id=case_id,
        score=score,
        passed=passed,
        hard_fail=hard_fail,
        metrics=metrics,
        failure_categories=sorted(categories),
        failure_reasons=_dedupe(reasons),
        key_trace=key_trace,
    )


def _classify_failure(
    metric: EvalMetricResult,
    run: EvalCaseResult,
) -> FailureCategoryName:
    metric_name = metric.metric_name.lower()
    reason = metric.details.reason.lower() if metric.details and metric.details.reason else ""

    if "knowledge" in metric_name:
        return "knowledge_recall_failure"
    if any(token in reason for token in ("argument", "parameter", "参数", "入参")):
        return "tool_argument_error"
    if any(token in reason for token in ("knowledge", "retriev", "ground", "recall", "知识", "召回")):
        return "knowledge_recall_failure"
    if any(token in reason for token in ("format", "schema", "json", "格式")):
        return "format_violation"
    if "tool" in metric_name or "trajectory" in metric_name:
        return _classify_tool_failure(run)
    if any(token in reason for token in ("tool", "function", "工具", "函数")):
        return "tool_call_error"
    if "llm" in metric_name or "rubric" in metric_name:
        return "llm_rubric_failure"
    if "final_response" in metric_name or "response_match" in metric_name:
        if _has_structured_format_failure(run):
            return "format_violation"
        return "final_response_mismatch"
    if reason:
        return "final_response_mismatch"
    return "unknown_failure"


def _classify_tool_failure(run: EvalCaseResult) -> FailureCategoryName:
    for item in run.eval_metric_result_per_invocation:
        actual = get_all_tool_calls(item.actual_invocation.intermediate_data)
        expected = (
            get_all_tool_calls(item.expected_invocation.intermediate_data)
            if item.expected_invocation is not None
            else [])
        actual_names = [call.name for call in actual]
        expected_names = [call.name for call in expected]
        if actual_names != expected_names:
            return "tool_call_error"
        if any(actual_call.args != expected_call.args
               for actual_call, expected_call in zip(actual, expected)):
            return "tool_argument_error"
    return "tool_call_error"


def _has_structured_format_failure(run: EvalCaseResult) -> bool:
    for item in run.eval_metric_result_per_invocation:
        actual = _content_text(item.actual_invocation.final_response)
        expected = (
            _content_text(item.expected_invocation.final_response)
            if item.expected_invocation is not None
            else "")
        expected = expected.strip()
        if not expected.startswith(("{", "[")):
            continue
        try:
            json.loads(expected)
        except json.JSONDecodeError:
            continue
        try:
            json.loads(actual)
        except (json.JSONDecodeError, TypeError):
            return True
    return False


def _trace_from_run(run: EvalCaseResult) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for index, item in enumerate(run.eval_metric_result_per_invocation):
        actual_calls = get_all_tool_calls(item.actual_invocation.intermediate_data)
        expected_calls = (
            get_all_tool_calls(item.expected_invocation.intermediate_data)
            if item.expected_invocation is not None
            else [])
        trace.append({
            "invocationIndex":
                index,
            "actualFinalResponse":
                _content_text(item.actual_invocation.final_response),
            "expectedFinalResponse": (
                _content_text(item.expected_invocation.final_response)
                if item.expected_invocation is not None
                else None),
            "actualToolCalls": [_function_call_payload(call) for call in actual_calls],
            "expectedToolCalls": [_function_call_payload(call) for call in expected_calls],
        })
    return trace


def _function_call_payload(call: Any) -> dict[str, Any]:
    return {
        "name": getattr(call, "name", ""),
        "args": getattr(call, "args", {}),
    }


def _content_text(content: Any) -> str:
    if content is None or not getattr(content, "parts", None):
        return ""
    return "".join(part.text or "" for part in content.parts if getattr(part, "text", None))


def _compare_snapshots(
    baseline: EvaluationSnapshot,
    candidate: EvaluationSnapshot,
) -> SplitDelta:
    baseline_by_key = {(case.eval_set_id, case.case_id): case for case in baseline.cases}
    candidate_by_key = {(case.eval_set_id, case.case_id): case for case in candidate.cases}
    cases: list[CaseDelta] = []
    groups: dict[str, list[str]] = {
        "newly_passed": [],
        "newly_failed": [],
        "improved": [],
        "regressed": [],
        "unchanged": [],
    }
    for key in sorted(set(baseline_by_key) | set(candidate_by_key)):
        before = baseline_by_key.get(key)
        after = candidate_by_key.get(key)
        if before is None:
            status = "added"
            delta = None
        elif after is None:
            status = "removed"
            delta = None
        else:
            delta = _rounded(after.score - before.score)
            if not before.passed and after.passed:
                status = "newly_passed"
            elif before.passed and not after.passed:
                status = "newly_failed"
            elif delta > _EPSILON:
                status = "improved"
            elif delta < -_EPSILON:
                status = "regressed"
            else:
                status = "unchanged"
        if status in groups:
            groups[status].append(key[1])
        cases.append(
            CaseDelta(
                eval_set_id=key[0],
                case_id=key[1],
                baseline_score=before.score if before else None,
                candidate_score=after.score if after else None,
                score_delta=delta,
                baseline_passed=before.passed if before else None,
                candidate_passed=after.passed if after else None,
                baseline_hard_fail=before.hard_fail if before else None,
                candidate_hard_fail=after.hard_fail if after else None,
                status=status,
            ))
    return SplitDelta(
        score_delta=_rounded(candidate.score - baseline.score),
        pass_rate_delta=_rounded(candidate.pass_rate - baseline.pass_rate),
        cases=cases,
        **groups,
    )


def _apply_gate(
    *,
    gate: OptimizationGateConfig,
    train_delta: SplitDelta,
    validation_delta: SplitDelta,
    baseline_validation: EvaluationSnapshot,
    candidate_validation: EvaluationSnapshot,
    optimizer_status: str,
    total_cost_usd: float,
) -> GateDecision:
    baseline_by_id = {case.case_id: case for case in baseline_validation.cases}
    candidate_by_id = {case.case_id: case for case in candidate_validation.cases}
    new_hard_fail_ids = sorted(
        case_id for case_id, candidate_case in candidate_by_id.items()
        if candidate_case.hard_fail
        and not (baseline_by_id.get(case_id) and baseline_by_id[case_id].hard_fail))
    critical_regressions: list[str] = []
    for case_id in gate.critical_case_ids:
        before = baseline_by_id.get(case_id)
        after = candidate_by_id.get(case_id)
        if before is None or after is None:
            critical_regressions.append(case_id)
            continue
        if before.passed and not after.passed:
            critical_regressions.append(case_id)
            continue
        if before.score - after.score > gate.max_critical_score_drop + _EPSILON:
            critical_regressions.append(case_id)
    critical_regressions = sorted(set(critical_regressions))
    validation_regressions = sorted(
        set(validation_delta.newly_failed + validation_delta.regressed))
    overfitting = (
        train_delta.score_delta > _EPSILON
        and (
            validation_delta.score_delta + _EPSILON < gate.min_validation_score_delta
            or bool(validation_regressions)))

    checks = [
        GateCheck(
            name="optimizer_status",
            configured=True,
            passed=optimizer_status == "SUCCEEDED",
            actual=optimizer_status,
            expected="SUCCEEDED",
            detail="Optimizer must finish successfully before a candidate can be accepted.",
        ),
        GateCheck(
            name="validation_score_delta",
            configured=True,
            passed=validation_delta.score_delta + _EPSILON >= gate.min_validation_score_delta,
            actual=validation_delta.score_delta,
            expected=f">= {gate.min_validation_score_delta}",
            detail="Validation mean score must improve by the configured threshold.",
        ),
        GateCheck(
            name="validation_pass_rate_delta",
            configured=True,
            passed=validation_delta.pass_rate_delta + _EPSILON >= gate.min_validation_pass_rate_delta,
            actual=validation_delta.pass_rate_delta,
            expected=f">= {gate.min_validation_pass_rate_delta}",
            detail="Validation pass rate must not fall below the configured delta.",
        ),
        GateCheck(
            name="new_hard_fail",
            configured=gate.reject_new_hard_fail,
            passed=not gate.reject_new_hard_fail or not new_hard_fail_ids,
            actual=new_hard_fail_ids,
            expected=[],
            detail="No new configured hard failure may be introduced.",
        ),
        GateCheck(
            name="critical_case_regression",
            configured=bool(gate.critical_case_ids),
            passed=not critical_regressions,
            actual=critical_regressions,
            expected=[],
            detail="Critical cases must stay within the configured score-drop tolerance.",
        ),
        GateCheck(
            name="validation_regression_count",
            configured=gate.max_validation_regressions is not None,
            passed=(
                gate.max_validation_regressions is None
                or len(validation_regressions) <= gate.max_validation_regressions),
            actual=len(validation_regressions),
            expected=(
                "disabled"
                if gate.max_validation_regressions is None
                else f"<= {gate.max_validation_regressions}"),
            detail="Candidate validation regressions are counted per case.",
        ),
        GateCheck(
            name="overfitting_guard",
            configured=gate.reject_overfitting,
            passed=not gate.reject_overfitting or not overfitting,
            actual=overfitting,
            expected=False,
            detail=("Train-only gains are rejected when validation misses the score "
                    "threshold or any validation case regresses."),
        ),
        GateCheck(
            name="total_cost_budget",
            configured=gate.max_total_cost_usd is not None,
            passed=(
                gate.max_total_cost_usd is None
                or total_cost_usd <= gate.max_total_cost_usd + _EPSILON),
            actual=total_cost_usd,
            expected=(
                "disabled"
                if gate.max_total_cost_usd is None
                else f"<= {gate.max_total_cost_usd}"),
            detail="Cost includes optimizer cost and configured per-case evaluation estimates.",
        ),
    ]
    failed = [check for check in checks if check.configured and not check.passed]
    accepted = not failed
    reasons = (
        ["All configured acceptance checks passed."]
        if accepted
        else [f"{check.name}: {check.detail}" for check in failed])
    return GateDecision(
        accepted=accepted,
        reasons=reasons,
        checks=checks,
        new_hard_fail_case_ids=new_hard_fail_ids,
        critical_regression_case_ids=critical_regressions,
        validation_regression_case_ids=validation_regressions,
        overfitting_detected=overfitting,
    )


def _summarize_attribution(snapshot: EvaluationSnapshot) -> FailureAttributionSummary:
    case_ids: dict[str, list[str]] = {}
    failed = [case for case in snapshot.cases if not case.passed]
    for case in failed:
        categories = case.failure_categories or ["unknown_failure"]
        for category in categories:
            case_ids.setdefault(category, []).append(case.case_id)
    normalized = {
        category: sorted(set(ids))
        for category, ids in sorted(case_ids.items())
    }
    return FailureAttributionSummary(
        total_failed_cases=len(failed),
        counts={category: len(ids) for category, ids in normalized.items()},
        case_ids=normalized,
    )


def _file_artifact(path: str, base: Path) -> InputArtifact:
    resolved = Path(path).resolve()
    return InputArtifact(
        path=_portable_path(resolved, base),
        sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


def _portable_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        try:
            return Path(os.path.relpath(path.resolve(), base)).as_posix()
        except ValueError:
            return str(path.resolve())


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***REDACTED***" if key in _SECRET_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return sanitized or "prompt"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
