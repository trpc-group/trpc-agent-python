"""AgentEvaluator adapter and leakage-safe input validation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Awaitable
from typing import Callable

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import OptimizeConfigFile
from trpc_agent_sdk.evaluation import get_all_tool_calls
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult
from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

from .models import CaseSnapshot
from .models import EvaluationSnapshot
from .models import GateConfig
from .models import InputBundle
from .models import InputPaths
from .models import InvocationSnapshot
from .models import SplitName

CallAgent = Callable[[str], Awaitable[str]]
HASH_NAME = "sha256"


@dataclass(frozen=True)
class EvaluationRequest:
    """Inputs needed for one evaluator invocation."""

    dataset_path: Path
    optimizer_path: Path
    split: SplitName
    call_agent: CallAgent | None
    fake_judge: bool = False


def load_gate_config(path: Path) -> GateConfig:
    """Load and validate gate.json."""
    return GateConfig.model_validate_json(path.read_text(encoding="utf-8"))


def load_eval_set(path: Path) -> EvalSet:
    """Load one evalset using the SDK schema."""
    return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))


def validate_inputs(paths: InputPaths) -> tuple[InputBundle, OptimizeConfigFile, GateConfig]:
    """Validate paths, schemas, metric references, and split leakage."""
    input_paths = tuple(paths.model_dump().values())
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    if paths.train_path.resolve() == paths.validation_path.resolve():
        raise ValueError("train and validation paths must differ")

    train_set = load_eval_set(paths.train_path)
    validation_set = load_eval_set(paths.validation_path)
    optimizer = load_optimize_config(str(paths.optimizer_path))
    gate = load_gate_config(paths.gate_path)
    _validate_split_leakage(train_set, validation_set)
    _validate_gate_references(gate, optimizer, validation_set)
    hashes = {path.name: _file_hash(path) for path in input_paths}
    bundle = InputBundle(
        prompt_path=paths.prompt_path.resolve(),
        train_path=paths.train_path.resolve(),
        validation_path=paths.validation_path.resolve(),
        optimizer_path=paths.optimizer_path.resolve(),
        gate_path=paths.gate_path.resolve(),
        hashes=hashes,
    )
    return bundle, optimizer, gate


async def evaluate_split(request: EvaluationRequest) -> EvaluationSnapshot:
    """Run AgentEvaluator and retain results even when cases fail."""
    config = load_optimize_config(str(request.optimizer_path))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="eval-optimize-", dir=Path.cwd()) as temp_dir:
        dataset_path = _dataset_for_sdk(request.dataset_path, Path(temp_dir))
        metrics_path = Path(temp_dir) / "eval_config.json"
        metrics_path.write_text(
            config.evaluate.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        executor = AgentEvaluator.get_executer(
            dataset_path,
            call_agent=request.call_agent,
            num_runs=config.evaluate.num_runs,
            print_detailed_results=False,
            print_summary_report=False,
            eval_metrics_file_path_or_dir=str(metrics_path),
            case_parallelism=config.optimize.eval_case_parallelism,
        )
        try:
            await executor.evaluate()
        except _EvaluationCasesFailed:
            # SDK reserves this subclass for partial case failures; unrelated
            # AssertionError instances must propagate to the pipeline failure.
            if executor.get_result() is None:
                raise
        result = executor.get_result()
    if result is None:
        raise RuntimeError("AgentEvaluator completed without a result")
    return _snapshot_result(
        result,
        request.split,
        config,
        time.monotonic() - started,
        request.fake_judge,
    )


def _snapshot_result(
    result: EvaluateResult,
    split: SplitName,
    config: OptimizeConfigFile,
    duration: float,
    fake_judge: bool,
) -> EvaluationSnapshot:
    metrics_config = config.evaluate.get_eval_metrics()
    primary_metric = metrics_config[0]
    primary = primary_metric.metric_name
    grouped: dict[str, list[EvalCaseResult]] = {}
    for set_result in result.results_by_eval_set_id.values():
        for case_id, runs in set_result.eval_results_by_eval_id.items():
            grouped.setdefault(case_id, []).extend(runs)
    cases = [
        _snapshot_case(
            case_id,
            split,
            runs,
            primary,
            fake_judge,
            primary_metric.threshold,
        ) for case_id, runs in sorted(grouped.items())
    ]
    metric_names = [metric.metric_name for metric in metrics_config]
    metrics = {name: _mean_optional(case.metric_scores.get(name) for case in cases) for name in metric_names}
    primary_score = None if any(case.hard_failure for case in cases) else metrics.get(primary)
    passed = sum(case.passed for case in cases)
    return EvaluationSnapshot(
        split=split,
        primary_metric=primary,
        primary_score=primary_score,
        pass_rate=passed / len(cases) if cases else 0.0,
        metric_scores=metrics,
        cases=cases,
        duration_seconds=max(duration, 0.0),
    )


def _snapshot_case(
    case_id: str,
    split: SplitName,
    runs: list[EvalCaseResult],
    primary: str,
    fake_judge: bool = False,
    primary_threshold: float = 1.0,
) -> CaseSnapshot:
    metric_names = sorted({metric.metric_name for run in runs for metric in run.overall_eval_metric_results})
    scores = {
        name:
        _mean_optional(metric.score for run in runs for metric in run.overall_eval_metric_results
                       if metric.metric_name == name)
        for name in metric_names
    }
    if fake_judge:
        scores[primary] = _fake_judge_score(runs)
    statuses = {name: _aggregate_metric_status(runs, name) for name in metric_names}
    reasons = _metric_reasons(runs)
    errors = [run.error_message for run in runs if run.error_message]
    passed = bool(runs) and all(run.final_eval_status == EvalStatus.PASSED for run in runs)
    metric_not_evaluated = any(metric.eval_status == EvalStatus.NOT_EVALUATED for run in runs
                               for metric in run.overall_eval_metric_results)
    hard_failure = not runs or scores.get(primary) is None or metric_not_evaluated or any(
        run.final_eval_status == EvalStatus.NOT_EVALUATED or run.error_message for run in runs)
    if fake_judge and scores[primary] is not None:
        statuses[primary] = (EvalStatus.PASSED.name if scores[primary] >= primary_threshold else EvalStatus.FAILED.name)
        passed = scores[primary] >= primary_threshold
    passed = passed and not hard_failure
    actual, expected = _invocation_snapshots(runs)
    return CaseSnapshot(
        case_id=case_id,
        split=split,
        passed=passed,
        hard_failure=hard_failure,
        metric_scores=scores,
        metric_statuses=statuses,
        reasons=reasons,
        error_message="; ".join(errors),
        actual=actual,
        expected=expected,
    )


def _aggregate_metric_status(runs: list[EvalCaseResult], metric_name: str) -> str:
    statuses = [
        metric.eval_status for run in runs for metric in run.overall_eval_metric_results
        if metric.metric_name == metric_name
    ]
    if not statuses or EvalStatus.NOT_EVALUATED in statuses:
        return EvalStatus.NOT_EVALUATED.name
    if all(status == EvalStatus.PASSED for status in statuses):
        return EvalStatus.PASSED.name
    return EvalStatus.FAILED.name


def _metric_reasons(runs: list[EvalCaseResult]) -> dict[str, str]:
    reasons: dict[str, list[str]] = {}
    for run in runs:
        for metric in run.overall_eval_metric_results:
            reason = metric.details.reason if metric.details else None
            if reason:
                reasons.setdefault(metric.metric_name, []).append(reason)
    return {name: "; ".join(dict.fromkeys(values)) for name, values in reasons.items()}


def _invocation_snapshots(runs: list[EvalCaseResult], ) -> tuple[list[InvocationSnapshot], list[InvocationSnapshot]]:
    actual: list[InvocationSnapshot] = []
    expected: list[InvocationSnapshot] = []
    for run in runs:
        for result in run.eval_metric_result_per_invocation:
            actual.append(_snapshot_invocation(result.actual_invocation))
            if result.expected_invocation:
                expected.append(_snapshot_invocation(result.expected_invocation))
    return _deduplicate_invocations(actual), _deduplicate_invocations(expected)


def _snapshot_invocation(invocation) -> InvocationSnapshot:
    text = ""
    if invocation.final_response and invocation.final_response.parts:
        text = "".join(part.text or "" for part in invocation.final_response.parts)
    tool_calls = [{
        "name": call.name,
        "args": call.args or {}
    } for call in get_all_tool_calls(invocation.intermediate_data)]
    return InvocationSnapshot(final_text=text, tool_calls=tool_calls)


def _deduplicate_invocations(values: list[InvocationSnapshot]) -> list[InvocationSnapshot]:
    unique: dict[str, InvocationSnapshot] = {}
    for value in values:
        key = value.model_dump_json()
        unique.setdefault(key, value)
    return list(unique.values())


def _mean_optional(values) -> float | None:
    present = [float(value) for value in values if value is not None]
    return mean(present) if present else None


def _validate_split_leakage(train_set: EvalSet, validation_set: EvalSet) -> None:
    train_ids = [case.eval_id for case in train_set.eval_cases]
    validation_ids = [case.eval_id for case in validation_set.eval_cases]
    if len(train_ids) != len(set(train_ids)) or len(validation_ids) != len(set(validation_ids)):
        raise ValueError("case ids must be unique within each split")
    overlap = sorted(set(train_ids) & set(validation_ids))
    if overlap:
        raise ValueError(f"case ids overlap across splits: {overlap}")
    train_hashes = {_case_hash(case) for case in train_set.eval_cases}
    validation_hashes = {_case_hash(case) for case in validation_set.eval_cases}
    if train_hashes & validation_hashes:
        raise ValueError("normalized case content overlaps across splits")


def _validate_gate_references(
    gate: GateConfig,
    optimizer: OptimizeConfigFile,
    validation_set: EvalSet,
) -> None:
    metrics = {metric.metric_name for metric in optimizer.evaluate.get_eval_metrics()}
    if gate.primary_metric not in metrics:
        raise ValueError(f"primary_metric {gate.primary_metric!r} is not configured")
    unknown_metrics = sorted(set(gate.hard_metric_names) - metrics)
    if unknown_metrics:
        raise ValueError(f"unknown hard metrics: {unknown_metrics}")
    validation_ids = {case.eval_id for case in validation_set.eval_cases}
    referenced = set(gate.critical_case_ids) | set(gate.hard_case_ids)
    unknown_cases = sorted(referenced - validation_ids)
    if unknown_cases:
        raise ValueError(f"gate case ids must belong to validation: {unknown_cases}")


def _case_hash(case) -> str:
    payload = case.model_dump(mode="json", by_alias=True)
    payload.pop("eval_id", None)
    payload.pop("evalId", None)
    payload.pop("creation_timestamp", None)
    payload.pop("creationTimestamp", None)
    for field in ("conversation", "actualConversation"):
        for invocation in payload.get(field) or []:
            invocation.pop("invocation_id", None)
            invocation.pop("invocationId", None)
            invocation.pop("creation_timestamp", None)
            invocation.pop("creationTimestamp", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.new(HASH_NAME, encoded.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.new(HASH_NAME, path.read_bytes()).hexdigest()


def _fake_judge_score(runs: list[EvalCaseResult]) -> float | None:
    """Deterministic offline judge used only when --fake-judge is enabled."""
    scores = []
    for run in runs:
        actual = _invocation_snapshots([run])[0]
        expected = _invocation_snapshots([run])[1]
        scores.append(float(bool(actual and expected and actual[0].final_text == expected[0].final_text)))
    return _mean_optional(scores)


def _portable_dataset_path(path: Path) -> str:
    """Avoid the SDK's colon-based case selector on Windows paths."""
    return os.path.relpath(path, Path.cwd())


def _dataset_for_sdk(path: Path, temp_dir: Path) -> str:
    try:
        return _portable_dataset_path(path)
    except (OSError, ValueError):
        pass
    local = temp_dir / path.name
    shutil.copyfile(path, local)
    return _portable_dataset_path(local)
