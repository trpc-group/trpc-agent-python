#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Run Champion and Challenger evaluations and persist auditable evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from trpc_agent_sdk.evaluation import AgentEvaluator, EvalCaseResult, EvaluateResult, TargetPrompt

CallAgent = Callable[[str], Awaitable[str]]


@dataclass
class FrozenInputs:
    """Immutable inputs and environment facts for one experiment."""

    run_id: str
    champion_sha256: str
    challenger_sha256: str
    train_sha256: str
    val_sha256: str
    metric_config_sha256: str
    run_config_sha256: str
    optimizer_config_sha256: Optional[str]
    seed: int
    started_at: str
    mode: str
    candidate_source: str
    scenario: Optional[str] = None
    gate_config: dict[str, Any] = field(default_factory=dict)
    model_info: dict[str, Any] = field(default_factory=dict)
    evaluator_info: dict[str, Any] = field(default_factory=dict)
    optimizer_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class SplitResult:
    champion_avg: float
    challenger_avg: float

    @property
    def delta(self) -> float:
        return self.challenger_avg - self.champion_avg


@dataclass
class CaseRecord:
    """Case-level comparison plus evidence needed for attribution."""

    eval_id: str
    split: str
    slice_name: str
    risk_level: str
    protected: bool
    scenario_tag: Optional[str]
    champion_status: str
    challenger_status: str
    champion_score: float
    challenger_score: float
    expected_text: Optional[str]
    actual_text: Optional[str]
    metric_results: list[dict[str, Any]] = field(default_factory=list)
    actual_tool_uses: list[dict[str, Any]] = field(default_factory=list)
    expected_tool_uses: list[dict[str, Any]] = field(default_factory=list)
    actual_tool_responses: list[dict[str, Any]] = field(default_factory=list)
    expected_tool_responses: list[dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    failure_reasons: list[str] = field(default_factory=list)
    trace_ref: Optional[str] = None

    @property
    def delta(self) -> float:
        return self.challenger_score - self.champion_score

    @property
    def transition(self) -> str:
        if self.champion_status == "PASSED" and self.challenger_status != "PASSED":
            return "newly_failed"
        if self.champion_status != "PASSED" and self.challenger_status == "PASSED":
            return "newly_passed"
        if self.delta > 0:
            return "score_up"
        if self.delta < 0:
            return "score_down"
        return "unchanged"

    @property
    def failure_kind(self) -> str:
        if self.challenger_status == "PASSED":
            return "none"
        if self.error_message or self.challenger_status == "NOT_EVALUATED":
            return "infrastructure_failure"
        return "agent_quality_failure"


@dataclass
class RunArtifact:
    frozen: FrozenInputs
    train: SplitResult
    val: SplitResult
    cases: list[CaseRecord]
    champion_train_avg: float
    champion_val_avg: float
    artifact_dir: Path
    cost_status: str
    total_tokens: Optional[int]
    total_cost: Optional[float]
    duration_seconds: float
    champion_prompt_text: str
    challenger_prompt_text: str
    optimizer_artifacts: dict[str, str] = field(default_factory=dict)
    optimizer_rounds: list[dict[str, Any]] = field(default_factory=list)


def new_run_id() -> str:
    """Return a filesystem-safe, collision-resistant UTC run id."""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def sha256_file(path: Path) -> str:
    """Hash UTF-8 text with normalized newlines; binary files hash as bytes."""

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        raw = text.encode("utf-8")
    except UnicodeDecodeError:
        pass
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def _load_evalset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_state(case: dict[str, Any]) -> dict[str, Any]:
    return (case.get("session_input") or {}).get("state") or {}


def _content_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    parts = getattr(content, "parts", None) or []
    texts = [str(getattr(part, "text", "")) for part in parts if getattr(part, "text", None)]
    return "\n".join(texts) if texts else None


def _expected_text(case: dict[str, Any]) -> Optional[str]:
    conversation = case.get("conversation") or []
    if not conversation:
        return None
    parts = (conversation[0].get("final_response") or {}).get("parts") or []
    texts = [str(part.get("text", "")) for part in parts if part.get("text")]
    return "\n".join(texts) if texts else None


def _model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return value
    return {key: getattr(value, key) for key in ("id", "name", "args", "response") if hasattr(value, key)}


def _intermediate_items(invocation: Any, field_name: str) -> list[dict[str, Any]]:
    intermediate = getattr(invocation, "intermediate_data", None)
    values = getattr(intermediate, field_name, None) or []
    return [_model_to_dict(value) for value in values]


def _metric_evidence(result: EvalCaseResult) -> tuple[list[dict[str, Any]], list[str]]:
    metrics: list[dict[str, Any]] = []
    reasons: list[str] = []
    for metric in result.overall_eval_metric_results or []:
        details = getattr(metric, "details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason:
            reasons.append(str(reason))
        metrics.append(
            {
                "metric_name": metric.metric_name,
                "score": metric.score,
                "threshold": metric.threshold,
                "eval_status": getattr(metric.eval_status, "name", str(metric.eval_status)),
                "reason": reason,
                "rubric_scores": [_model_to_dict(score) for score in (getattr(details, "rubric_scores", None) or [])],
            }
        )
    for per_invocation in result.eval_metric_result_per_invocation or []:
        for metric in per_invocation.eval_metric_results or []:
            details = getattr(metric, "details", None)
            reason = getattr(details, "reason", None) if details is not None else None
            if reason and str(reason) not in reasons:
                reasons.append(str(reason))
    return metrics, reasons


def _status(result: EvalCaseResult) -> str:
    return getattr(result.final_eval_status, "name", str(result.final_eval_status))


def _score(result: EvalCaseResult) -> float:
    metrics = list(result.overall_eval_metric_results or [])
    if not metrics:
        return 1.0 if _status(result) == "PASSED" else 0.0
    scores = [float(metric.score) if metric.score is not None else 0.0 for metric in metrics]
    return sum(scores) / len(scores) if scores else 0.0


def _worst_run(runs: list[EvalCaseResult]) -> Optional[EvalCaseResult]:
    if not runs:
        return None
    return min(runs, key=lambda result: (0 if _status(result) == "NOT_EVALUATED" else 1, _score(result)))


def _flatten_results(result: EvaluateResult) -> dict[str, list[EvalCaseResult]]:
    flattened: dict[str, list[EvalCaseResult]] = {}
    for aggregate in (result.results_by_eval_set_id or {}).values():
        for eval_id, runs in (aggregate.eval_results_by_eval_id or {}).items():
            flattened[eval_id] = list(runs or [])
    return flattened


async def _run_evaluator(
    evalset_path: Path,
    *,
    call_agent: Optional[CallAgent] = None,
    metric_config_path: Optional[Path] = None,
) -> EvaluateResult:
    """Run the public evaluator API while avoiding the Windows drive-colon parser."""

    previous_cwd = Path.cwd()
    try:
        os.chdir(evalset_path.parent)
        metric_arg: Optional[str] = None
        if metric_config_path is not None:
            if metric_config_path.parent.resolve() != evalset_path.parent.resolve():
                local_config = evalset_path.parent / metric_config_path.name
                if not local_config.exists():
                    shutil.copyfile(metric_config_path, local_config)
            metric_arg = metric_config_path.name
        executer = AgentEvaluator.get_executer(
            evalset_path.name,
            call_agent=call_agent,
            eval_metrics_file_path_or_dir=metric_arg,
            print_detailed_results=False,
            print_summary_report=False,
        )
        try:
            await executer.evaluate()
        except AssertionError:
            # Case failures are represented as AssertionError by the public facade;
            # the structured result remains available from the executer.
            pass
        result = executer.get_result()
    finally:
        os.chdir(previous_cwd)
    return result or EvaluateResult()


def _make_trace_evalset(base_evalset: dict[str, Any], actual_cases: list[dict[str, Any]]) -> dict[str, Any]:
    trace = dict(base_evalset)
    trace["eval_cases"] = actual_cases
    trace["eval_set_id"] = f"{base_evalset.get('eval_set_id', 'set')}_trace"
    return trace


def _write_trace_evalset(
    target_path: Path,
    base_evalset: dict[str, Any],
    actual_cases: list[dict[str, Any]],
    metric_config_path: Path,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    local_config = target_path.parent / metric_config_path.name
    if not local_config.exists():
        shutil.copyfile(metric_config_path, local_config)
    target_path.write_text(
        json.dumps(_make_trace_evalset(base_evalset, actual_cases), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _case_record(
    *,
    case: dict[str, Any],
    split: str,
    champion_runs: list[EvalCaseResult],
    challenger_runs: list[EvalCaseResult],
) -> CaseRecord:
    champion = _worst_run(champion_runs)
    challenger = _worst_run(challenger_runs)
    state = _case_state(case)
    actual_text: Optional[str] = None
    expected_text = _expected_text(case)
    actual_tools: list[dict[str, Any]] = []
    expected_tools: list[dict[str, Any]] = []
    actual_responses: list[dict[str, Any]] = []
    expected_responses: list[dict[str, Any]] = []
    metric_results: list[dict[str, Any]] = []
    reasons: list[str] = []
    error_message: Optional[str] = None

    if challenger is not None:
        error_message = challenger.error_message
        metric_results, reasons = _metric_evidence(challenger)
        for per_invocation in challenger.eval_metric_result_per_invocation or []:
            actual = per_invocation.actual_invocation
            expected = per_invocation.expected_invocation
            if actual_text is None:
                actual_text = _content_text(getattr(actual, "final_response", None))
            if expected_text is None and expected is not None:
                expected_text = _content_text(getattr(expected, "final_response", None))
            actual_tools.extend(_intermediate_items(actual, "tool_uses"))
            actual_responses.extend(_intermediate_items(actual, "tool_responses"))
            if expected is not None:
                expected_tools.extend(_intermediate_items(expected, "tool_uses"))
                expected_responses.extend(_intermediate_items(expected, "tool_responses"))
    if error_message and error_message not in reasons:
        reasons.insert(0, error_message)

    champion_status = _status(champion) if champion is not None else "NOT_EVALUATED"
    challenger_status = _status(challenger) if challenger is not None else "NOT_EVALUATED"
    return CaseRecord(
        eval_id=case["eval_id"],
        split=split,
        slice_name=str(state.get("slice", "default")),
        risk_level=str(state.get("risk_level", "low")),
        protected=bool(state.get("protected", False)),
        scenario_tag=state.get("scenario_tag"),
        champion_status=champion_status,
        challenger_status=challenger_status,
        champion_score=_score(champion) if champion is not None else 0.0,
        challenger_score=_score(challenger) if challenger is not None else 0.0,
        expected_text=expected_text,
        actual_text=actual_text,
        metric_results=metric_results,
        actual_tool_uses=actual_tools,
        expected_tool_uses=expected_tools,
        actual_tool_responses=actual_responses,
        expected_tool_responses=expected_responses,
        error_message=error_message,
        failure_reasons=reasons,
        trace_ref=f"{split}_eval.json#eval_id={case['eval_id']}",
    )


def _build_case_records(
    *,
    train_base: dict[str, Any],
    val_base: dict[str, Any],
    champion_train: EvaluateResult,
    champion_val: EvaluateResult,
    challenger_train: EvaluateResult,
    challenger_val: EvaluateResult,
) -> list[CaseRecord]:
    records: list[CaseRecord] = []
    for split, cases, champion_result, challenger_result in (
        ("train", train_base["eval_cases"], champion_train, challenger_train),
        ("val", val_base["eval_cases"], champion_val, challenger_val),
    ):
        champion_map = _flatten_results(champion_result)
        challenger_map = _flatten_results(challenger_result)
        for case in cases:
            eval_id = case["eval_id"]
            records.append(
                _case_record(
                    case=case,
                    split=split,
                    champion_runs=champion_map.get(eval_id, []),
                    challenger_runs=challenger_map.get(eval_id, []),
                )
            )
    return records


def _dump_eval_json(path: Path, *, champion: EvaluateResult, challenger: EvaluateResult) -> None:
    def serialize(result: EvaluateResult) -> dict[str, Any]:
        return result.model_dump(mode="json", by_alias=True)

    path.write_text(
        json.dumps(
            {"champion": serialize(champion), "challenger": serialize(challenger)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def run_pair(
    *,
    champion_prompt_path: Path,
    challenger_text: str,
    train_evalset_path: Path,
    val_evalset_path: Path,
    metric_config_path: Path,
    artifact_root: Path,
    mode: str,
    candidate_source: str,
    scenario: Optional[str],
    seed: int,
    artifact_dir: Optional[Path] = None,
    call_agent: Optional[CallAgent] = None,
    run_config: Optional[dict[str, Any]] = None,
    gate_config: Optional[dict[str, Any]] = None,
    optimizer_config_path: Optional[Path] = None,
    model_info: Optional[dict[str, Any]] = None,
    optimizer_info: Optional[dict[str, Any]] = None,
    cost_status: Optional[str] = None,
    total_tokens: Optional[int] = None,
    total_cost: Optional[float] = None,
    optimizer_artifacts: Optional[dict[str, str]] = None,
    optimizer_rounds: Optional[list[dict[str, Any]]] = None,
) -> RunArtifact:
    """Evaluate both prompts on train and validation with guaranteed restoration."""

    started = datetime.now(timezone.utc)
    started_at = started.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    started_monotonic = time.monotonic()
    run_id = artifact_dir.name if artifact_dir is not None else new_run_id()
    artifact_dir = artifact_dir or artifact_root / run_id
    if artifact_dir.exists():
        if (artifact_dir / "frozen.json").exists():
            raise FileExistsError(f"run artifact already exists: {artifact_dir}")
    else:
        artifact_dir.mkdir(parents=True, exist_ok=False)

    champion_text = champion_prompt_path.read_text(encoding="utf-8")
    run_config = run_config or {}
    gate_config = gate_config or {}
    optimizer_info = optimizer_info or {}
    frozen = FrozenInputs(
        run_id=run_id,
        champion_sha256=sha256_text(champion_text),
        challenger_sha256=sha256_text(challenger_text),
        train_sha256=sha256_file(train_evalset_path),
        val_sha256=sha256_file(val_evalset_path),
        metric_config_sha256=sha256_file(metric_config_path),
        run_config_sha256=sha256_json(run_config),
        optimizer_config_sha256=(
            sha256_file(optimizer_config_path)
            if optimizer_config_path is not None and optimizer_config_path.exists()
            else None
        ),
        seed=seed,
        started_at=started_at,
        mode=mode,
        candidate_source=candidate_source,
        scenario=scenario,
        gate_config=gate_config,
        model_info=model_info or {},
        evaluator_info={
            "name": "AgentEvaluator",
            "metric_config": str(metric_config_path),
            "metric_config_sha256": sha256_file(metric_config_path),
        },
        optimizer_info=optimizer_info,
    )

    (artifact_dir / "frozen.json").write_text(
        json.dumps(asdict(frozen), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    champion_dir = artifact_dir / "champion_prompts"
    challenger_dir = artifact_dir / "challenger_prompts"
    champion_dir.mkdir()
    challenger_dir.mkdir()
    (champion_dir / "system.md").write_text(champion_text, encoding="utf-8")
    (challenger_dir / "system.md").write_text(challenger_text, encoding="utf-8")

    train_base = _load_evalset(train_evalset_path)
    val_base = _load_evalset(val_evalset_path)
    target = TargetPrompt().add_path("system", str(champion_prompt_path))
    snapshot = await target.read_all()

    if mode == "fake":
        import fake_agent  # type: ignore[unresolved-import]

        paths = {
            "champion_train": artifact_dir / "champion_train.evalset.json",
            "champion_val": artifact_dir / "champion_val.evalset.json",
            "challenger_train": artifact_dir / "challenger_train.evalset.json",
            "challenger_val": artifact_dir / "challenger_val.evalset.json",
        }
        _write_trace_evalset(
            paths["champion_train"],
            train_base,
            fake_agent.gen_actual_conversation(champion_text, train_base),
            metric_config_path,
        )
        _write_trace_evalset(
            paths["champion_val"],
            val_base,
            fake_agent.gen_actual_conversation(champion_text, val_base),
            metric_config_path,
        )
        _write_trace_evalset(
            paths["challenger_train"],
            train_base,
            fake_agent.gen_actual_conversation(challenger_text, train_base),
            metric_config_path,
        )
        _write_trace_evalset(
            paths["challenger_val"],
            val_base,
            fake_agent.gen_actual_conversation(challenger_text, val_base),
            metric_config_path,
        )
        champion_train = await _run_evaluator(paths["champion_train"], metric_config_path=metric_config_path)
        champion_val = await _run_evaluator(paths["champion_val"], metric_config_path=metric_config_path)
        challenger_train = await _run_evaluator(paths["challenger_train"], metric_config_path=metric_config_path)
        challenger_val = await _run_evaluator(paths["challenger_val"], metric_config_path=metric_config_path)
        resolved_cost_status = "measured"
        resolved_tokens = 0
        resolved_cost = 0.0
    else:
        if call_agent is None:
            raise ValueError("optimize mode requires a real call_agent callback")
        champion_train = await _run_evaluator(
            train_evalset_path,
            call_agent=call_agent,
            metric_config_path=metric_config_path,
        )
        champion_val = await _run_evaluator(
            val_evalset_path,
            call_agent=call_agent,
            metric_config_path=metric_config_path,
        )
        try:
            await target.write_all({"system": challenger_text})
            challenger_train = await _run_evaluator(
                train_evalset_path,
                call_agent=call_agent,
                metric_config_path=metric_config_path,
            )
            challenger_val = await _run_evaluator(
                val_evalset_path,
                call_agent=call_agent,
                metric_config_path=metric_config_path,
            )
        finally:
            await target.write_all(snapshot)
        resolved_cost_status = cost_status or "unavailable"
        resolved_tokens = total_tokens if resolved_cost_status == "measured" else None
        resolved_cost = total_cost if resolved_cost_status == "measured" else None

    cases = _build_case_records(
        train_base=train_base,
        val_base=val_base,
        champion_train=champion_train,
        champion_val=champion_val,
        challenger_train=challenger_train,
        challenger_val=challenger_val,
    )
    train_cases = [case for case in cases if case.split == "train"]
    val_cases = [case for case in cases if case.split == "val"]
    train = SplitResult(
        champion_avg=sum(case.champion_score for case in train_cases) / max(len(train_cases), 1),
        challenger_avg=sum(case.challenger_score for case in train_cases) / max(len(train_cases), 1),
    )
    val = SplitResult(
        champion_avg=sum(case.champion_score for case in val_cases) / max(len(val_cases), 1),
        challenger_avg=sum(case.challenger_score for case in val_cases) / max(len(val_cases), 1),
    )
    _dump_eval_json(artifact_dir / "train_eval.json", champion=champion_train, challenger=challenger_train)
    _dump_eval_json(artifact_dir / "val_eval.json", champion=champion_val, challenger=challenger_val)

    return RunArtifact(
        frozen=frozen,
        train=train,
        val=val,
        cases=cases,
        champion_train_avg=train.champion_avg,
        champion_val_avg=val.champion_avg,
        artifact_dir=artifact_dir,
        cost_status=resolved_cost_status,
        total_tokens=resolved_tokens,
        total_cost=resolved_cost,
        duration_seconds=time.monotonic() - started_monotonic,
        champion_prompt_text=champion_text,
        challenger_prompt_text=challenger_text,
        optimizer_artifacts=optimizer_artifacts or {},
        optimizer_rounds=optimizer_rounds or [],
    )
