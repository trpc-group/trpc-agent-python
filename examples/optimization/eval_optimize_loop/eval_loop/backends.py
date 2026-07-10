"""Backend-neutral adapters for fake and SDK optimization paths."""

from __future__ import annotations

import asyncio
import importlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol

from .diffing import make_unified_diff
from .evaluator import ExampleEvaluator
from .fake_judge import FakeJudge
from .fake_model import FakeModel
from .loader import load_eval_cases
from .loader import read_json
from .optimizer import FakeOptimizer
from .schemas import CandidatePrompt
from .schemas import CaseResult
from .schemas import CostSummary
from .schemas import EvalCase
from .schemas import EvalResult
from .schemas import OptimizationResult
from .schemas import OptimizationRound
from .writeback import snapshot_prompt_files
from .writeback import temporary_prompt_bundle


class EvaluationBackend(Protocol):
    """Common asynchronous evaluation contract."""

    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: str | Path,
        split: str,
        trace: bool,
        artifact_dir: str | Path,
    ) -> EvalResult:
        ...


class OptimizationBackend(Protocol):
    """Common asynchronous optimization contract."""

    async def optimize_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: str | Path,
        validation_path: str | Path,
        config_path: str | Path,
        artifact_dir: str | Path,
    ) -> OptimizationResult:
        ...


@dataclass
class FakeBackend:
    """Deterministic backend used by the self-contained example."""

    seed: int = 91
    trace_enabled: bool = False

    def __post_init__(self) -> None:
        self._optimizer = FakeOptimizer()

    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: str | Path,
        split: str,
        trace: bool,
        artifact_dir: str | Path,
    ) -> EvalResult:
        del artifact_dir
        prompt = _required_system_prompt(prompts, context=f"cannot evaluate {prompt_id}")
        cases = load_eval_cases(dataset_path, split=split)
        evaluator = ExampleEvaluator(
            FakeModel(seed=self.seed),
            FakeJudge(),
            trace_enabled=trace,
        )
        return evaluator.evaluate(
            prompt_id=prompt_id,
            prompt=prompt,
            cases=cases,
            split=split,
        )

    async def optimize_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: str | Path,
        validation_path: str | Path,
        config_path: str | Path,
        artifact_dir: str | Path,
    ) -> OptimizationResult:
        del train_path, validation_path, config_path, artifact_dir
        baseline_prompt = _required_system_prompt(
            baseline_prompts,
            context="cannot optimize fake prompt bundle",
        )
        candidates = _normalize_fake_candidates(self._optimizer.propose(baseline_prompt))
        zero_cost = CostSummary(complete=True)
        rounds = [
            OptimizationRound(
                round_id=index,
                candidate_id=candidate.candidate_id,
                prompts=candidate.bundle(),
                rationale=candidate.rationale,
                metrics={},
                cost=zero_cost,
                duration_seconds=0.0,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]
        return OptimizationResult(
            candidates=candidates,
            rounds=rounds,
            cost=zero_cost,
            raw_summary={
                "backend": "fake",
                "baseline_prompt_id": baseline_train.prompt_id,
                "failure_summary": _safe_jsonable(failure_summary),
            },
        )

    def optimize(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
        """Compatibility wrapper for the pre-async fake pipeline."""

        del train_path, val_path, optimizer_config_path, output_dir
        return _normalize_fake_candidates(self._optimizer.propose(baseline_prompt))


class SDKBackend:
    """Adapter around SDK AgentEvaluator, AgentOptimizer, and TargetPrompt.

    update_source is accepted only through legacy keyword forwarding so the
    current synchronous pipeline can transition independently. It is never
    stored or delegated; source writeback belongs to the wrapper after gating.
    """

    def __init__(
        self,
        prompt_path: str | Path,
        call_agent_path: str | None = None,
        target_prompt_paths: dict[str, str | Path] | None = None,
        **legacy_options: object,
    ) -> None:
        unexpected = sorted(set(legacy_options) - {"update_source"})
        if unexpected:
            raise TypeError(f"unexpected SDKBackend options: {', '.join(unexpected)}")
        self.prompt_path = prompt_path
        self.call_agent_path = call_agent_path
        self.target_prompt_paths = dict(target_prompt_paths) if target_prompt_paths else None
        self.last_result: Any | None = None
        self.last_result_summary: dict[str, Any] | None = None
        self.last_artifact_dir: str | None = None
        self.last_baseline_prompts: dict[str, str] | None = None
        self.last_best_prompts: dict[str, str] | None = None

    def optimize(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
        """Safely bridge old synchronous callers to the async implementation."""

        if _has_running_loop():
            raise ValueError(
                "SDKBackend.optimize() cannot be called while an event loop is already running; "
                "use await SDKBackend.optimize_async(...) instead."
            )
        return asyncio.run(
            self.optimize_async(
                baseline_prompt=baseline_prompt,
                train_path=train_path,
                val_path=val_path,
                optimizer_config_path=optimizer_config_path,
                output_dir=output_dir,
            )
        )

    async def optimize_async(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
        """Compatibility async wrapper returning the historical candidate list."""

        target_paths = self._target_prompt_paths()
        try:
            baseline_prompts = _read_prompt_bundle(target_paths)
        except FileNotFoundError:
            # Let optimize_candidates report dependency/import failures before
            # it reaches its authoritative source snapshot.
            baseline_prompts = {name: baseline_prompt for name in target_paths}
        result = await self.optimize_candidates(
            baseline_prompts=baseline_prompts,
            baseline_train=EvalResult(
                prompt_id="baseline",
                split="train",
                score=0.0,
                passed=False,
                cost=0.0,
                cases=[],
            ),
            failure_summary={},
            train_path=train_path,
            validation_path=val_path,
            config_path=optimizer_config_path,
            artifact_dir=output_dir,
        )
        return result.candidates

    async def optimize_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: str | Path,
        validation_path: str | Path,
        config_path: str | Path,
        artifact_dir: str | Path,
    ) -> OptimizationResult:
        del baseline_train, failure_summary
        call_agent = self._load_required_call_agent(for_evaluation=False)
        try:
            from trpc_agent_sdk.evaluation import AgentOptimizer
            from trpc_agent_sdk.evaluation import TargetPrompt
        except Exception as exc:  # pragma: no cover - depends on optional SDK import health
            raise ValueError(f"sdk mode could not import AgentOptimizer/TargetPrompt: {exc}") from exc

        target_paths = self._target_prompt_paths()
        snapshot = snapshot_prompt_files(target_paths)
        source_bundle = _prompt_bundle_from_snapshot(snapshot)
        baseline_bundle = _validated_prompt_bundle(
            baseline_prompts,
            target_paths,
            context="baseline prompt bundle",
        )
        mismatched = sorted(
            name for name in target_paths if baseline_bundle[name] != source_bundle[name]
        )
        if mismatched:
            raise ValueError(
                "baseline prompt bundle does not match registered source prompt files: "
                + ", ".join(mismatched)
            )

        target_prompt = TargetPrompt()
        for name, path in target_paths.items():
            target_prompt.add_path(name, str(path))

        try:
            sdk_result = await AgentOptimizer.optimize(
                config_path=str(config_path),
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=str(train_path),
                validation_dataset_path=str(validation_path),
                output_dir=str(artifact_dir),
                update_source=False,
                verbose=0,
            )
        finally:
            changed_sources = _changed_snapshot_files(snapshot)
            if changed_sources:
                raise RuntimeError(
                    "AgentOptimizer modified source prompt files despite update_source=False: "
                    + ", ".join(changed_sources)
                )

        _require_successful_optimize_result(sdk_result)
        total_llm_cost = _nonnegative_result_field(
            "total_llm_cost",
            getattr(sdk_result, "total_llm_cost", 0.0),
        )
        _pass_rate_result_field(
            "baseline_pass_rate",
            getattr(sdk_result, "baseline_pass_rate", 0.0),
        )
        _pass_rate_result_field(
            "best_pass_rate",
            getattr(sdk_result, "best_pass_rate", 0.0),
        )
        _finite_result_field(
            "pass_rate_improvement",
            getattr(sdk_result, "pass_rate_improvement", 0.0),
        )
        _nonnegative_result_field(
            "duration_seconds",
            getattr(sdk_result, "duration_seconds", 0.0),
        )
        best_raw = getattr(sdk_result, "best_prompts", None)
        if not best_raw:
            raise ValueError("sdk mode completed but OptimizeResult.best_prompts was empty")
        best_prompts = _validated_prompt_bundle(
            best_raw,
            target_paths,
            context="OptimizeResult.best_prompts",
        )

        candidates: list[CandidatePrompt] = []
        rounds: list[OptimizationRound] = []
        seen_bundles: set[tuple[tuple[str, str], ...]] = set()
        seen_round_ids: set[int] = set()
        for index, sdk_round in enumerate(getattr(sdk_result, "rounds", []) or [], start=1):
            round_id = _round_id(sdk_round, fallback=index)
            if round_id in seen_round_ids:
                raise ValueError(f"duplicate SDK round id: {round_id}")
            seen_round_ids.add(round_id)
            candidate_id = f"sdk_round_{round_id:03d}"
            round_raw_prompts = getattr(sdk_round, "candidate_prompts", {}) or {}
            round_prompts = (
                _validated_prompt_bundle(
                    round_raw_prompts,
                    target_paths,
                    context=f"SDK round {round_id} candidate_prompts",
                )
                if round_raw_prompts
                else {}
            )
            rationale = str(getattr(sdk_round, "acceptance_reason", "") or "")
            round_metrics = _finite_metric_map(
                getattr(sdk_round, "metric_breakdown", {}) or {},
                context=f"SDK round {round_id} metric_breakdown",
            )
            _pass_rate_number(
                getattr(sdk_round, "train_pass_rate", 0.0),
                context=f"SDK round {round_id} train_pass_rate",
            )
            _pass_rate_number(
                getattr(sdk_round, "validation_pass_rate", 0.0),
                context=f"SDK round {round_id} validation_pass_rate",
            )
            round_cost_value = _nonnegative_number(
                getattr(sdk_round, "round_llm_cost", 0.0),
                context=f"SDK round {round_id} round_llm_cost",
            )
            duration_seconds = _nonnegative_number(
                getattr(sdk_round, "duration_seconds", 0.0),
                context=f"SDK round {round_id} duration_seconds",
            )
            rounds.append(
                OptimizationRound(
                    round_id=round_id,
                    candidate_id=candidate_id,
                    prompts=round_prompts,
                    rationale=rationale,
                    metrics=round_metrics,
                    cost=CostSummary(
                        optimizer=round_cost_value,
                        total=round_cost_value,
                        complete=False,
                    ),
                    duration_seconds=duration_seconds,
                )
            )
            if round_prompts:
                bundle_key = _prompt_bundle_key(round_prompts)
                if bundle_key not in seen_bundles:
                    seen_bundles.add(bundle_key)
                    candidates.append(
                        _candidate_from_bundle(
                            candidate_id=candidate_id,
                            prompts=round_prompts,
                            rationale=rationale,
                            baseline_prompts=baseline_bundle,
                        )
                    )

        best_key = _prompt_bundle_key(best_prompts)
        if best_key not in seen_bundles:
            candidates.append(
                _candidate_from_bundle(
                    candidate_id="sdk_best",
                    prompts=best_prompts,
                    rationale="Best prompt returned by AgentOptimizer.optimize.",
                    baseline_prompts=baseline_bundle,
                )
            )

        raw_summary = _summarize_sdk_result(sdk_result)
        cost = CostSummary(
            optimizer=total_llm_cost,
            total=total_llm_cost,
            complete=False,
        )
        result = OptimizationResult(
            candidates=candidates,
            rounds=rounds,
            cost=cost,
            raw_summary=raw_summary,
        )
        self.last_result = sdk_result
        self.last_result_summary = raw_summary
        self.last_artifact_dir = str(artifact_dir)
        self.last_baseline_prompts = baseline_bundle
        self.last_best_prompts = best_prompts
        return result

    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: str | Path,
        split: str,
        trace: bool,
        artifact_dir: str | Path,
    ) -> EvalResult:
        del trace
        call_agent = self._load_required_call_agent(for_evaluation=True)
        try:
            from trpc_agent_sdk.evaluation import AgentEvaluator
            from trpc_agent_sdk.evaluation import EvalConfig
            from trpc_agent_sdk.evaluation import EvaluationCasesFailed
        except Exception as exc:  # pragma: no cover - depends on optional SDK import health
            raise ValueError(f"sdk mode could not import AgentEvaluator: {exc}") from exc

        target_paths = self._target_prompt_paths()
        candidate_prompts = _validated_prompt_bundle(
            prompts,
            target_paths,
            context=f"cannot evaluate {prompt_id}",
        )
        expected_cases = _load_sdk_expected_cases(dataset_path, split=split)
        artifact_path = Path(artifact_dir)
        artifact_path.mkdir(parents=True, exist_ok=True)
        eval_config_path = artifact_path / "eval_config.json"
        eval_config_path.write_text(
            EvalConfig(
                criteria={"final_response_avg_score": 1.0}
            ).model_dump_json(indent=2),
            encoding="utf-8",
        )
        snapshot = snapshot_prompt_files(target_paths)
        result: Any | None = None
        with temporary_prompt_bundle(snapshot, candidate_prompts):
            executer = AgentEvaluator.get_executer(
                str(dataset_path),
                call_agent=call_agent,
                print_detailed_results=False,
                print_summary_report=False,
                eval_result_output_dir=str(artifact_dir),
                eval_metrics_file_path_or_dir=str(eval_config_path),
            )
            try:
                await executer.evaluate()
            except EvaluationCasesFailed:
                result = executer.get_result()
                if result is None:
                    raise
            else:
                result = executer.get_result()

        if result is None:
            raise ValueError(f"AgentEvaluator returned no result for {dataset_path}")
        return _eval_result_from_sdk_result(
            result,
            prompt_id=prompt_id,
            split=split,
            expected_cases=expected_cases.values(),
        )

    def _load_required_call_agent(self, *, for_evaluation: bool):
        if not self.call_agent_path:
            suffix = " for AgentEvaluator runs" if for_evaluation else (
                ". The callable must be async and compatible with "
                "AgentOptimizer.optimize(call_agent=...). Also configure real model credentials required "
                "by that callable, such as TRPC_AGENT_API_KEY/TRPC_AGENT_BASE_URL/TRPC_AGENT_MODEL_NAME."
            )
            raise ValueError(f"sdk mode requires --sdk-call-agent module:function{suffix}")
        return _load_call_agent(self.call_agent_path)

    def _target_prompt_paths(self) -> dict[str, str | Path]:
        if self.target_prompt_paths:
            return dict(self.target_prompt_paths)
        return {"system_prompt": self.prompt_path}


def _required_system_prompt(prompts: dict[str, str], *, context: str) -> str:
    if "system_prompt" not in prompts:
        raise ValueError(f"{context}: missing required prompt field 'system_prompt'")
    prompt = prompts["system_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"{context}: prompt field 'system_prompt' must be a non-empty string")
    return prompt


def _normalize_fake_candidates(candidates: Iterable[CandidatePrompt]) -> list[CandidatePrompt]:
    normalized: list[CandidatePrompt] = []
    for candidate in candidates:
        bundle = candidate.bundle()
        normalized.append(
            CandidatePrompt(
                candidate_id=candidate.candidate_id,
                prompt=candidate.prompt,
                rationale=candidate.rationale,
                prompt_diff=candidate.prompt_diff,
                prompt_fields=bundle,
            )
        )
    return normalized


def _load_call_agent(path: str):
    if ":" not in path:
        raise ValueError("--sdk-call-agent must use module:function format")
    module_name, function_name = path.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ValueError(f"--sdk-call-agent target {path!r} could not import module {module_name!r}: {exc}") from exc
    call_agent = getattr(module, function_name, None)
    if call_agent is None:
        raise ValueError(f"--sdk-call-agent target {path!r} was not found")
    if not callable(call_agent):
        raise ValueError(f"--sdk-call-agent target {path!r} was found but is not callable")
    return call_agent


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _validated_prompt_bundle(
    prompts: Any,
    paths: dict[str, str | Path],
    *,
    context: str,
) -> dict[str, str]:
    if not isinstance(prompts, dict):
        raise ValueError(f"{context} must be a prompt mapping")
    missing_fields = sorted(name for name in paths if name not in prompts)
    if missing_fields:
        if context == "OptimizeResult.best_prompts":
            raise ValueError(
                "sdk mode completed but OptimizeResult.best_prompts is missing registered target fields: "
                + ", ".join(missing_fields)
            )
        raise ValueError(f"{context} is missing registered target fields: {', '.join(missing_fields)}")
    extra_fields = sorted(name for name in prompts if name not in paths)
    if extra_fields:
        raise ValueError(f"{context} contains unregistered target fields: {', '.join(extra_fields)}")
    empty_fields = sorted(
        name
        for name in paths
        if not isinstance(prompts[name], str) or not prompts[name].strip()
    )
    if empty_fields:
        if context == "OptimizeResult.best_prompts":
            raise ValueError(
                "sdk mode completed but OptimizeResult.best_prompts contained empty registered target fields: "
                + ", ".join(empty_fields)
            )
        raise ValueError(f"{context} contains empty registered target fields: {', '.join(empty_fields)}")
    return {name: prompts[name] for name in paths}


def _read_prompt_bundle(paths: dict[str, str | Path]) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for name, path in paths.items():
        prompt_path = Path(path)
        try:
            prompts[name] = prompt_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"source prompt field {name!r} is not valid UTF-8: {prompt_path}"
            ) from exc
    return prompts


def _prompt_bundle_from_snapshot(snapshot: Any) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for name, prompt_file in snapshot.files.items():
        try:
            prompts[name] = prompt_file.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"source prompt field {name!r} is not valid UTF-8: {prompt_file.path}"
            ) from exc
    return prompts


def _changed_snapshot_files(snapshot: Any) -> list[str]:
    changed: list[str] = []
    for name, prompt_file in snapshot.files.items():
        try:
            current = prompt_file.path.read_bytes()
        except OSError:
            changed.append(name)
        else:
            if current != prompt_file.content:
                changed.append(name)
    return changed


def _round_id(round_record: Any, *, fallback: int) -> int:
    value = getattr(round_record, "round", fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("SDK round id must be a positive integer")
    return value


def _require_successful_optimize_result(result: Any) -> None:
    status = _sdk_result_text(getattr(result, "status", None)).upper()
    if status == "SUCCEEDED":
        return
    raise ValueError(
        "SDK optimization did not succeed: "
        f"status={status}; "
        f"error_message={_sdk_result_text(getattr(result, 'error_message', None))}; "
        f"finish_reason={_sdk_result_text(getattr(result, 'finish_reason', None))}; "
        f"stop_reason={_sdk_result_text(getattr(result, 'stop_reason', None))}"
    )


def _sdk_result_text(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        value = enum_value
    else:
        enum_name = getattr(value, "name", None)
        if enum_name is not None:
            value = enum_name
    return str(value).split(".")[-1]


def _prompt_bundle_key(prompts: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(prompts.items()))


def _candidate_from_bundle(
    *,
    candidate_id: str,
    prompts: dict[str, str],
    rationale: str,
    baseline_prompts: dict[str, str],
) -> CandidatePrompt:
    return CandidatePrompt(
        candidate_id=candidate_id,
        prompt=_render_prompt_bundle(prompts),
        rationale=rationale,
        prompt_diff=_render_prompt_bundle_diff(
            baseline_prompts,
            prompts,
            candidate_id=candidate_id,
        ),
        prompt_fields=dict(prompts),
    )


def _summarize_sdk_result(result: Any) -> dict[str, Any]:
    return {
        "schema_version": _safe_jsonable(getattr(result, "schema_version", None)),
        "algorithm": _safe_jsonable(getattr(result, "algorithm", None)),
        "status": _safe_jsonable(getattr(result, "status", None)),
        "finish_reason": _safe_jsonable(getattr(result, "finish_reason", None)),
        "stop_reason": _safe_jsonable(getattr(result, "stop_reason", None)),
        "error_message": _safe_jsonable(getattr(result, "error_message", None)),
        "baseline_pass_rate": _pass_rate_result_field(
            "baseline_pass_rate",
            getattr(result, "baseline_pass_rate", 0.0),
        ),
        "best_pass_rate": _pass_rate_result_field(
            "best_pass_rate",
            getattr(result, "best_pass_rate", 0.0),
        ),
        "pass_rate_improvement": _finite_result_field(
            "pass_rate_improvement",
            getattr(result, "pass_rate_improvement", 0.0),
        ),
        "baseline_metric_breakdown": _finite_metric_map(
            getattr(result, "baseline_metric_breakdown", {}) or {},
            context="SDK OptimizeResult baseline_metric_breakdown",
        ),
        "best_metric_breakdown": _finite_metric_map(
            getattr(result, "best_metric_breakdown", {}) or {},
            context="SDK OptimizeResult best_metric_breakdown",
        ),
        "metric_thresholds": _finite_metric_map(
            getattr(result, "metric_thresholds", {}) or {},
            context="SDK OptimizeResult metric_thresholds",
        ),
        "per_metric_best_candidates": _safe_jsonable(
            getattr(result, "per_metric_best_candidates", {})
        ),
        "total_llm_cost": _nonnegative_result_field(
            "total_llm_cost",
            getattr(result, "total_llm_cost", 0.0),
        ),
        "total_token_usage": _safe_jsonable(getattr(result, "total_token_usage", {})),
        "duration_seconds": _nonnegative_result_field(
            "duration_seconds",
            getattr(result, "duration_seconds", 0.0),
        ),
        "started_at": _safe_jsonable(getattr(result, "started_at", None)),
        "finished_at": _safe_jsonable(getattr(result, "finished_at", None)),
        "total_rounds": _safe_jsonable(getattr(result, "total_rounds", 0)),
        "baseline_prompts": _safe_jsonable(getattr(result, "baseline_prompts", {})),
        "best_prompts": _safe_jsonable(getattr(result, "best_prompts", {})),
        "rounds": [
            _round_raw_summary(round_record)
            for round_record in getattr(result, "rounds", []) or []
        ],
        "extras": _safe_jsonable(getattr(result, "extras", {})),
    }


def _round_raw_summary(round_record: Any) -> dict[str, Any]:
    serialized = _safe_jsonable(round_record)
    if isinstance(serialized, dict):
        return serialized
    return {
        "round": _safe_jsonable(getattr(round_record, "round", None)),
        "candidate_prompts": _safe_jsonable(getattr(round_record, "candidate_prompts", {})),
        "validation_pass_rate": _safe_jsonable(
            getattr(round_record, "validation_pass_rate", None)
        ),
        "metric_breakdown": _safe_jsonable(getattr(round_record, "metric_breakdown", {})),
        "accepted": _safe_jsonable(getattr(round_record, "accepted", None)),
        "acceptance_reason": _safe_jsonable(
            getattr(round_record, "acceptance_reason", "")
        ),
        "failed_case_ids": _safe_jsonable(getattr(round_record, "failed_case_ids", [])),
        "round_llm_cost": _safe_jsonable(getattr(round_record, "round_llm_cost", 0.0)),
        "duration_seconds": _safe_jsonable(getattr(round_record, "duration_seconds", 0.0)),
    }


def _finite_result_field(field_name: str, value: Any) -> float:
    try:
        return _finite_number(value, context=f"SDK OptimizeResult field {field_name}")
    except ValueError as exc:
        raise ValueError(f"SDK OptimizeResult field {field_name} must be a finite number") from exc


def _nonnegative_result_field(field_name: str, value: Any) -> float:
    number = _finite_result_field(field_name, value)
    if number < 0.0:
        raise ValueError(f"SDK OptimizeResult field {field_name} must be non-negative")
    return number


def _pass_rate_result_field(field_name: str, value: Any) -> float:
    number = _finite_result_field(field_name, value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(
            f"SDK OptimizeResult field {field_name} must be between 0 and 1"
        )
    return number


def _finite_metric_map(value: Any, *, context: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a metric mapping")
    return {
        str(name): _finite_number(score, context=f"{context}.{name}")
        for name, score in value.items()
    }


def _finite_number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{context} must be a finite number")
    return number


def _nonnegative_number(value: Any, *, context: str) -> float:
    number = _finite_number(value, context=context)
    if number < 0.0:
        raise ValueError(f"{context} must be non-negative")
    return number


def _pass_rate_number(value: Any, *, context: str) -> float:
    number = _finite_number(value, context=context)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{context} must be between 0 and 1")
    return number


def _safe_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _safe_jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _safe_jsonable(dict(value.__dict__))
    if isinstance(value, dict):
        return {str(key): _safe_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("SDK result values must be finite")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return repr(value)


def _load_sdk_expected_cases(
    dataset_path: str | Path,
    *,
    split: str,
) -> dict[str, EvalCase]:
    """Load wrapper metadata from an SDK EvalSet without changing the SDK file."""

    payload = read_json(dataset_path)
    if "eval_cases" not in payload:
        if "cases" in payload:
            return _eval_cases_by_id(
                load_eval_cases(dataset_path, split=split),
                context=f"legacy evalset {dataset_path}",
            )
        raise ValueError(f"SDK evalset {dataset_path} must contain an eval_cases list")

    eval_set_id = payload.get("eval_set_id")
    if not isinstance(eval_set_id, str) or not eval_set_id.strip():
        raise ValueError(f"SDK evalset {dataset_path} is missing non-empty eval_set_id")
    raw_cases = payload["eval_cases"]
    if not isinstance(raw_cases, list):
        raise ValueError(f"SDK evalset {dataset_path} eval_cases must be a list")
    if not raw_cases:
        raise ValueError(f"SDK evalset {dataset_path} eval_cases must not be empty")

    expected_cases: dict[str, EvalCase] = {}
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(
                f"SDK evalset {dataset_path} eval_cases[{index}] must be an object"
            )
        eval_id = raw_case.get("eval_id")
        if not isinstance(eval_id, str) or not eval_id.strip():
            raise ValueError(
                f"SDK evalset {dataset_path} eval_cases[{index}] is missing non-empty eval_id"
            )
        if eval_id in expected_cases:
            raise ValueError(f"SDK evalset {dataset_path} contains duplicate eval_id {eval_id!r}")
        expected_cases[eval_id] = _expected_case_from_sdk_case(
            raw_case,
            eval_id=eval_id,
            split=split,
            dataset_path=dataset_path,
        )
    return expected_cases


def _expected_case_from_sdk_case(
    raw_case: dict[str, Any],
    *,
    eval_id: str,
    split: str,
    dataset_path: str | Path,
) -> EvalCase:
    context = f"SDK evalset {dataset_path} case {eval_id!r}"
    conversation = raw_case.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        raise ValueError(f"{context} must contain a non-empty conversation list")

    input_text = ""
    for turn_index, invocation in reversed(list(enumerate(conversation))):
        if not isinstance(invocation, dict):
            raise ValueError(f"{context} conversation[{turn_index}] must be an object")
        user_content = invocation.get("user_content")
        if user_content is None:
            continue
        if not isinstance(user_content, dict):
            raise ValueError(
                f"{context} conversation[{turn_index}].user_content must be an object"
            )
        candidate_text = _content_text(user_content)
        if candidate_text.strip():
            input_text = candidate_text
            break
    if not input_text:
        raise ValueError(f"{context} conversation has no user_content text")

    session_input = raw_case.get("session_input")
    if not isinstance(session_input, dict):
        raise ValueError(f"{context} must contain a session_input object")
    state = session_input.get("state")
    if not isinstance(state, dict):
        raise ValueError(f"{context} session_input.state must be an object")
    expectation = state.get("eval_optimize_expectation")
    if not isinstance(expectation, dict):
        raise ValueError(
            f"{context} session_input.state must contain eval_optimize_expectation object"
        )

    tags = state.get("eval_optimize_tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError(f"{context} eval_optimize_tags must be a list of strings")
    protected = state.get("eval_optimize_protected", False)
    if not isinstance(protected, bool):
        raise ValueError(f"{context} eval_optimize_protected must be a boolean")

    expected_failure_category = state.get("eval_optimize_expected_failure_category")
    if expected_failure_category is None:
        expected_failure_category = state.get("expected_failure_category")
    if expected_failure_category is None:
        expected_failure_category = expectation.get("expected_failure_category")
    if (
        expected_failure_category is not None
        and (
            not isinstance(expected_failure_category, str)
            or not expected_failure_category.strip()
        )
    ):
        raise ValueError(f"{context} expected_failure_category must be a non-empty string")

    return EvalCase(
        case_id=eval_id,
        split=split,
        input=input_text,
        expectation=dict(expectation),
        tags=list(tags),
        protected=protected,
        expected_failure_category=expected_failure_category,
    )


def _eval_cases_by_id(
    cases: Iterable[EvalCase],
    *,
    context: str,
) -> dict[str, EvalCase]:
    by_id: dict[str, EvalCase] = {}
    for case in cases:
        if case.case_id in by_id:
            raise ValueError(f"{context} contains duplicate case id {case.case_id!r}")
        by_id[case.case_id] = case
    return by_id


def _eval_result_from_sdk_result(
    result: Any,
    *,
    prompt_id: str,
    split: str,
    expected_cases: Iterable[EvalCase],
) -> EvalResult:
    expected_by_id: dict[str, EvalCase] = {}
    expected_order: list[str] = []
    for expected_case in expected_cases:
        case_id = str(expected_case.case_id)
        if case_id in expected_by_id:
            raise ValueError(f"expected cases contain duplicate case id: {case_id}")
        expected_by_id[case_id] = expected_case
        expected_order.append(case_id)

    sdk_runs_by_id: dict[str, list[Any]] = {}
    results_by_eval_set_id = getattr(result, "results_by_eval_set_id", {}) or {}
    if not results_by_eval_set_id:
        raise ValueError("SDK EvaluateResult contains no eval set results")
    for raw_eval_set_id, set_result in results_by_eval_set_id.items():
        eval_set_id = str(raw_eval_set_id)
        num_runs = _optional_num_runs(set_result, eval_set_id=eval_set_id)
        eval_results_by_eval_id = getattr(set_result, "eval_results_by_eval_id", {}) or {}
        for raw_eval_id, runs in eval_results_by_eval_id.items():
            eval_id = str(raw_eval_id)
            if eval_id in sdk_runs_by_id:
                raise ValueError(f"SDK evaluation result contains duplicate case id: {eval_id}")
            run_list = list(runs or [])
            if num_runs is not None and len(run_list) != num_runs:
                raise ValueError(
                    f"SDK eval set {eval_set_id!r} declares num_runs={num_runs}, "
                    f"but case {eval_id!r} contains {len(run_list)} runs"
                )
            _validate_sdk_run_ids(
                run_list,
                eval_set_id=eval_set_id,
                eval_id=eval_id,
                num_runs=num_runs,
            )
            sdk_runs_by_id[eval_id] = run_list

    expected_ids = set(expected_by_id)
    sdk_ids = set(sdk_runs_by_id)
    if expected_ids != sdk_ids:
        details: list[str] = []
        missing = sorted(expected_ids - sdk_ids)
        extra = sorted(sdk_ids - expected_ids)
        if missing:
            details.append("missing SDK result IDs: " + ", ".join(missing))
        if extra:
            details.append("extra SDK result IDs: " + ", ".join(extra))
        raise ValueError("SDK evaluation case IDs do not match expected cases; " + "; ".join(details))

    case_results: list[CaseResult] = []
    for case_id in expected_order:
        expected_case = expected_by_id[case_id]
        run_list = sdk_runs_by_id[case_id]
        metrics = _aggregate_case_metrics(run_list, case_id=case_id)
        if metrics:
            score = _mean(list(metrics.values()))
        else:
            score = _mean([
                1.0 if _status_passed(getattr(run, "final_eval_status", None)) else 0.0
                for run in run_list
            ])
        score = round(score, 6)
        passed = bool(run_list) and all(
            _status_passed(getattr(run, "final_eval_status", None))
            for run in run_list
        )
        failure_reason, evidence, failure_category = _failure_details(run_list)
        actual_invocation = _last_actual_invocation(run_list)
        trace_available = actual_invocation is not None
        trace_payload = (
            {
                "user_content": _safe_jsonable(
                    getattr(actual_invocation, "user_content", None)
                ),
                "final_response": _safe_jsonable(
                    getattr(actual_invocation, "final_response", None)
                ),
                "intermediate_data": _safe_jsonable(
                    getattr(actual_invocation, "intermediate_data", None)
                ),
            }
            if actual_invocation is not None
            else {}
        )
        output = (
            _content_text(getattr(actual_invocation, "final_response", None))
            if actual_invocation is not None
            else ""
        )
        case_results.append(
            CaseResult(
                case_id=case_id,
                split=split,
                score=score,
                passed=passed,
                output=output,
                metrics=metrics,
                trace=trace_payload,
                trace_available=trace_available,
                failure_category=None if passed else failure_category,
                failure_reason=None if passed else failure_reason,
                evidence=None if passed else evidence,
                cost=0.0,
                hard_failed=(not passed and score <= 0.0),
                expected_failure_category=expected_case.expected_failure_category,
            )
        )

    aggregate_score = (
        round(_mean([case.score for case in case_results]), 6)
        if case_results
        else 0.0
    )
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=aggregate_score,
        passed=all(case.passed for case in case_results),
        cost=0.0,
        cases=case_results,
    )


def _optional_num_runs(set_result: Any, *, eval_set_id: str) -> int | None:
    if not hasattr(set_result, "num_runs"):
        return None
    num_runs = getattr(set_result, "num_runs")
    if isinstance(num_runs, bool) or not isinstance(num_runs, int) or num_runs <= 0:
        raise ValueError(
            f"SDK eval set {eval_set_id!r} num_runs must be a positive integer"
        )
    return num_runs


def _validate_sdk_run_ids(
    runs: list[Any],
    *,
    eval_set_id: str,
    eval_id: str,
    num_runs: int | None,
) -> None:
    seen_run_ids: set[int] = set()
    for run in runs:
        internal_eval_id = getattr(run, "eval_id", None)
        if internal_eval_id not in (None, "") and str(internal_eval_id) != eval_id:
            raise ValueError(
                f"SDK run internal eval_id {internal_eval_id!r} does not match "
                f"container case id {eval_id!r}"
            )
        internal_eval_set_id = getattr(run, "eval_set_id", None)
        if (
            internal_eval_set_id not in (None, "")
            and str(internal_eval_set_id) != eval_set_id
        ):
            raise ValueError(
                f"SDK run internal eval_set_id {internal_eval_set_id!r} does not match "
                f"container eval set id {eval_set_id!r}"
            )

        run_id = getattr(run, "run_id", None)
        if run_id is None:
            continue
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise ValueError(
                f"SDK case {eval_id!r} run_id must be a positive integer or None"
            )
        if num_runs is not None and run_id > num_runs:
            raise ValueError(
                f"SDK case {eval_id!r} run_id {run_id} exceeds num_runs={num_runs}"
            )
        if run_id in seen_run_ids:
            raise ValueError(f"SDK evaluation result contains duplicate run_id {run_id} for case {eval_id}")
        seen_run_ids.add(run_id)


def _aggregate_case_metrics(runs: list[Any], *, case_id: str) -> dict[str, float]:
    scores_by_metric: dict[str, list[float]] = {}
    for run in runs:
        for metric in getattr(run, "overall_eval_metric_results", []) or []:
            raw_score = getattr(metric, "score", None)
            if raw_score is None:
                continue
            metric_name = str(getattr(metric, "metric_name", "") or "")
            if not metric_name:
                raise ValueError(f"SDK case {case_id} contains a scored metric without a name")
            score = _finite_number(
                raw_score,
                context=f"SDK case {case_id} metric {metric_name} score",
            )
            scores_by_metric.setdefault(metric_name, []).append(score)
    return {
        metric_name: round(_mean(scores), 6)
        for metric_name, scores in scores_by_metric.items()
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _status_passed(status: Any) -> bool:
    name = getattr(status, "name", None)
    if name:
        return str(name).upper() == "PASSED"
    return str(status).split(".")[-1].upper() == "PASSED"


def _failure_details(runs: list[Any]) -> tuple[str, str, str]:
    for run in runs:
        if _status_passed(getattr(run, "final_eval_status", None)):
            continue
        error_message = getattr(run, "error_message", None)
        for metric in getattr(run, "overall_eval_metric_results", []) or []:
            if _status_passed(getattr(metric, "eval_status", None)):
                continue
            details = getattr(metric, "details", None)
            reason = getattr(details, "reason", None) if details is not None else None
            metric_name = str(getattr(metric, "metric_name", "") or "")
            score = getattr(metric, "score", None)
            evidence = f"{metric_name} score={score}" if metric_name else f"score={score}"
            return (
                str(reason or error_message or "evaluation metric failed"),
                evidence,
                _metric_failure_category(metric_name),
            )
        if error_message:
            return (str(error_message), str(error_message), "evaluation_error")
    return ("evaluation failed", "no failed metric detail available", "unknown_failure")


def _metric_failure_category(metric_name: str) -> str:
    lowered = metric_name.lower()
    if "parameter" in lowered or "arg" in lowered:
        return "parameter_error"
    if "tool" in lowered:
        return "tool_call_error"
    if "knowledge" in lowered or "recall" in lowered:
        return "knowledge_recall_insufficient"
    if "rubric" in lowered or "judge" in lowered or "llm" in lowered:
        return "llm_rubric_not_met"
    if "format" in lowered:
        return "format_violation"
    if "response" in lowered or "match" in lowered or "exact" in lowered:
        return "final_response_mismatch"
    return "unknown_failure"


def _last_actual_invocation(runs: list[Any]) -> Any | None:
    for run in reversed(runs):
        invocation_results = getattr(run, "eval_metric_result_per_invocation", []) or []
        for invocation_result in reversed(invocation_results):
            actual = getattr(invocation_result, "actual_invocation", None)
            if actual is not None:
                return actual
    return None


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if hasattr(content, "model_dump"):
        content = content.model_dump(mode="json")
    if not isinstance(content, dict):
        return ""
    texts = []
    for part in content.get("parts") or []:
        if isinstance(part, dict) and part.get("text") is not None:
            texts.append(str(part["text"]))
    return "\n".join(texts)


def _render_prompt_bundle(prompts: dict[str, str]) -> str:
    if set(prompts) == {"system_prompt"}:
        return prompts["system_prompt"]
    sections = []
    for name in sorted(prompts):
        sections.append(f"## {name}\n\n{prompts[name]}")
    return "\n\n".join(sections)


def _render_prompt_bundle_diff(
    baseline_prompts: dict[str, str],
    candidate_prompts: dict[str, str],
    *,
    candidate_id: str,
) -> str:
    if set(baseline_prompts) == {"system_prompt"} and set(candidate_prompts) == {"system_prompt"}:
        return make_unified_diff(
            baseline_prompts.get("system_prompt", ""),
            candidate_prompts.get("system_prompt", ""),
            before_name="baseline_system_prompt.txt",
            after_name=f"{candidate_id}/system_prompt.txt",
        )
    diffs = []
    for name in sorted(set(baseline_prompts) | set(candidate_prompts)):
        diffs.append(
            make_unified_diff(
                baseline_prompts.get(name, ""),
                candidate_prompts.get(name, ""),
                before_name=f"baseline/{name}.txt",
                after_name=f"{candidate_id}/{name}.txt",
            )
        )
    return "\n\n".join(diffs)
