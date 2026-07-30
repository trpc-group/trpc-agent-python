"""The two deliberate substitution boundaries used by the pipeline."""

from __future__ import annotations

from typing import Protocol

from trpc_agent_sdk.evaluation import EvalConfig, EvalSet, EvaluateResult, TargetPrompt

from .models import AttributionSnapshot, CandidateProposal, Phase, Split


class EvaluationBackend(Protocol):
    """Evaluate an immutable request and return the SDK's raw result object."""

    async def evaluate(  # noqa: E704
        self,
        *,
        eval_set: EvalSet,
        eval_config: EvalConfig,
        prompts: dict[str, str],
        split: Split,
        phase: Phase,
        audit_dir: str,
    ) -> EvaluateResult:
        ...


class CandidateGenerator(Protocol):
    """Propose prompts from inner-training evidence without making the gate decision."""

    async def generate(  # noqa: E704
        self,
        *,
        target_prompt: TargetPrompt,
        baseline_prompts: dict[str, str],
        train_attribution: AttributionSnapshot,
        inner_train_path: str,
        inner_selection_path: str,
        config_path: str,
        output_dir: str,
    ) -> CandidateProposal:
        ...
