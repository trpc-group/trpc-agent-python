# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation, optimization, validation, and gate pipeline."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from .attribution import FailureAttributor
from .delta import DeltaAnalyzer
from .gate import GateEvaluator
from .optimization import PromptOptimizer
from .optimization import deterministic_answer
from .report import MARKDOWN_REPORT_FILENAME
from .report import REPORT_FILENAME
from .report import write_optimization_report
from .types import BaselineCaseRecord
from .types import BaselineOptimizationReport
from .types import BaselineSplitResult
from .types import CandidateEvaluation
from .types import ReportPaths


class EvalOptimizePipeline:
    """Run the full evaluation and optimization loop."""

    def __init__(
        self,
        *,
        train_evalset_path: Path,
        val_evalset_path: Path,
        optimizer_config_path: Path,
        gate_config_path: Path | None = None,
        output_dir: Path | None = None,
        mode: str = "fake",
    ) -> None:
        if mode not in {"real", "fake"}:
            raise ValueError(f"mode must be 'real' or 'fake', got {mode!r}")
        self.train_evalset_path = train_evalset_path
        self.val_evalset_path = val_evalset_path
        self.optimizer_config_path = optimizer_config_path
        self.gate_config_path = gate_config_path or optimizer_config_path.resolve().parent / "gate.json"
        self.mode = mode
        self.example_root = optimizer_config_path.resolve().parent
        self.output_dir = output_dir or self.example_root / "output"

    async def run(self) -> ReportPaths:
        """Run the loop and persist both report formats."""
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        optimizer_payload = _load_optimizer_payload(self.optimizer_config_path)
        gate_config = _load_gate_config(self.gate_config_path)
        prompt_optimizer = PromptOptimizer(
            example_root=self.example_root,
            output_dir=self.output_dir,
            optimizer_config_path=self.optimizer_config_path,
            train_evalset_path=self.train_evalset_path,
            val_evalset_path=self.val_evalset_path,
        )

        if self.mode == "real":
            eval_config = _load_real_eval_config(optimizer_payload)
            train_evalset = _load_real_evalset(self.train_evalset_path)
            val_evalset = _load_real_evalset(self.val_evalset_path)
            train = await self._run_real_split(train_evalset, eval_config)
            val = await self._run_real_split(val_evalset, eval_config)
            call_agent_train_path, call_agent_val_path = prompt_optimizer.write_call_agent_evalsets()
        else:
            metrics = _fake_metrics_from_optimizer(optimizer_payload)
            train_evalset = _load_json(self.train_evalset_path)
            val_evalset = _load_json(self.val_evalset_path)
            train = self._run_fake_split(train_evalset, metrics)
            val = self._run_fake_split(val_evalset, metrics)
            call_agent_train_path = None
            call_agent_val_path = None

        attributor = FailureAttributor()
        attributor.annotate_split(train)
        attributor.annotate_split(val)

        optimization = await prompt_optimizer.optimize(
            mode=self.mode,
            optimizer_payload=optimizer_payload,
            train_baseline=train,
            val_baseline=val,
            call_agent_train_path=call_agent_train_path,
            call_agent_val_path=call_agent_val_path,
        )

        if self.mode == "real":
            candidate_train = await self._run_call_agent_split(
                _load_real_evalset(call_agent_train_path),
                eval_config,
                prompt_optimizer.call_agent_from_prompts(optimization.best_prompts),
            )
            candidate_val = await self._run_call_agent_split(
                _load_real_evalset(call_agent_val_path),
                eval_config,
                prompt_optimizer.call_agent_from_prompts(optimization.best_prompts),
            )
        else:
            candidate_train = self._run_fake_split(train_evalset, metrics, candidate_prompts=optimization.best_prompts)
            candidate_val = self._run_fake_split(val_evalset, metrics, candidate_prompts=optimization.best_prompts)

        attributor.annotate_split(candidate_train)
        attributor.annotate_split(candidate_val)
        delta = DeltaAnalyzer().analyze(
            train_baseline=train,
            train_candidate=candidate_train,
            val_baseline=val,
            val_candidate=candidate_val,
        )
        gate_decision = GateEvaluator(gate_config).evaluate(delta=delta, optimization=optimization)
        finished_at = datetime.now(timezone.utc)
        duration_seconds = time.perf_counter() - started
        output_paths = {
            "json": _relative_to_example(self.output_dir / REPORT_FILENAME, self.example_root),
            "markdown": _relative_to_example(self.output_dir / MARKDOWN_REPORT_FILENAME, self.example_root),
        }

        report = BaselineOptimizationReport(
            mode=self.mode,
            train=train,
            val=val,
            run={
                "mode": self.mode,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": round(duration_seconds, 6),
                "seed": optimization.seed,
            },
            inputs={
                "train_evalset_path": _relative_to_example(self.train_evalset_path, self.example_root),
                "val_evalset_path": _relative_to_example(self.val_evalset_path, self.example_root),
                "optimizer_config_path": _relative_to_example(self.optimizer_config_path, self.example_root),
                "gate_config_path": _relative_to_example(self.gate_config_path, self.example_root),
                "prompt_paths": {
                    name: _relative_to_example(path, self.example_root)
                    for name, path in prompt_optimizer.prompt_paths().items()
                },
            },
            config={
                "gate": gate_config.to_dict(),
                "optimizer": optimizer_payload,
            },
            failure_attribution=_failure_attribution_payload(
                baseline_train=train,
                baseline_val=val,
                candidate_train=candidate_train,
                candidate_val=candidate_val,
            ),
            candidate=CandidateEvaluation(
                train=candidate_train,
                val=candidate_val,
                prompts=optimization.best_prompts,
            ),
            optimization=optimization,
            delta=delta,
            gate_decision=gate_decision,
            metadata={
                "example_root": ".",
                "reproduction_command": f"python run_pipeline.py --mode {self.mode}",
                "output_dir": _relative_to_example(self.output_dir, self.example_root),
                "output_paths": output_paths,
            },
        )
        return write_optimization_report(report, self.output_dir)

    async def _run_real_split(
        self,
        eval_set: Any,
        eval_config: Any,
    ) -> BaselineSplitResult:
        from trpc_agent_sdk.evaluation import AgentEvaluator
        from trpc_agent_sdk.evaluation import EvalSet

        records: list[BaselineCaseRecord] = []
        for case in eval_set.eval_cases:
            one_case_set = EvalSet(
                eval_set_id=eval_set.eval_set_id,
                app_name=eval_set.app_name,
                name=eval_set.name,
                description=eval_set.description,
                eval_cases=[case],
            )
            started = time.perf_counter()
            _, _, _, eval_results = await AgentEvaluator.evaluate_eval_set(
                one_case_set,
                eval_config=eval_config,
                print_detailed_results=False,
            )
            latency = time.perf_counter() - started
            runs = eval_results.get(case.eval_id, [])
            if not runs:
                records.append(_missing_result_record(case, latency))
                continue
            records.append(_record_from_eval_case_result(runs[0], latency))
        return BaselineSplitResult(eval_set_id=eval_set.eval_set_id, cases=records)

    def _run_fake_split(
        self,
        eval_set: dict[str, Any],
        metrics: list[dict[str, Any]],
        candidate_prompts: dict[str, str] | None = None,
    ) -> BaselineSplitResult:
        records = []
        for case in eval_set.get("eval_cases", []):
            started = time.perf_counter()
            records.append(
                _fake_record(
                    case,
                    metrics,
                    time.perf_counter() - started,
                    candidate_prompts=candidate_prompts,
                ))
        return BaselineSplitResult(eval_set_id=str(eval_set.get("eval_set_id", "")), cases=records)

    async def _run_call_agent_split(
        self,
        eval_set: Any,
        eval_config: Any,
        call_agent: Any,
    ) -> BaselineSplitResult:
        from trpc_agent_sdk.evaluation import AgentEvaluator
        from trpc_agent_sdk.evaluation import EvalSet

        records: list[BaselineCaseRecord] = []
        for case in eval_set.eval_cases:
            one_case_set = EvalSet(
                eval_set_id=eval_set.eval_set_id,
                app_name=eval_set.app_name,
                name=eval_set.name,
                description=eval_set.description,
                eval_cases=[case],
            )
            started = time.perf_counter()
            _, _, _, eval_results = await AgentEvaluator.evaluate_eval_set(
                one_case_set,
                call_agent=call_agent,
                eval_config=eval_config,
                print_detailed_results=False,
            )
            latency = time.perf_counter() - started
            runs = eval_results.get(case.eval_id, [])
            if not runs:
                records.append(_missing_result_record(case, latency))
                continue
            records.append(_record_from_eval_case_result(runs[0], latency))
        return BaselineSplitResult(eval_set_id=eval_set.eval_set_id, cases=records)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_to_example(path: Path, example_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(example_root.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), example_root.resolve())


def _failure_attribution_payload(
    *,
    baseline_train: BaselineSplitResult,
    baseline_val: BaselineSplitResult,
    candidate_train: BaselineSplitResult,
    candidate_val: BaselineSplitResult,
) -> dict[str, Any]:
    splits = {
        ("baseline", "train"): baseline_train,
        ("baseline", "val"): baseline_val,
        ("candidate", "train"): candidate_train,
        ("candidate", "val"): candidate_val,
    }
    failed_cases = []
    overall_summary: dict[str, int] = {}
    for (variant, split), result in splits.items():
        for category, count in result.failure_attribution_summary().items():
            overall_summary[category] = overall_summary.get(category, 0) + count
        for case in result.cases:
            if not case.failure_analysis:
                continue
            failed_cases.append({
                "variant": variant,
                "split": split,
                "case_id": case.id,
                "category": case.failure_analysis.category,
                "confidence": case.failure_analysis.confidence,
                "explanation": case.failure_analysis.explanation,
                "evidence": case.failure_analysis.evidence,
            })
    return {
        "train_summary": {
            "baseline": baseline_train.failure_attribution_summary(),
            "candidate": candidate_train.failure_attribution_summary(),
        },
        "val_summary": {
            "baseline": baseline_val.failure_attribution_summary(),
            "candidate": candidate_val.failure_attribution_summary(),
        },
        "overall_summary": overall_summary,
        "failed_cases": failed_cases,
    }


def _load_optimizer_payload(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    evaluate = payload.get("evaluate")
    if not isinstance(evaluate, dict):
        raise ValueError(f"{path} must contain an object field named 'evaluate'")
    return payload


def _load_gate_config(path: Path) -> Any:
    from .types import GateConfig

    if not path.exists():
        return GateConfig()
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return GateConfig.from_dict(payload)


def _load_real_evalset(path: Path) -> Any:
    from trpc_agent_sdk.evaluation import EvalSet

    return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))


def _load_real_eval_config(payload: dict[str, Any]) -> Any:
    from trpc_agent_sdk.evaluation import EvalConfig

    return EvalConfig.model_validate(payload["evaluate"])


def _fake_metrics_from_optimizer(payload: dict[str, Any]) -> list[dict[str, Any]]:
    evaluate = payload["evaluate"]
    metrics = evaluate.get("metrics")
    if isinstance(metrics, list):
        return [metric for metric in metrics if isinstance(metric, dict)]
    criteria = evaluate.get("criteria") or {}
    if not isinstance(criteria, dict):
        return []
    return [{
        "metric_name": name,
        "threshold": value if isinstance(value, (int, float)) else value.get("threshold", 1.0),
        "criterion": value.get("criterion") if isinstance(value, dict) else None,
    } for name, value in criteria.items()]


def _record_from_eval_case_result(result: Any, latency: float) -> BaselineCaseRecord:
    metric_scores = {
        metric.metric_name: float(metric.score)
        for metric in result.overall_eval_metric_results if metric.score is not None
    }
    metric_score = _mean(metric_scores.values())
    passed = _status_name(result.final_eval_status) == "PASSED"
    return BaselineCaseRecord(
        id=result.eval_id,
        metric_score=metric_score,
        metric_scores=metric_scores,
        passed=passed,
        failure_reason="" if passed else _failure_reason_from_result(result),
        trace=_trace_from_result(result),
        latency=latency,
        cost=0.0,
        evaluator_metadata=_evaluator_metadata_from_result(result),
    )


def _missing_result_record(case: Any, latency: float) -> BaselineCaseRecord:
    return BaselineCaseRecord(
        id=str(getattr(case, "eval_id", "")),
        metric_score=0.0,
        metric_scores={},
        passed=False,
        failure_reason="No EvalCaseResult was produced.",
        trace=_trace_from_case(case),
        latency=latency,
        cost=0.0,
        evaluator_metadata=_missing_result_metadata(case),
    )


def _failure_reason_from_result(result: Any) -> str:
    if result.error_message:
        return result.error_message
    for metric in result.overall_eval_metric_results:
        if _status_name(metric.eval_status) == "PASSED":
            continue
        reason = metric.details.reason if metric.details and metric.details.reason else None
        if reason:
            return reason
        return f"{metric.metric_name} failed."
    return "Case did not pass."


def _trace_from_result(result: Any) -> dict[str, Any]:
    per_invocations = result.eval_metric_result_per_invocation or []
    if not per_invocations:
        return {}
    first = per_invocations[0]
    return _trace_from_invocations(
        expected=first.expected_invocation,
        actual=first.actual_invocation,
    )


def _trace_from_case(case: Any) -> dict[str, Any]:
    expected = case.conversation[0] if case.conversation else None
    actual = case.actual_conversation[0] if case.actual_conversation else None
    return _trace_from_invocations(expected=expected, actual=actual)


def _trace_from_invocations(
    *,
    expected: Any | None,
    actual: Any | None,
) -> dict[str, Any]:
    from trpc_agent_sdk.evaluation import get_all_tool_calls

    return {
        "user":
        _content_text(actual.user_content if actual else expected.user_content if expected else None),
        "expected":
        _content_text(expected.final_response if expected else None),
        "actual":
        _content_text(actual.final_response if actual else None),
        "tool_calls": [{
            "name": call.name,
            "args": call.args or {},
        } for call in get_all_tool_calls(actual.intermediate_data if actual else None)],
    }


def _evaluator_metadata_from_result(result: Any) -> dict[str, Any]:
    failed_metrics = []
    overall_metrics = []
    for metric in result.overall_eval_metric_results:
        metric_entry = _metric_result_entry(metric)
        overall_metrics.append(metric_entry)
        if metric_entry["eval_status"] != "PASSED":
            failed_metrics.append(metric_entry)

    expected_invocation, actual_invocation = _first_invocation_pair(result)
    return {
        "error_message":
        result.error_message,
        "final_eval_status":
        _status_name(result.final_eval_status),
        "overall_metric_results":
        overall_metrics,
        "failed_metric_results":
        failed_metrics,
        "expected_tool_calls":
        _tool_calls_from_invocation(expected_invocation.intermediate_data if expected_invocation else None),
        "actual_tool_calls":
        _tool_calls_from_invocation(actual_invocation.intermediate_data if actual_invocation else None),
        "expected_final_response":
        _content_text(expected_invocation.final_response if expected_invocation else None),
        "actual_final_response":
        _content_text(actual_invocation.final_response if actual_invocation else None),
    }


def _first_invocation_pair(result: Any) -> tuple[Any | None, Any | None]:
    per_invocations = result.eval_metric_result_per_invocation or []
    if not per_invocations:
        return None, None
    first = per_invocations[0]
    return first.expected_invocation, first.actual_invocation


def _metric_result_entry(metric: Any) -> dict[str, Any]:
    details = metric.details
    return {
        "metric_name": metric.metric_name,
        "score": float(metric.score) if metric.score is not None else None,
        "threshold": float(metric.threshold) if metric.threshold is not None else None,
        "eval_status": _status_name(metric.eval_status),
        "reason": details.reason if details else None,
        "rubric_scores": getattr(details, "rubric_scores", None) if details else None,
    }


def _tool_calls_from_invocation(intermediate_data: Any | None) -> list[dict[str, Any]]:
    from trpc_agent_sdk.evaluation import get_all_tool_calls

    return [{
        "name": call.name,
        "args": call.args or {},
    } for call in get_all_tool_calls(intermediate_data)]


def _missing_result_metadata(case: Any) -> dict[str, Any]:
    expected_invocation = _first_case_invocation(case, "conversation")
    actual_invocation = _first_case_invocation(case, "actual_conversation")
    return {
        "error_message":
        "No EvalCaseResult was produced.",
        "failed_metric_results": [],
        "overall_metric_results": [],
        "expected_tool_calls":
        _tool_calls_from_invocation(expected_invocation.intermediate_data if expected_invocation else None),
        "actual_tool_calls":
        _tool_calls_from_invocation(actual_invocation.intermediate_data if actual_invocation else None),
        "expected_final_response":
        _content_text(expected_invocation.final_response if expected_invocation else None),
        "actual_final_response":
        _content_text(actual_invocation.final_response if actual_invocation else None),
    }


def _first_case_invocation(case: Any, field_name: str) -> Any | None:
    invocations = getattr(case, field_name, None)
    if not invocations:
        return None
    return invocations[0]


def _fake_record(
    case: dict[str, Any],
    metrics: list[dict[str, Any]],
    latency: float,
    *,
    candidate_prompts: dict[str, str] | None = None,
) -> BaselineCaseRecord:
    conversation = case.get("conversation") or []
    actual_conversation = case.get("actual_conversation") or case.get("actualConversation") or []
    expected = conversation[0] if conversation else None
    if candidate_prompts:
        actual = _candidate_invocation(case, expected, candidate_prompts)
    else:
        actual = actual_conversation[0] if actual_conversation else None
    metric_scores: dict[str, float] = {}
    failed_metrics: list[str] = []
    metric_results: list[dict[str, Any]] = []

    for metric in metrics:
        score = _fake_metric_score(metric, expected=expected, actual=actual)
        metric_name = str(metric.get("metric_name") or metric.get("metricName") or "")
        metric_scores[metric_name] = score
        threshold = float(metric.get("threshold", 1.0))
        metric_result = {
            "metric_name": metric_name,
            "score": score,
            "threshold": threshold,
            "eval_status": "PASSED" if score >= threshold else "FAILED",
            "reason": _fake_metric_reason(metric_name, score, threshold, expected=expected, actual=actual),
            "rubric_scores": None,
        }
        metric_results.append(metric_result)
        if score < threshold:
            failed_metrics.append(metric_name)

    passed = not failed_metrics
    return BaselineCaseRecord(
        id=str(case.get("eval_id", "")),
        metric_score=_mean(metric_scores.values()),
        metric_scores=metric_scores,
        passed=passed,
        failure_reason="" if passed else f"Failed metrics: {', '.join(failed_metrics)}.",
        trace=_fake_trace(expected=expected, actual=actual),
        latency=latency,
        cost=0.0,
        evaluator_metadata={
            "error_message": None,
            "final_eval_status": "PASSED" if passed else "FAILED",
            "overall_metric_results": metric_results,
            "failed_metric_results": [entry for entry in metric_results if entry["eval_status"] != "PASSED"],
            "expected_tool_calls": _raw_tool_calls(expected),
            "actual_tool_calls": _raw_tool_calls(actual),
            "expected_final_response": _raw_content_text(expected.get("final_response") if expected else None),
            "actual_final_response": _raw_content_text(actual.get("final_response") if actual else None),
        },
    )


def _candidate_invocation(
    case: dict[str, Any],
    expected: dict[str, Any] | None,
    candidate_prompts: dict[str, str],
) -> dict[str, Any]:
    source = expected or {}
    user_content = source.get("user_content") or {}
    query = _raw_content_text(user_content)
    return {
        "invocation_id": f"{case.get('eval_id', 'candidate')}-candidate",
        "user_content": user_content,
        "final_response": {
            "parts": [{
                "text": deterministic_answer(query, candidate_prompts),
            }],
            "role": "model",
        },
    }


def _fake_metric_score(
    metric: dict[str, Any],
    *,
    expected: dict[str, Any] | None,
    actual: dict[str, Any] | None,
) -> float:
    metric_name = str(metric.get("metric_name") or metric.get("metricName") or "")
    if metric_name == "final_response_avg_score":
        expected_text = _raw_content_text(expected.get("final_response") if expected else None)
        actual_text = _raw_content_text(actual.get("final_response") if actual else None)
        return 1.0 if _text_matches(metric, actual_text, expected_text) else 0.0
    if metric_name == "tool_trajectory_avg_score":
        expected_calls = _raw_tool_calls(expected)
        actual_calls = _raw_tool_calls(actual)
        return 1.0 if actual_calls == expected_calls else 0.0
    return 1.0


def _fake_metric_reason(
    metric_name: str,
    score: float,
    threshold: float,
    *,
    expected: dict[str, Any] | None,
    actual: dict[str, Any] | None,
) -> str | None:
    if score >= threshold:
        return None
    if metric_name == "final_response_avg_score":
        return "Final response does not match the reference answer."
    if "tool_trajectory" in metric_name:
        return "Tool trajectory does not match the reference trace."
    if "rubric" in metric_name:
        return "LLM rubric score is below threshold."
    if "recall" in metric_name:
        return "Knowledge recall is below threshold."
    if "format" in metric_name or "json" in metric_name:
        return "Output format does not satisfy the requested constraint."
    actual_text = _raw_content_text(actual.get("final_response") if actual else None)
    expected_text = _raw_content_text(expected.get("final_response") if expected else None)
    return f"Metric {metric_name} failed for actual={actual_text!r} expected={expected_text!r}."


def _text_matches(metric: dict[str, Any], actual: str, expected: str) -> bool:
    strategy = _final_response_text_strategy(metric)
    match = str(strategy.get("match", "exact")).lower()
    case_insensitive = bool(strategy.get("case_insensitive", False))
    if case_insensitive:
        actual = actual.lower()
        expected = expected.lower()
    if match == "contains":
        return expected in actual
    if match == "regex":
        flags = re.IGNORECASE if case_insensitive else 0
        return re.search(expected, actual, flags=flags) is not None
    return actual == expected


def _final_response_text_strategy(metric: dict[str, Any]) -> dict[str, Any]:
    criterion = metric.get("criterion") or {}
    final_response = criterion.get("final_response") or criterion.get("finalResponse") or {}
    text = final_response.get("text") or {}
    return text if isinstance(text, dict) else {}


def _content_text(content: Any | None) -> str:
    if not content or not content.parts:
        return ""
    return "\n".join(part.text or "" for part in content.parts if part.text).strip()


def _fake_trace(
    *,
    expected: dict[str, Any] | None,
    actual: dict[str, Any] | None,
) -> dict[str, Any]:
    user_content = (actual or expected or {}).get("user_content")
    return {
        "user": _raw_content_text(user_content),
        "expected": _raw_content_text(expected.get("final_response") if expected else None),
        "actual": _raw_content_text(actual.get("final_response") if actual else None),
        "tool_calls": _raw_tool_calls(actual),
    }


def _raw_content_text(content: dict[str, Any] | None) -> str:
    if not content:
        return ""
    parts = content.get("parts") or []
    return "\n".join(str(part.get("text") or "") for part in parts
                     if isinstance(part, dict) and part.get("text")).strip()


def _raw_tool_calls(invocation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not invocation:
        return []
    intermediate = invocation.get("intermediate_data") or invocation.get("intermediateData") or {}
    tool_uses = intermediate.get("tool_uses") or intermediate.get("toolUses") or []
    calls = []
    for call in tool_uses:
        if not isinstance(call, dict):
            continue
        calls.append({
            "name": call.get("name"),
            "args": call.get("args") or {},
        })
    return calls


def _status_name(status: Any) -> str:
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name
    return str(status)


def _mean(values: Any) -> float:
    numbers = list(values)
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


BaselinePipeline = EvalOptimizePipeline
