"""Backend adapters for fake and SDK optimization paths."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable

from .diffing import make_unified_diff
from .evaluator import ExampleEvaluator
from .fake_judge import FakeJudge
from .fake_model import FakeModel
from .optimizer import FakeOptimizer
from .schemas import CandidatePrompt
from .schemas import EvalCase
from .schemas import EvalResult


@dataclass
class FakeBackend:
    seed: int = 91
    trace_enabled: bool = False

    def __post_init__(self) -> None:
        self._evaluator = ExampleEvaluator(FakeModel(seed=self.seed), FakeJudge(), trace_enabled=self.trace_enabled)
        self._optimizer = FakeOptimizer()

    def evaluate(self, *, prompt_id: str, prompt: str, cases: Iterable[EvalCase], split: str) -> EvalResult:
        return self._evaluator.evaluate(prompt_id=prompt_id, prompt=prompt, cases=cases, split=split)

    def optimize(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
        return self._optimizer.propose(baseline_prompt)


@dataclass
class SDKBackend:
    """Thin optimizer adapter around AgentOptimizer/TargetPrompt for SDK runs.

    SDK mode relies on AgentOptimizer's internal evaluation loop. It does not
    implement the fake per-case ``evaluate`` API.
    """

    prompt_path: str | Path
    call_agent_path: str | None = None
    update_source: bool = False
    last_result: Any | None = None
    last_result_summary: dict[str, Any] | None = None
    last_artifact_dir: str | None = None

    def optimize(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
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
        if not self.call_agent_path:
            raise ValueError(
                "sdk mode requires --sdk-call-agent module:function. The callable must be async and compatible "
                "with AgentOptimizer.optimize(call_agent=...). Also configure real model credentials required "
                "by that callable, such as TRPC_AGENT_API_KEY/TRPC_AGENT_BASE_URL/TRPC_AGENT_MODEL_NAME."
            )
        call_agent = _load_call_agent(self.call_agent_path)
        try:
            from trpc_agent_sdk.evaluation import AgentOptimizer
            from trpc_agent_sdk.evaluation import TargetPrompt
        except Exception as exc:  # pragma: no cover - depends on optional SDK import health
            raise ValueError(f"sdk mode could not import AgentOptimizer/TargetPrompt: {exc}") from exc

        target_prompt = TargetPrompt().add_path("system_prompt", str(self.prompt_path))
        result = await AgentOptimizer.optimize(
            config_path=str(optimizer_config_path),
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(output_dir),
            update_source=self.update_source,
            verbose=0,
        )
        best_prompt = getattr(result, "best_prompts", {}).get("system_prompt")
        if not best_prompt:
            raise ValueError("sdk mode completed but OptimizeResult.best_prompts['system_prompt'] was missing")
        self.last_result = result
        self.last_result_summary = _summarize_sdk_result(result)
        self.last_artifact_dir = str(output_dir)
        return [
            CandidatePrompt(
                candidate_id="sdk_best",
                prompt=best_prompt,
                rationale="Best prompt returned by AgentOptimizer.optimize.",
                prompt_diff=make_unified_diff(
                    baseline_prompt,
                    best_prompt,
                    before_name="baseline_system_prompt.txt",
                    after_name="sdk_best/system_prompt.txt",
                ),
            )
        ]


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
    return call_agent


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _summarize_sdk_result(result: Any) -> dict[str, Any]:
    return {
        "status": _safe_jsonable(getattr(result, "status", None)),
        "baseline_pass_rate": _safe_jsonable(getattr(result, "baseline_pass_rate", None)),
        "best_pass_rate": _safe_jsonable(getattr(result, "best_pass_rate", None)),
        "pass_rate_improvement": _safe_jsonable(getattr(result, "pass_rate_improvement", None)),
        "baseline_metric_breakdown": _safe_jsonable(getattr(result, "baseline_metric_breakdown", {})),
        "best_metric_breakdown": _safe_jsonable(getattr(result, "best_metric_breakdown", {})),
        "metric_thresholds": _safe_jsonable(getattr(result, "metric_thresholds", {})),
        "total_llm_cost": _safe_jsonable(getattr(result, "total_llm_cost", 0.0)),
        "total_token_usage": _safe_jsonable(getattr(result, "total_token_usage", {})),
        "duration_seconds": _safe_jsonable(getattr(result, "duration_seconds", 0.0)),
        "total_rounds": _safe_jsonable(getattr(result, "total_rounds", 0)),
        "rounds": [
            {
                "validation_pass_rate": _safe_jsonable(getattr(round_record, "validation_pass_rate", None)),
                "accepted": _safe_jsonable(getattr(round_record, "accepted", None)),
                "failed_case_ids": _safe_jsonable(getattr(round_record, "failed_case_ids", [])),
                "round_llm_cost": _safe_jsonable(getattr(round_record, "round_llm_cost", 0.0)),
                "budget_used": _safe_jsonable(getattr(round_record, "budget_used", None)),
                "budget_total": _safe_jsonable(getattr(round_record, "budget_total", None)),
            }
            for round_record in getattr(result, "rounds", []) or []
        ],
    }


def _safe_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _safe_jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _safe_jsonable(dict(value.__dict__))
    if isinstance(value, dict):
        return {str(key): _safe_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
