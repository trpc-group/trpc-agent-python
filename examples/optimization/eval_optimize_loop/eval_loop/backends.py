"""Backend adapters for fake and SDK optimization paths."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Protocol

from .evaluator import ExampleEvaluator
from .fake_judge import FakeJudge
from .fake_model import FakeModel
from .optimizer import FakeOptimizer
from .schemas import CandidatePrompt
from .schemas import EvalCase
from .schemas import EvalResult


class EvalOptimizeBackend(Protocol):
    def evaluate(self, *, prompt_id: str, prompt: str, cases: Iterable[EvalCase], split: str) -> EvalResult:
        ...

    def optimize(
        self,
        *,
        baseline_prompt: str,
        train_path: str | Path,
        val_path: str | Path,
        optimizer_config_path: str | Path,
        output_dir: str | Path,
    ) -> list[CandidatePrompt]:
        ...


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
    """Thin adapter around AgentOptimizer/TargetPrompt for real SDK runs."""

    prompt_path: str | Path
    call_agent_path: str | None = None
    update_source: bool = False

    def evaluate(self, *, prompt_id: str, prompt: str, cases: Iterable[EvalCase], split: str) -> EvalResult:
        raise ValueError(
            "sdk mode evaluation expects SDK evalset files and is performed by AgentOptimizer/AgentEvaluator. "
            "Use --sdk-call-agent module:function and SDK-compatible train/val evalsets; fake EvalCase objects "
            "cannot be evaluated by the SDK adapter."
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
        if not self.call_agent_path:
            raise ValueError(
                "sdk mode requires --sdk-call-agent module:function. The callable must be async and compatible "
                "with AgentOptimizer.optimize(call_agent=...). Also configure real model credentials required "
                "by that callable, such as TRPC_AGENT_API_KEY/TRPC_AGENT_BASE_URL/TRPC_AGENT_MODEL_NAME."
            )
        call_agent = _load_call_agent(self.call_agent_path)
        try:
            from trpc_agent_sdk.evaluation import AgentEvaluator
            from trpc_agent_sdk.evaluation import AgentOptimizer
            from trpc_agent_sdk.evaluation import TargetPrompt
        except Exception as exc:  # pragma: no cover - depends on optional SDK import health
            raise ValueError(f"sdk mode could not import AgentEvaluator/AgentOptimizer/TargetPrompt: {exc}") from exc

        _ = AgentEvaluator
        target_prompt = TargetPrompt().add_path("system_prompt", str(self.prompt_path))
        result = asyncio.run(
            AgentOptimizer.optimize(
                config_path=str(optimizer_config_path),
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=str(train_path),
                validation_dataset_path=str(val_path),
                output_dir=str(output_dir),
                update_source=self.update_source,
                verbose=0,
            )
        )
        best_prompt = getattr(result, "best_prompts", {}).get("system_prompt")
        if not best_prompt:
            raise ValueError("sdk mode completed but OptimizeResult.best_prompts['system_prompt'] was missing")
        return [
            CandidatePrompt(
                candidate_id="sdk_best",
                prompt=best_prompt,
                rationale="Best prompt returned by AgentOptimizer.optimize.",
                prompt_diff=_simple_diff(baseline_prompt, best_prompt),
            )
        ]


def _load_call_agent(path: str):
    if ":" not in path:
        raise ValueError("--sdk-call-agent must use module:function format")
    module_name, function_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    call_agent = getattr(module, function_name, None)
    if call_agent is None:
        raise ValueError(f"--sdk-call-agent target {path!r} was not found")
    return call_agent


def _simple_diff(before: str, after: str) -> str:
    if before == after:
        return "# no prompt changes"
    return "- baseline system_prompt\n+ optimized system_prompt"
