"""Pure dataset validation, deterministic splitting, normalization and comparison."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Iterable, Mapping

from trpc_agent_sdk.evaluation import EvalConfig, EvalSet, EvalStatus, EvaluateResult

from .models import (
    AttributionSnapshot,
    CaseComparison,
    CaseRun,
    CaseSnapshot,
    ComparisonSnapshot,
    EvaluationSnapshot,
    FailureAttribution,
    MetricDelta,
    MetricRun,
    MetricSnapshot,
    Phase,
    RubricOutcome,
    Split,
    Transition,
)
from .schema import sanitize

_VOLATILE_KEYS = {
    "invocationId",
    "invocation_id",
    "callId",
    "call_id",
    "toolCallId",
    "tool_call_id",
    "executionId",
    "execution_id",
    "eventId",
    "event_id",
    "creationTimestamp",
    "creation_timestamp",
}


def canonical_json(value: Any) -> str:
    """Return deterministic JSON suitable for content hashing."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _without_execution_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_execution_ids(item) for key, item in value.items() if key not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_without_execution_ids(item) for item in value]
    return value


def case_content_fingerprint(case: Any) -> str:
    payload = case.model_dump(mode="json", by_alias=True)
    payload.pop("evalId", None)
    return sha256_json(_without_execution_ids(payload))


def case_input_fingerprint(case: Any) -> str:
    """Fingerprint only model-visible inputs so changed labels cannot hide leakage."""

    invocations = case.conversation or case.actual_conversation or []
    payload = {
        "userContents": [invocation.user_content.model_dump(mode="json", by_alias=True) for invocation in invocations],
        "conversationScenario": (case.conversation_scenario.model_dump(mode="json", by_alias=True)
                                 if case.conversation_scenario is not None else None),
        "sessionInput":
        (case.session_input.model_dump(mode="json", by_alias=True) if case.session_input is not None else None),
        "contextMessages": [item.model_dump(mode="json", by_alias=True) for item in (case.context_messages or [])],
    }
    return sha256_json(_without_execution_ids(payload))


def dataset_contract_payload(eval_set: EvalSet) -> dict[str, Any]:
    """Return the submitted dataset contract without injected defaults."""

    return eval_set.model_dump(mode="json", by_alias=True, exclude_unset=True)


def dataset_fingerprint_payload(payload: Mapping[str, Any]) -> str:
    """Hash a serialized dataset contract after removing execution-only IDs."""

    return sha256_json(_without_execution_ids(payload))


def dataset_fingerprint(eval_set: EvalSet) -> str:
    """Hash the source dataset contract used for replay and trace validation."""

    return dataset_fingerprint_payload(dataset_contract_payload(eval_set))


def dataset_audit_payload(eval_set: EvalSet) -> dict[str, Any]:
    """Return the exact credential-redacted payload persisted in the audit tree."""

    return sanitize(dataset_contract_payload(eval_set), max_text_chars=None)


def dataset_audit_fingerprint(eval_set: EvalSet) -> str:
    """Hash the redacted dataset artifact independently from its source hash."""

    return dataset_fingerprint_payload(dataset_audit_payload(eval_set))


def _validate_case_ids(eval_set: EvalSet, label: str, minimum: int) -> set[str]:
    if len(eval_set.eval_cases) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} cases")
    case_ids = [case.eval_id for case in eval_set.eval_cases]
    if any(not case_id or case_id.strip() != case_id for case_id in case_ids):
        raise ValueError(f"{label} case IDs must be non-empty and already trimmed")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{label} case IDs must be unique")
    return set(case_ids)


def validate_datasets(
    train: EvalSet,
    validation: EvalSet,
    *,
    train_path: str,
    validation_path: str,
    configured_metrics: Iterable[str],
    critical_case_ids: Iterable[str] = (),
    hard_case_ids: Iterable[str] = (),
    metric_weights: Mapping[str, float] | None = None,
    train_case_weights: Mapping[str, float] | None = None,
    validation_case_weights: Mapping[str, float] | None = None,
) -> None:
    """Validate split isolation and all configured references."""

    if train_path.casefold() == validation_path.casefold():
        raise ValueError("train and validation paths must differ")
    train_ids = _validate_case_ids(train, "train", 3)
    validation_ids = _validate_case_ids(validation, "validation", 1)
    overlap = train_ids & validation_ids
    if overlap:
        raise ValueError(f"train and validation share case IDs: {sorted(overlap)}")

    train_fingerprints = {case_content_fingerprint(case) for case in train.eval_cases}
    validation_fingerprints = {case_content_fingerprint(case) for case in validation.eval_cases}
    if train_fingerprints & validation_fingerprints:
        raise ValueError("train and validation contain duplicate case content")
    train_inputs = {case_input_fingerprint(case) for case in train.eval_cases}
    validation_inputs = {case_input_fingerprint(case) for case in validation.eval_cases}
    if train_inputs & validation_inputs:
        raise ValueError("train and validation contain duplicate model inputs")

    validation_references = set(critical_case_ids) | set(hard_case_ids)
    unknown_validation = validation_references - validation_ids
    if unknown_validation:
        raise ValueError(f"critical/hard cases are missing from validation: {sorted(unknown_validation)}")

    metric_list = list(configured_metrics)
    if not metric_list or any(not name or name != name.strip() for name in metric_list):
        raise ValueError("configured metric names must be non-empty and trimmed")
    if len(metric_list) != len(set(metric_list)):
        raise ValueError("configured metric names must be unique")
    metrics = set(metric_list)
    unknown_metrics = set(metric_weights or {}) - metrics
    if unknown_metrics:
        raise ValueError(f"metric weights reference unknown metrics: {sorted(unknown_metrics)}")
    unknown_train = set(train_case_weights or {}) - train_ids
    if unknown_train:
        raise ValueError(f"train weights reference unknown cases: {sorted(unknown_train)}")
    unknown_val = set(validation_case_weights or {}) - validation_ids
    if unknown_val:
        raise ValueError(f"validation weights reference unknown cases: {sorted(unknown_val)}")


def split_train_dataset(
    train: EvalSet,
    baseline: EvaluationSnapshot,
    *,
    seed: int,
    selection_ratio: float,
) -> tuple[EvalSet, EvalSet]:
    """Return deterministic inner-train and selection sets, stratified when possible."""

    case_ids = [case.eval_id for case in train.eval_cases]
    if tuple(case_ids) != baseline.case_ids:
        raise ValueError("baseline train snapshot does not match train dataset")
    if len(case_ids) < 3:
        raise ValueError("inner split requires at least three train cases")
    selection_count = 1 if len(case_ids) == 3 else round(len(case_ids) * selection_ratio)
    selection_count = max(1, min(len(case_ids) - 1, selection_count))

    passed = {case.case_id: case.passed for case in baseline.cases}

    def rank(case_id: str) -> str:
        return hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()

    groups = {
        False: sorted((case_id for case_id in case_ids if not passed[case_id]), key=rank),
        True: sorted((case_id for case_id in case_ids if passed[case_id]), key=rank),
    }
    selection: list[str] = []
    if selection_count >= 2 and groups[False] and groups[True]:
        selection.extend((groups[False].pop(0), groups[True].pop(0)))
    remaining = sorted(groups[False] + groups[True], key=rank)
    selection.extend(remaining[:selection_count - len(selection)])
    selection_ids = set(selection)

    train_cases = [deepcopy(case) for case in train.eval_cases if case.eval_id not in selection_ids]
    selection_cases = [deepcopy(case) for case in train.eval_cases if case.eval_id in selection_ids]
    inner_train_update = {"eval_set_id": f"{train.eval_set_id}_inner_train", "eval_cases": train_cases}
    inner_selection_update = {"eval_set_id": f"{train.eval_set_id}_inner_selection", "eval_cases": selection_cases}
    inner_train = train.model_copy(update=inner_train_update, deep=True)
    inner_selection = train.model_copy(update=inner_selection_update, deep=True)
    return inner_train, inner_selection


def _metric_config(eval_config: EvalConfig) -> tuple[list[str], dict[str, float]]:
    metrics = eval_config.get_eval_metrics()
    names = [metric.metric_name for metric in metrics]
    if not names or len(names) != len(set(names)):
        raise ValueError("configured metric names must be non-empty and unique")
    thresholds = {metric.metric_name: float(metric.threshold) for metric in metrics}
    if any(not math.isfinite(value) for value in thresholds.values()):
        raise ValueError("configured metric thresholds must be finite")
    return names, thresholds


def _status_passed(status: EvalStatus) -> bool:
    if status == EvalStatus.NOT_EVALUATED:
        raise ValueError("metric status cannot be NOT_EVALUATED")
    return status == EvalStatus.PASSED


def _same_model(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return left.model_dump(mode="json", by_alias=True) == right.model_dump(mode="json", by_alias=True)


def _normalize_run(
    result: Any,
    *,
    expected_case: Any,
    expected_run_id: int,
    eval_set_id: str,
    metric_names: list[str],
    thresholds: dict[str, float],
) -> CaseRun:
    if result.eval_set_id != eval_set_id or result.eval_id != expected_case.eval_id:
        raise ValueError("run eval-set/case identity drift")
    if result.run_id != expected_run_id:
        raise ValueError("run IDs must be contiguous and 1-based")
    overall = result.overall_eval_metric_results
    if [item.metric_name for item in overall] != metric_names:
        raise ValueError("overall metric schema drift")

    has_reference = bool(expected_case.conversation)
    expected_invocations = expected_case.conversation or expected_case.actual_conversation or []
    per_invocation = result.eval_metric_result_per_invocation
    if len(per_invocation) != len(expected_invocations):
        raise ValueError("invocation count drift")
    trace: list[dict[str, Any]] = []
    for index, invocation_result in enumerate(per_invocation):
        expected = expected_invocations[index]
        if has_reference:
            if not _same_model(invocation_result.expected_invocation, expected):
                raise ValueError("expected invocation drift")
        else:
            placeholder = invocation_result.expected_invocation
            if (placeholder is None or placeholder.final_response is not None
                    or placeholder.intermediate_data is not None
                    or not _same_model(placeholder.user_content, expected.user_content)):
                raise ValueError("reference-free expected invocation drift")
        if not _same_model(invocation_result.actual_invocation.user_content, expected.user_content):
            raise ValueError("actual user content drift")
        per_metric = invocation_result.eval_metric_results
        if [item.metric_name for item in per_metric] != metric_names:
            raise ValueError("per-invocation metric schema drift")
        for item in per_metric:
            if item.score is None or not math.isfinite(float(item.score)):
                raise ValueError("per-invocation metric score must be finite")
            if float(item.threshold) != thresholds[item.metric_name]:
                raise ValueError("per-invocation metric threshold drift")
            if _status_passed(item.eval_status) != (float(item.score) >= float(item.threshold)):
                raise ValueError("per-invocation metric status drift")
        trace.append({
            "actual": invocation_result.actual_invocation.model_dump(mode="json", by_alias=True),
            "expected": (expected.model_dump(mode="json", by_alias=True) if has_reference else None),
        })

    metrics: list[MetricRun] = []
    for item in overall:
        if item.score is None or not math.isfinite(float(item.score)):
            raise ValueError("overall metric score must be finite")
        if float(item.threshold) != thresholds[item.metric_name]:
            raise ValueError("overall metric threshold drift")
        passed = _status_passed(item.eval_status)
        if passed != (float(item.score) >= float(item.threshold)):
            raise ValueError("overall metric status drift")
        details = item.details
        rubrics: list[RubricOutcome] = []
        if details and details.rubric_scores:
            for rubric in details.rubric_scores:
                if isinstance(rubric, Mapping):
                    rubric_id = rubric.get("rubricId") or rubric.get("rubric_id") or rubric.get("id")
                    rubric_score = rubric.get("score")
                    rubric_reason = rubric.get("reason")
                else:
                    rubric_id = getattr(rubric, "rubric_id", None) or getattr(rubric, "id", None)
                    rubric_score = getattr(rubric, "score", None)
                    rubric_reason = getattr(rubric, "reason", None)
                if not rubric_id or rubric_score is None:
                    raise ValueError("rubric result requires a non-empty id and score")
                score = float(rubric_score)
                rubrics.append(
                    RubricOutcome(
                        id=str(rubric_id),
                        score=score,
                        passed=score >= float(item.threshold),
                        reason=str(rubric_reason) if rubric_reason is not None else None,
                    ))
        metrics.append(
            MetricRun(
                metric_name=item.metric_name,
                score=float(item.score),
                threshold=float(item.threshold),
                passed=passed,
                reason=details.reason if details else None,
                rubrics=tuple(rubrics),
            ))
    final_passed = _status_passed(result.final_eval_status)
    expected_passed = result.error_message is None and all(metric.passed for metric in metrics)
    if final_passed != expected_passed:
        raise ValueError("final run status drift")
    return CaseRun(
        run_id=expected_run_id,
        passed=final_passed,
        metrics=tuple(metrics),
        error=result.error_message,
        trace=tuple(trace),
    )


def _weighted_average(values: Iterable[tuple[float, float]]) -> float:
    pairs = list(values)
    total_weight = sum(weight for _, weight in pairs)
    if not pairs or total_weight <= 0:
        raise ValueError("weighted average requires positive total weight")
    return sum(value * weight for value, weight in pairs) / total_weight


def normalize_result(
    raw: EvaluateResult,
    eval_set: EvalSet,
    eval_config: EvalConfig,
    *,
    split: Split,
    phase: Phase,
    metric_weights: Mapping[str, float] | None = None,
    case_weights: Mapping[str, float] | None = None,
) -> EvaluationSnapshot:
    """Validate and unfold an SDK EvaluateResult exactly once."""

    if set(raw.results_by_eval_set_id) != {eval_set.eval_set_id}:
        raise ValueError("EvaluateResult must contain exactly the expected eval set")
    aggregate = raw.results_by_eval_set_id[eval_set.eval_set_id]
    if aggregate.num_runs != eval_config.num_runs:
        raise ValueError("aggregate run count does not match eval config")
    expected_ids = [case.eval_id for case in eval_set.eval_cases]
    if set(aggregate.eval_results_by_eval_id) != set(expected_ids):
        raise ValueError("EvaluateResult case IDs do not match input dataset")
    metric_names, thresholds = _metric_config(eval_config)
    metric_weight_map = {name: float((metric_weights or {}).get(name, 1.0)) for name in metric_names}

    cases: list[CaseSnapshot] = []
    for expected_case in eval_set.eval_cases:
        raw_runs = aggregate.eval_results_by_eval_id[expected_case.eval_id]
        if len(raw_runs) != eval_config.num_runs:
            raise ValueError("case run count does not match eval config")
        by_run_id = {run.run_id: run for run in raw_runs}
        if set(by_run_id) != set(range(1, eval_config.num_runs + 1)):
            raise ValueError("case run IDs are missing or duplicated")
        runs = tuple(
            _normalize_run(
                by_run_id[run_id],
                expected_case=expected_case,
                expected_run_id=run_id,
                eval_set_id=eval_set.eval_set_id,
                metric_names=metric_names,
                thresholds=thresholds,
            ) for run_id in range(1, eval_config.num_runs + 1))
        metric_snapshots: list[MetricSnapshot] = []
        for metric_name in metric_names:
            run_metrics = [next(metric for metric in run.metrics if metric.metric_name == metric_name) for run in runs]
            score = sum(metric.score for metric in run_metrics) / len(run_metrics)
            metric_snapshots.append(
                MetricSnapshot(
                    metric_name=metric_name,
                    score=score,
                    threshold=thresholds[metric_name],
                    passed=all(metric.passed for metric in run_metrics),
                ))
        case_score = _weighted_average(
            (metric.score, metric_weight_map[metric.metric_name]) for metric in metric_snapshots)
        cases.append(
            CaseSnapshot(
                case_id=expected_case.eval_id,
                passed=all(run.passed for run in runs),
                score=case_score,
                metrics=tuple(metric_snapshots),
                runs=runs,
                error=next((run.error for run in runs if run.error), None),
            ))

    case_weight_map = {case.case_id: float((case_weights or {}).get(case.case_id, 1.0)) for case in cases}
    dataset_score = _weighted_average((case.score, case_weight_map[case.case_id]) for case in cases)
    metric_scores = {
        metric_name:
        _weighted_average((
            next(metric.score for metric in case.metrics if metric.metric_name == metric_name),
            case_weight_map[case.case_id],
        ) for case in cases)
        for metric_name in metric_names
    }
    return EvaluationSnapshot(
        split=split,
        phase=phase,
        dataset_score=dataset_score,
        pass_rate=sum(case.passed for case in cases) / len(cases),
        metric_scores=metric_scores,
        case_ids=tuple(expected_ids),
        cases=tuple(cases),
    )


def compare_snapshots(
    baseline: EvaluationSnapshot,
    candidate: EvaluationSnapshot,
    *,
    epsilon: float,
    critical_case_ids: Iterable[str] = (),
    hard_case_ids: Iterable[str] = (),
    baseline_attribution: AttributionSnapshot | None = None,
    candidate_attribution: AttributionSnapshot | None = None,
) -> ComparisonSnapshot:
    """Compare schema-identical snapshots and classify every case transition."""

    if baseline.split != candidate.split or baseline.case_ids != candidate.case_ids:
        raise ValueError("baseline and candidate case schemas differ")
    if set(baseline.metric_scores) != set(candidate.metric_scores):
        raise ValueError("baseline and candidate metric schemas differ")
    baseline_failures = _attribution_map(baseline_attribution)
    candidate_failures = _attribution_map(candidate_attribution)
    critical = set(critical_case_ids)
    hard = set(hard_case_ids)
    comparisons: list[CaseComparison] = []
    for base_case, new_case in zip(baseline.cases, candidate.cases):
        if base_case.case_id != new_case.case_id:
            raise ValueError("baseline and candidate case ordering differs")
        base_metrics = {metric.metric_name: metric for metric in base_case.metrics}
        new_metrics = {metric.metric_name: metric for metric in new_case.metrics}
        if set(base_metrics) != set(new_metrics):
            raise ValueError("baseline and candidate per-case metric schemas differ")
        delta = new_case.score - base_case.score
        if not base_case.passed and new_case.passed:
            transition = Transition.NEW_PASS
        elif base_case.passed and not new_case.passed:
            transition = Transition.NEW_FAIL
        elif delta > epsilon:
            transition = Transition.IMPROVED
        elif delta < -epsilon:
            transition = Transition.REGRESSED
        else:
            transition = Transition.UNCHANGED
        comparisons.append(
            CaseComparison(
                case_id=base_case.case_id,
                baseline_passed=base_case.passed,
                candidate_passed=new_case.passed,
                baseline_score=base_case.score,
                candidate_score=new_case.score,
                delta=delta,
                metrics=tuple(
                    MetricDelta(
                        metric_name=name,
                        baseline=base_metrics[name].score,
                        candidate=new_metrics[name].score,
                        delta=new_metrics[name].score - base_metrics[name].score,
                    ) for name in base_metrics),
                transition=transition,
                critical=base_case.case_id in critical,
                hard=base_case.case_id in hard,
                baseline_attribution=baseline_failures.get(base_case.case_id),
                candidate_attribution=candidate_failures.get(base_case.case_id),
                new_hard_failure=base_case.case_id in hard and base_case.passed and not new_case.passed,
            ))
    return ComparisonSnapshot(
        split=baseline.split,
        score_delta=candidate.dataset_score - baseline.dataset_score,
        pass_rate_delta=candidate.pass_rate - baseline.pass_rate,
        metric_deltas={
            name: candidate.metric_scores[name] - baseline.metric_scores[name]
            for name in baseline.metric_scores
        },
        cases=tuple(comparisons),
    )


def _attribution_map(snapshot: AttributionSnapshot | None) -> dict[str, FailureAttribution]:
    return {} if snapshot is None else {failure.case_id: failure for failure in snapshot.failures}
