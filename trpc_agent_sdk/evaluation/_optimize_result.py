# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optimization result data structures."""

from __future__ import annotations

import os
from typing import Any
from typing import Literal
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel

RunStatus = Literal["SUCCEEDED", "FAILED", "CANCELED"]

FinishReason = Literal[
    "completed",
    "perfect_pass_rate",
    "no_improvement",
    "error",
]

StopReason = Literal[
    "required_metrics_passing",
    "budget_exhausted",
    "no_improvement",
    "timeout",
    "score_threshold",
    "max_candidate_proposals",
    "max_tracked_candidates",
    "user_requested_stop",
    "completed",
]

RoundKind = Literal["reflective", "merge"]


class RoundRecord(EvalBaseModel):
    """Per-round optimization record.

    Attributes:
        round: 1-based round index.
        optimized_field_names: Field names actually rewritten by the optimize model this round.
        candidate_prompts: Full candidate map for the round; reused fields carry the previous text.
        train_pass_rate: Currently always 0.0; see field description below.
        validation_pass_rate: Pass rate on the validation split.
        metric_breakdown: Mean score per metric on the validation split.
        accepted: True iff the candidate was accepted as new best.
        acceptance_reason: Human-readable reason for the acceptance decision.
        failed_case_ids: Eval case ids that failed the validation split this round.
        failed_cases_truncated: Number of failed cases dropped by token-budget truncation.
        per_field_diagnosis: Diagnosis text from the reflection LM, keyed by optimized field name.
        reflection_lm_calls: Number of reflection LM invocations this round (including retries).
        round_llm_cost: USD cost for this round (reflection LM + evaluator).
        round_token_usage: Token usage for this round; keys are "prompt", "completion", "total".
        started_at: ISO-8601 timestamp when the round started.
        duration_seconds: Wall-clock duration of the round in seconds.
        extras: Free-form business payload; the optimizer never reads or modifies it.
    """

    round: int = Field(description="1-based round index.")
    optimized_field_names: list[str] = Field(description="Field names rewritten by the optimize model this round.", )
    candidate_prompts: dict[str, str] = Field(description="Full candidate prompt map for the round.", )

    train_pass_rate: float = Field(
        default=0.0,
        description=("Currently always 0.0: gepa does not expose a full-train-set pass "
                     "rate (it only samples minibatches each round). Use "
                     "train_subsample_parent_score / train_subsample_candidate_score "
                     "for per-round minibatch metrics instead."),
    )
    validation_pass_rate: float = Field(description="Pass rate on the validation split.")
    metric_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description=("Mean score per metric on the validation split. Empty when the "
                     "round was skipped before valset evaluation, or when the "
                     "evaluator did not expose per-metric scores."),
    )

    accepted: bool = Field(description="True iff the candidate was accepted as new best.")
    acceptance_reason: str = Field(default="", description="Human-readable acceptance reason.")

    failed_case_ids: list[str] = Field(
        default_factory=list,
        description="Eval case ids that failed validation this round.",
    )
    failed_cases_truncated: int = Field(
        default=0,
        description="Number of failed cases dropped by token-budget truncation.",
    )
    per_field_diagnosis: dict[str, str] = Field(
        default_factory=dict,
        description="Diagnosis text from the reflection LM, keyed by optimized field name.",
    )
    reflection_lm_calls: int = Field(
        default=0,
        description="Number of reflection LM invocations this round (including retries).",
    )

    round_llm_cost: float = Field(
        default=0.0,
        description="USD cost for this round (reflection LM + evaluator).",
    )
    round_token_usage: dict[str, int] = Field(
        default_factory=lambda: {
            "prompt": 0,
            "completion": 0,
            "total": 0
        },
        description='Token usage for this round; keys are "prompt", "completion", "total".',
    )

    started_at: str = Field(description="ISO-8601 timestamp when the round started.")
    duration_seconds: float = Field(description="Wall-clock duration of the round in seconds.")

    kind: RoundKind = Field(
        default="reflective",
        description=("Mutation kind for this round: 'reflective' for the standard "
                     "reflective proposal step and 'merge' for system-aware merges."),
    )
    train_minibatch_size: int = Field(
        default=0,
        description=("Cases sampled from the training set this round. 0 when the round "
                     "skipped before sampling (e.g. 'no proposal')."),
    )
    train_subsample_parent_score: Optional[float] = Field(
        default=None,
        description=("Parent candidate's score on the sampled minibatch; None when no "
                     "subsample was produced."),
    )
    train_subsample_candidate_score: Optional[float] = Field(
        default=None,
        description=("New candidate's score on the sampled minibatch; None when no "
                     "candidate was evaluated."),
    )
    skip_reason: Optional[str] = Field(
        default=None,
        description=("Human-readable reason set on skipped rounds (e.g. "
                     "'subsample perfect', 'no proposal'). None when the round ran "
                     "normally or ended in an error."),
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message when the round ended in an algorithm error.",
    )
    budget_used: Optional[int] = Field(
        default=None,
        description=("Cumulative metric calls consumed across all rounds so far. None "
                     "when the algorithm does not track a budget."),
    )
    budget_total: Optional[int] = Field(
        default=None,
        description="Configured budget cap (e.g. max_metric_calls); None means 'auto'.",
    )

    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form business payload; optimizer ignores it.",
    )


class OptimizeResult(EvalBaseModel):
    """Top-level optimization result.

    Attributes:
        schema_version: Result schema version; bumped on breaking layout changes.
        algorithm: Algorithm name that produced this result.
        status: Final run status.
        finish_reason: Why the loop stopped.
        error_message: Error message when status is FAILED.
        baseline_pass_rate: Validation pass rate of the baseline prompts.
        best_pass_rate: Validation pass rate of the best prompts.
        pass_rate_improvement: best_pass_rate minus baseline_pass_rate.
        baseline_metric_breakdown: Mean score per metric for the baseline.
        best_metric_breakdown: Mean score per metric for the best prompts.
        baseline_prompts: Initial prompt text keyed by TargetPrompt name.
        best_prompts: Best prompt text keyed by TargetPrompt name.
        total_rounds: Number of rounds executed.
        rounds: Per-round records in order.
        total_reflection_lm_calls: Total reflection LM invocations (including retries).
        total_judge_model_calls: Currently always 0; see field description below.
        total_llm_cost: USD cost across the whole run (reflection LM + evaluator).
        total_token_usage: Token usage across the whole run; keys are "prompt", "completion", "total".
        duration_seconds: Wall-clock duration of the whole run in seconds.
        started_at: ISO-8601 timestamp when the run started.
        finished_at: ISO-8601 timestamp when the run finished.
        extras: Free-form business payload; the optimizer never reads or modifies it.
    """

    schema_version: str = Field(default="v1", description="Result schema version.")
    algorithm: str = Field(description=("Algorithm name that produced this result; matches the registered key in "
                                        "OPTIMIZER_REGISTRY (e.g. 'gepa_reflective')."), )

    status: RunStatus = Field(description="Final run status.")
    finish_reason: FinishReason = Field(description="Why the loop stopped.")
    stop_reason: Optional[StopReason] = Field(
        default=None,
        description=("Which stop policy ended the run: 'required_metrics_passing' when "
                     "the framework's per-metric threshold policy fired; "
                     "'budget_exhausted' on MaxMetricCallsStopper; 'no_improvement' on "
                     "NoImprovementStopper; 'timeout' on TimeoutStopCondition; "
                     "'score_threshold' on ScoreThresholdStopper; "
                     "'max_candidate_proposals' / 'max_tracked_candidates' on the "
                     "respective candidate caps; 'completed' when the GEPA loop ended "
                     "without any registered stopper firing. None on FAILED runs that "
                     "errored before any stopper ran."),
    )
    error_message: str = Field(default="", description="Error message when status is FAILED.")

    baseline_pass_rate: float = Field(description="Baseline validation pass rate.")
    best_pass_rate: float = Field(description="Best validation pass rate.")
    pass_rate_improvement: float = Field(description="best_pass_rate minus baseline_pass_rate.")

    baseline_metric_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Mean score per metric for the baseline.",
    )
    best_metric_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Mean score per metric for the best prompts.",
    )
    metric_thresholds: dict[str, float] = Field(
        default_factory=dict,
        description=("PASS/FAIL threshold per metric, copied from evaluate.metrics[].threshold. "
                     "Lets reporters and summary.txt show baseline / best scores alongside "
                     "the per-metric threshold so users can see at a glance whether a metric "
                     "is now above or below its acceptance bar."),
    )

    per_metric_best_candidates: dict[str, list[int]] = Field(
        default_factory=dict,
        description=("Per-metric Pareto-best candidate indices reported by GEPA. Keyed by "
                     "metric name; the list contains 0-based indices into the candidate "
                     "trajectory. Empty when the underlying algorithm does not expose "
                     "per-objective fronts. Useful for diagnosing which candidate excels "
                     "on which metric independent of the aggregated best."),
    )

    baseline_prompts: dict[str, str] = Field(
        default_factory=dict,
        description="Initial prompt text keyed by TargetPrompt name.",
    )
    best_prompts: dict[str, str] = Field(
        default_factory=dict,
        description="Best prompt text keyed by TargetPrompt name.",
    )

    total_rounds: int = Field(description="Number of rounds executed.")
    rounds: list[RoundRecord] = Field(
        default_factory=list,
        description="Per-round records in order.",
    )

    total_reflection_lm_calls: int = Field(description="Total reflection LM invocations (including retries).", )
    total_judge_model_calls: int = Field(
        default=0,
        description=("Currently always 0: the evaluator does not surface per-judge "
                     "invocation counts. Reflection LM cost is reflected in "
                     "total_reflection_lm_calls / total_llm_cost; for judge cost use "
                     "your LLM provider's billing dashboard."),
    )
    total_llm_cost: float = Field(
        default=0.0,
        description="USD cost across the whole run.",
    )
    total_token_usage: dict[str, int] = Field(
        default_factory=lambda: {
            "prompt": 0,
            "completion": 0,
            "total": 0
        },
        description='Token usage across the whole run; keys are "prompt", "completion", "total".',
    )

    duration_seconds: float = Field(description="Wall-clock duration of the run in seconds.")
    started_at: str = Field(description="ISO-8601 timestamp when the run started.")
    finished_at: str = Field(description="ISO-8601 timestamp when the run finished.")

    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form business payload; optimizer ignores it.",
    )

    def dump_to(self, path: str) -> None:
        """Serialize the result to a JSON file using model_dump_json(indent=2)."""
        payload = self.model_dump_json(indent=2, by_alias=True)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(payload)

    @classmethod
    def from_file(cls, path: str) -> "OptimizeResult":
        """Load an OptimizeResult previously written by dump_to."""
        with open(path, "r", encoding="utf-8") as fp:
            payload = fp.read()
        return cls.model_validate_json(payload)

    def format_summary(self, *, output_dir: str, update_source: bool) -> str:
        """Render the human-readable text summary persisted as ``summary.txt``.

        The layout mirrors the terminal summary so users can copy paste any
        line directly. Algorithm name, status, baseline / best pass rates,
        delta, rounds, duration, error message (when present), best prompt
        inventory and the output directory are always included.
        """
        sign = "+" if self.pass_rate_improvement >= 0 else ""
        if self.pass_rate_improvement > 0:
            label = "improved"
        elif self.pass_rate_improvement < 0:
            label = "regressed"
        else:
            label = "no improvement"
        accepted = sum(1 for r in self.rounds if r.accepted)
        lines: list[str] = [
            f"Optimization complete  |  status={self.status}  |  algorithm={self.algorithm}",
            "",
            f"pass_rate     : {self.baseline_pass_rate:.4f} -> {self.best_pass_rate:.4f}"
            f"   ({sign}{self.pass_rate_improvement:.4f}, {label})",
            f"rounds        : {accepted} accepted / {self.total_rounds} total",
            f"duration      : {self.duration_seconds:.2f}s",
            f"started_at    : {self.started_at}",
            f"finished_at   : {self.finished_at}",
        ]
        if self.status != "SUCCEEDED" and self.error_message:
            lines.append(f"error_message : {self.error_message}")
        if self.stop_reason is not None:
            lines.append(f"stop_reason   : {self.stop_reason}")
        lines.append(f"update_source : {'true' if update_source else 'false'}")
        lines.append(f"output_dir    : {output_dir}")
        if (self.baseline_metric_breakdown or self.best_metric_breakdown or self.metric_thresholds):
            lines.append("")
            lines.append("metric breakdown (threshold | baseline -> best):")
            keys = sorted({
                *self.baseline_metric_breakdown.keys(),
                *self.best_metric_breakdown.keys(),
                *self.metric_thresholds.keys(),
            })
            for name in keys:
                b = self.baseline_metric_breakdown.get(name, float("nan"))
                t = self.best_metric_breakdown.get(name, float("nan"))
                if name in self.metric_thresholds:
                    threshold_str = f"{self.metric_thresholds[name]:.4f}"
                else:
                    threshold_str = "  -   "
                lines.append(f"  - {name:<40s} threshold {threshold_str}   "
                             f"{b:.4f} -> {t:.4f}")
        if self.best_prompts:
            lines.append("")
            lines.append("best prompts:")
            for name, content in self.best_prompts.items():
                rel = os.path.join("best_prompts", f"{name}.md")
                lines.append(f"  - {name:<40s} {len(content)} chars   ({rel})")
        lines.append("")
        lines.append(f"artifacts directory: {output_dir}")
        lines.append("  result.json   summary.txt   rounds/   run.log   "
                     "baseline_prompts/   best_prompts/   config.snapshot.json")
        return "\n".join(lines) + "\n"
