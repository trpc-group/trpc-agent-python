# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optimizer configuration schema.

Each registered algorithm contributes a pydantic model under
``OptimizeConfig.algorithm``; field names mirror the upstream library
(e.g. https://github.com/gepa-ai/gepa) 1:1 so users can cross-reference
upstream docs without translating.

The top-level ``optimize`` section only carries algorithm-agnostic
switches (e.g. evaluator parallelism, framework stop policies); any
switch whose effect depends on the selected algorithm lives inside the
algorithm block.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional
from typing import Union

from pydantic import Field
from pydantic import model_validator

from ._common import EvalBaseModel
from ._eval_config import EvalConfig
from ._optimize_model_options import OptimizeModelOptions


class GepaReflectiveAlgo(EvalBaseModel):
    """gepa_reflective algorithm configuration.

    Field names mirror ``gepa.optimize`` parameters and gepa
    ``StopperProtocol`` constructor arguments so config maps to gepa
    docs directly.
    """

    name: Literal["gepa_reflective"] = Field(description="Algorithm discriminator tag.", )

    seed: int = Field(
        default=42,
        description="Random seed forwarded to gepa.optimize(seed=...).",
    )
    reflection_lm: OptimizeModelOptions = Field(
        description=("LLM gepa uses to reflect on failed cases and propose new prompts. "
                     "Forwarded to gepa.optimize(reflection_lm=...)."), )

    candidate_selection_strategy: Literal[
        "pareto",
        "current_best",
        "epsilon_greedy",
        "top_k_pareto",
    ] = Field(
        default="pareto",
        description="Strategy gepa uses to pick the parent candidate each round.",
    )
    module_selector: str = Field(
        default="round_robin",
        description="Component selector passed to gepa (e.g. 'round_robin', 'all').",
    )
    frontier_type: Literal["instance", "objective", "hybrid", "cartesian"] = Field(
        default="instance",
        description="Pareto frontier tracking granularity forwarded to gepa.",
    )
    reflection_minibatch_size: Optional[int] = Field(
        default=None,
        description="Per-round minibatch size for the reflective dataset; None lets gepa decide.",
    )
    reflection_history_top_k: int = Field(
        default=2,
        ge=0,
        le=5,
        description=("How many historical best traces per case to expose to the "
                     "reflection LM as the ``history_top_k`` record field. 0 "
                     "disables the feature. Capped at 5 to bound prompt-token "
                     "growth — for K=2 a typical multi-turn case grows ~30%."),
    )
    perfect_score: float = Field(
        default=1.0,
        description="Score considered 'perfect' for skip_perfect_score decisions.",
    )
    skip_perfect_score: bool = Field(
        default=True,
        description="Whether gepa skips optimizing instances that already score perfect.",
    )

    use_merge: bool = Field(
        default=False,
        description="Whether to enable gepa merge-based candidate proposals.",
    )
    max_merge_invocations: int = Field(
        default=5,
        description="Maximum merge invocations when use_merge is true.",
    )
    merge_val_overlap_floor: int = Field(
        default=5,
        description="Minimum shared validation ids required before attempting a merge subsample.",
    )

    cache_evaluation: bool = Field(
        default=False,
        description="Cache (candidate, case) scores so repeated evaluations skip the metric call.",
    )
    track_best_outputs: bool = Field(
        default=False,
        description="Track per-case best outputs alongside the best candidate.",
    )

    max_metric_calls: Optional[int] = Field(
        default=None,
        description=("Stop after this many metric calls (one metric call = one case-level "
                     "evaluation). Mapped to gepa MaxMetricCallsStopper. At least one of the "
                     "five stop conditions on this object must be set."),
    )
    max_iterations_without_improvement: Optional[int] = Field(
        default=None,
        description=("Stop after this many consecutive iterations whose best valset score "
                     "did not improve. Mapped to gepa NoImprovementStopper."),
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        description=("Stop after this many wall-clock seconds. Mapped to gepa "
                     "TimeoutStopCondition."),
    )
    score_threshold: Optional[float] = Field(
        default=None,
        description=("Stop once the best valset score reaches this threshold. Mapped to "
                     "gepa ScoreThresholdStopper."),
    )
    max_candidate_proposals: Optional[int] = Field(
        default=None,
        description=("Stop after this many candidate proposals. Mapped to gepa "
                     "MaxCandidateProposalsStopper."),
    )
    max_tracked_candidates: Optional[int] = Field(
        default=None,
        description=("Stop once the candidate pool reaches this size. Mapped to gepa "
                     "MaxTrackedCandidatesStopper."),
    )

    @model_validator(mode="after")
    def _require_at_least_one_stop_condition(self) -> "GepaReflectiveAlgo":
        if not any(value is not None for value in (
                self.max_metric_calls,
                self.max_iterations_without_improvement,
                self.timeout_seconds,
                self.score_threshold,
                self.max_candidate_proposals,
                self.max_tracked_candidates,
        )):
            raise ValueError("gepa_reflective requires at least one stop condition: set one of "
                             "max_metric_calls / max_iterations_without_improvement / "
                             "timeout_seconds / score_threshold / max_candidate_proposals / "
                             "max_tracked_candidates.")
        return self


class FrameworkStopConfig(EvalBaseModel):
    """Framework-level stop policies applied to every algorithm.

    Today the only such policy is metric-based early stopping: stop
    when every metric named by ``required_metrics`` meets its threshold
    on the validation set. Threshold values come from
    ``evaluate.metrics[].threshold``; this section only decides which
    metrics participate.

    Pass-rate-based stopping is not exposed here because every supported
    engine has an equivalent native field (e.g. ``algorithm.score_threshold``
    for gepa_reflective).

    Field values for ``required_metrics``:
      - ``"all"`` (default): every metric in ``evaluate.metrics[]``
        must meet its threshold.
      - ``list[str]``: only the listed metrics must meet thresholds.
        Each name must match an entry in
        ``evaluate.metrics[].metric_name`` (validated by
        :class:`OptimizeConfigFile`). Empty list disables the policy.
      - ``None``: disable the policy entirely; the run finishes only
        via algorithm-native stop conditions.
    """

    required_metrics: Optional[Union[Literal["all"], list[str]]] = Field(
        default="all",
        description=("Metrics whose thresholds must be met on the validation set "
                     "before the framework asks the algorithm to stop. 'all' means "
                     "every metric in evaluate.metrics[]; a list narrows the set; "
                     "None or [] disables the policy."),
    )


class OptimizeConfig(EvalBaseModel):
    """Algorithm-agnostic optimizer section.

    Holds switches the framework itself consumes; algorithm-specific
    knobs live under :attr:`algorithm` so different algorithms can
    expose entirely different field sets without polluting one another.

    To add a second algorithm:
        1. Define ``MyAlgo(EvalBaseModel)`` with ``name: Literal["my_algo"]``.
        2. Replace :attr:`algorithm` type with::

            algorithm: Annotated[
                Union[GepaReflectiveAlgo, MyAlgo],
                Field(discriminator="name"),
            ]

           pydantic v2 then routes validation by the ``name`` tag and
           rejects unknown algorithm names with a clear error.
    """

    eval_case_parallelism: int = Field(
        default=4,
        description="Case-level parallelism forwarded to the evaluator.",
    )
    stop: FrameworkStopConfig = Field(
        default_factory=FrameworkStopConfig,
        description=("Framework-level stop policies; OR'd with any algorithm-native "
                     "stop conditions configured under :attr:`algorithm`."),
    )
    algorithm: GepaReflectiveAlgo = Field(description="Algorithm selection and algorithm-specific parameters.", )


class OptimizeConfigFile(EvalBaseModel):
    """Top-level schema for an optimizer JSON config file."""

    evaluate: EvalConfig = Field(description="Evaluator section: same schema as evaluator's EvalConfig.", )
    optimize: OptimizeConfig = Field(description="Optimizer section: framework switches plus the algorithm block.", )

    @model_validator(mode="after")
    def _validate_required_metrics_against_evaluate(self) -> "OptimizeConfigFile":
        required = self.optimize.stop.required_metrics
        if not isinstance(required, list) or not required:
            return self
        available = {metric.metric_name for metric in self.evaluate.get_eval_metrics()}
        unknown = [name for name in required if name not in available]
        if unknown:
            raise ValueError("stop.required_metrics references unknown metric(s) "
                             f"{unknown}; available metrics from evaluate.metrics[]: "
                             f"{sorted(available)}")
        return self


def load_optimize_config(path: str) -> OptimizeConfigFile:
    """Load and parse an optimizer JSON config file.

    Accepts camelCase and snake_case keys.

    Raises:
        FileNotFoundError: if path does not exist.
        pydantic.ValidationError: on schema violations.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return OptimizeConfigFile.model_validate_json(content)
