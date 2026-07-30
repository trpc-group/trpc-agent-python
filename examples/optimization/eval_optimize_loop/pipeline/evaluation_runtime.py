"""Side-effecting evaluation stage runner around the pure normalizer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Optional

from trpc_agent_sdk.evaluation import EvalConfig, EvalSet

from .artifacts import AuditSink
from .configuration import ValidatedRunConfig
from .contracts import EvaluationBackend
from .costing import CostLedger
from .evaluation import normalize_result
from .models import EvaluationSnapshot, Phase, SnapshotPair, Split


def create_evaluation_runtime(
    *,
    validated: ValidatedRunConfig,
    backend: EvaluationBackend,
    custom_backend: bool,
    sink: AuditSink,
    ledger: CostLedger,
) -> "EvaluationRuntime":
    """Construct accounting policy once at the composition boundary."""

    settings = validated.config.pipeline
    offline = not custom_backend and settings.mode in {"fake", "trace"}
    calls_per_invocation = (1 if not custom_backend and settings.mode == "fake" else
                            0 if not custom_backend and settings.mode == "trace" else None)
    return EvaluationRuntime(
        backend=backend,
        sink=sink,
        ledger=ledger,
        eval_config=validated.config.evaluate,
        cost_usd=0 if offline else None,
        model_calls_per_invocation=calls_per_invocation,
        metric_weights=settings.metric_weights,
        train_case_weights=settings.train_case_weights,
        validation_case_weights=settings.validation_case_weights,
    )


@dataclass
class EvaluationRuntime:
    """Own backend invocation, completed-call accounting and snapshot persistence."""

    backend: EvaluationBackend
    sink: AuditSink
    ledger: CostLedger
    eval_config: EvalConfig
    cost_usd: float | None
    model_calls_per_invocation: int | None
    metric_weights: dict[str, float]
    train_case_weights: dict[str, float]
    validation_case_weights: dict[str, float]

    async def evaluate(
        self,
        *,
        eval_set: EvalSet,
        prompts: dict[str, str],
        split: Split,
        phase: Phase,
    ) -> EvaluationSnapshot:
        stage = f"{phase.value}_{split.value}"
        phase_dir = self.sink.phase_dir(stage)
        metric_count = len(self.eval_config.get_eval_metrics())
        invocations = sum(len(case.conversation or case.actual_conversation or []) for case in eval_set.eval_cases)
        metric_calls = invocations * self.eval_config.num_runs * metric_count
        try:
            raw = await self.backend.evaluate(
                eval_set=deepcopy(eval_set),
                eval_config=deepcopy(self.eval_config),
                prompts=deepcopy(prompts),
                split=split,
                phase=phase,
                audit_dir=str(phase_dir),
            )
        except BaseException:
            self.ledger.record(
                stage,
                cost_usd=self.cost_usd,
                model_calls=(0 if self.model_calls_per_invocation == 0 else None),
                metric_calls=None,
            )
            raise
        model_calls = None
        if self.model_calls_per_invocation is not None:
            model_calls = invocations * self.eval_config.num_runs * self.model_calls_per_invocation
        self.ledger.record(
            stage,
            cost_usd=self.cost_usd,
            model_calls=model_calls,
            metric_calls=metric_calls,
        )
        snapshot = normalize_result(
            raw,
            eval_set,
            self.eval_config,
            split=split,
            phase=phase,
            metric_weights=self.metric_weights,
            case_weights=(self.train_case_weights if split == Split.TRAIN else self.validation_case_weights),
        )
        self.sink.write_json(
            f"{stage}/snapshot.json",
            snapshot.model_dump(mode="json", by_alias=True),
        )
        return snapshot

    async def evaluate_phase(
        self,
        *,
        train: EvalSet,
        validation: EvalSet,
        prompts: dict[str, str],
        phase: Phase,
        on_stage: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[Phase, SnapshotPair], None]] = None,
    ) -> SnapshotPair:
        """Evaluate train then validation for one phase in the fixed order."""

        snapshots = {}
        for split, eval_set in (
            (Split.TRAIN, train),
            (Split.VALIDATION, validation),
        ):
            if on_stage is not None:
                on_stage(f"{phase.value}_{split.value}")
            snapshots[split] = await self.evaluate(
                eval_set=eval_set,
                prompts=prompts,
                split=split,
                phase=phase,
            )
            progress = SnapshotPair(
                train=snapshots.get(Split.TRAIN),
                validation=snapshots.get(Split.VALIDATION),
            )
            if on_progress is not None:
                on_progress(phase, progress)
        return progress
