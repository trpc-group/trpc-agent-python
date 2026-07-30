# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GEPACallback adapter buffering real-time iteration events as RoundRecords.

Implements ``gepa.core.callbacks.GEPACallback`` so the framework captures the
full reflective lifecycle for each iteration:

  * ``on_iteration_start``     — reset per-iteration buffer; snapshot the
                                 reflection-LM counters so per-round deltas
                                 are correct.
  * ``on_minibatch_sampled``   — record train minibatch size for the round.
  * ``on_proposal_end``        — capture which components the reflection LM
                                 actually rewrote this round (gepa's
                                 component selector, e.g. RoundRobin, may
                                 mutate only a subset of the candidate's
                                 components per round).
  * ``on_evaluation_end``      — capture parent / candidate subsample scores
                                 (the first two non-seed evaluations of an
                                 iteration are parent + candidate on the
                                 sampled minibatch).
  * ``on_evaluation_skipped``  — capture the skip reason that prevented a
                                 full validation evaluation (e.g. subsample
                                 gate did not pass).
  * ``on_valset_evaluated``    — capture the full validation pass rate,
                                 metric breakdown and failed case ids; the
                                 ``iteration == 0`` event is recorded as the
                                 baseline instead of a round.
  * ``on_merge_attempted``     — tag the current round as a ``"merge"`` round.
  * ``on_budget_updated``      — track the gepa-reported ``metric_calls_used``
                                 counter so the reporter shows real budget
                                 usage instead of a derived estimate.
  * ``on_iteration_end``       — flush a complete RoundRecord (always, even
                                 for rounds rejected at the subsample gate);
                                 emit a RoundView for the attached reporter.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional

from ._optimize_result import RoundRecord

if TYPE_CHECKING:
    from ._optimize_reporter import OptimizeReporter

# Translate gepa's skip reason literals into user-facing wording.
# Source: reference/gepa reflective_mutation.py:299, :320.
_GEPA_SKIP_REASON_MAP: dict[str, str] = {
    "no_trajectories": "no trajectories captured this round",
    "all_scores_perfect": "minibatch already perfect (skip_perfect_score on)",
}

# Used when a round produced no candidate without emitting evaluation_skipped.
_NO_PROPOSAL_FALLBACK: str = "reflect-LM produced no usable new prompt"


def _translate_skip_reason(raw: Optional[str]) -> Optional[str]:
    """Translate a gepa skip reason; unknown values surface under ``gepa-internal:``."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text in _GEPA_SKIP_REASON_MAP:
        return _GEPA_SKIP_REASON_MAP[text]
    normalised = text.lower().replace(" ", "_").replace("-", "_")
    if normalised in _GEPA_SKIP_REASON_MAP:
        return _GEPA_SKIP_REASON_MAP[normalised]
    return f"gepa-internal: {text}"


class _AgentGEPACallback:
    """Buffer per-iteration RoundRecords for GepaReflectiveOptimizer.

    Attributes:
        rounds: list of RoundRecord populated during gepa.optimize() execution.
        baseline_metric_breakdown: metric breakdown for the seed candidate
            captured from the iteration-0 valset evaluation event.
        baseline_failed_case_ids: failed case ids for the seed candidate.
        baseline_pass_rate: average validation score for the seed candidate.
    """

    def __init__(
        self,
        *,
        adapter: Any = None,
        reflection_lm: Any = None,
        reporter: Optional["OptimizeReporter"] = None,
        train_size: int = 0,
        budget_total: Optional[int] = None,
        metric_thresholds: Optional[Mapping[str, float]] = None,
        on_valset_breakdown: Optional[Callable[[dict[str, float]], None]] = None,
    ) -> None:
        self.rounds: list[RoundRecord] = []
        self.baseline_metric_breakdown: dict[str, float] = {}
        self.baseline_failed_case_ids: list[str] = []
        self.baseline_pass_rate: float = 0.0
        self._adapter = adapter
        self._reflection_lm = reflection_lm
        self._reporter = reporter
        self._train_size = int(train_size)
        self._budget_total = budget_total
        self._metric_thresholds = dict(metric_thresholds or {})
        self._on_valset_breakdown = on_valset_breakdown
        self._budget_used: int = 0
        self._reset_iter_buffer()
        self._calls_at_iter_start: int = 0
        self._cost_at_iter_start: float = 0.0
        self._tokens_at_iter_start: dict[str, int] = {
            "prompt": 0,
            "completion": 0,
            "total": 0,
        }

    def _reset_iter_buffer(self) -> None:
        self._iter_started_at: Optional[datetime] = None
        self._iter_iteration: int = 0
        self._iter_candidate: Optional[dict[str, str]] = None
        self._iter_val_score: Optional[float] = None
        self._iter_is_best: bool = False
        self._iter_metric_breakdown: dict[str, float] = {}
        self._iter_failed_case_ids: list[str] = []
        self._iter_train_minibatch_size: int = 0
        self._iter_train_size: int = self._train_size
        self._iter_train_parent_score: Optional[float] = None
        self._iter_train_candidate_score: Optional[float] = None
        self._iter_skip_reason: Optional[str] = None
        self._iter_error_message: Optional[str] = None
        self._iter_kind: str = "reflective"
        # Components rewritten this round (set by on_proposal_end). None
        # means no proposal event observed for the iteration.
        self._iter_changed_components: Optional[list[str]] = None

    def on_iteration_start(self, event: Mapping[str, Any]) -> None:
        self._reset_iter_buffer()
        self._iter_started_at = datetime.now(timezone.utc)
        self._iter_iteration = int(event.get("iteration", 0))
        if self._reflection_lm is not None:
            self._calls_at_iter_start = int(getattr(self._reflection_lm, "total_calls", 0))
            self._cost_at_iter_start = float(getattr(self._reflection_lm, "total_cost", 0.0))
            usage = getattr(self._reflection_lm, "total_token_usage", None) or {}
            self._tokens_at_iter_start = {
                "prompt": int(usage.get("prompt", 0)),
                "completion": int(usage.get("completion", 0)),
                "total": int(usage.get("total", 0)),
            }

    def on_minibatch_sampled(self, event: Mapping[str, Any]) -> None:
        minibatch_ids = event.get("minibatch_ids") or []
        self._iter_train_minibatch_size = len(minibatch_ids)
        trainset_size = event.get("trainset_size")
        if isinstance(trainset_size, int) and trainset_size > 0:
            self._iter_train_size = trainset_size

    def on_proposal_end(self, event: Mapping[str, Any]) -> None:
        """Capture which components the reflection LM rewrote this round.

        gepa's component selector (e.g. ``RoundRobinReflectionComponentSelector``)
        chooses a subset of the candidate's components per round; only
        components that produced a non-empty new instruction land in
        ``new_instructions``, making it the authoritative source for the
        ``optimized_field_names`` field on the buffered RoundRecord. Code
        paths that bypass this event (e.g. merge rounds) leave the
        marker ``None`` so ``on_iteration_end`` falls back to
        ``candidate.keys()``.
        """
        new_instructions = event.get("new_instructions")
        if isinstance(new_instructions, Mapping):
            self._iter_changed_components = list(new_instructions.keys())

    def on_evaluation_end(self, event: Mapping[str, Any]) -> None:
        """Record subsample scores for the parent and the new candidate.

        gepa marks the post-mutation / post-merge evaluation with
        ``candidate_idx=None`` (reflective_mutation.py:430 emits None for
        the new-candidate eval; merge.py:376 also uses None for the
        post-merge eval). Every other evaluation_end carries an int
        ``candidate_idx`` and represents the parent / current-program
        eval. Routing on this field is more reliable than counting
        event order — earlier seq-based logic misclassified rounds
        where the reflective proposer picked the seed program (id=0)
        as parent, because gepa flags that parent eval with
        ``is_seed_candidate=True`` and the previous early-return
        dropped the parent score, shifting the candidate score into
        the parent slot.
        """
        scores = event.get("scores") or []
        if not scores:
            return
        avg = sum(float(s) for s in scores) / max(1, len(scores))
        if event.get("candidate_idx") is None:
            # New candidate evaluation (reflective post-mutation OR
            # merge post-merge).
            self._iter_train_candidate_score = avg
        else:
            # Parent / current-program evaluation.
            self._iter_train_parent_score = avg
            if not self._iter_train_minibatch_size:
                self._iter_train_minibatch_size = len(scores)

    def on_evaluation_skipped(self, event: Mapping[str, Any]) -> None:
        translated = _translate_skip_reason(event.get("reason"))
        if translated:
            self._iter_skip_reason = translated

    def on_merge_attempted(self, event: Mapping[str, Any]) -> None:
        self._iter_kind = "merge"

    def on_budget_updated(self, event: Mapping[str, Any]) -> None:
        used = event.get("metric_calls_used")
        if isinstance(used, int):
            self._budget_used = used

    def on_error(self, event: Mapping[str, Any]) -> None:
        exc = event.get("exception")
        if exc is not None:
            self._iter_error_message = str(exc)

    def on_valset_evaluated(self, event: Mapping[str, Any]) -> None:
        candidate = event.get("candidate")
        if candidate is None:
            return
        # adapter.last_outcome was set immediately before gepa emits this
        # event, so the breakdown / failures correspond to ``candidate``.
        outcome = getattr(self._adapter, "last_outcome", None) if self._adapter else None
        metric_breakdown: dict[str, float] = {}
        failed_case_ids: list[str] = []
        if outcome is not None:
            metric_breakdown = dict(getattr(outcome, "metric_breakdown", {}))
            failed_case_ids = list(getattr(outcome, "failed_case_ids", []))

        if self._on_valset_breakdown is not None:
            try:
                self._on_valset_breakdown(dict(metric_breakdown))
            except Exception:  # pragma: no cover - never break loop on stopper error
                pass

        if int(event.get("iteration", -1)) == 0:
            self.baseline_metric_breakdown = metric_breakdown
            self.baseline_failed_case_ids = failed_case_ids
            self.baseline_pass_rate = float(event.get("average_score", 0.0))
            if self._reporter is not None:
                try:
                    self._reporter.baseline_evaluated(
                        self.baseline_pass_rate,
                        dict(self.baseline_metric_breakdown),
                        metric_thresholds=dict(self._metric_thresholds),
                    )
                except Exception:  # pragma: no cover - never break loop on reporter error
                    pass
            return

        self._iter_candidate = dict(candidate)
        self._iter_val_score = float(event.get("average_score", 0.0))
        self._iter_is_best = bool(event.get("is_best_program", False))
        self._iter_metric_breakdown = metric_breakdown
        self._iter_failed_case_ids = failed_case_ids

    def on_iteration_end(self, event: Mapping[str, Any]) -> None:
        """Flush a RoundRecord for the iteration regardless of acceptance.

        Iterations rejected at the subsample gate (``_iter_candidate`` stays
        None) are still recorded so the reporter timeline matches gepa's
        actual progression and round indices stay contiguous.
        """
        iteration = int(event.get("iteration", self._iter_iteration))
        started_at = self._iter_started_at or datetime.now(timezone.utc)
        finished_at = datetime.now(timezone.utc)
        duration = max(0.0, (finished_at - started_at).total_seconds())
        proposal_accepted = bool(event.get("proposal_accepted", False))
        candidate_seen = self._iter_candidate is not None
        accepted = proposal_accepted and candidate_seen

        if self._iter_error_message:
            reason = f"error: {self._iter_error_message}"
        elif self._iter_skip_reason:
            reason = f"skipped: {self._iter_skip_reason}"
        elif candidate_seen:
            score = self._iter_val_score or 0.0
            reason = (f"GEPA accepted proposal (val_score={score:.4f})"
                      if accepted else f"Explored by GEPA (val_score={score:.4f})")
        else:
            reason = "no candidate produced this round"

        reflection_calls_delta = 0
        round_llm_cost = 0.0
        round_token_usage = {"prompt": 0, "completion": 0, "total": 0}
        if self._reflection_lm is not None:
            reflection_calls_delta = max(
                0,
                int(getattr(self._reflection_lm, "total_calls", 0)) - self._calls_at_iter_start,
            )
            round_llm_cost = max(
                0.0,
                float(getattr(self._reflection_lm, "total_cost", 0.0)) - self._cost_at_iter_start,
            )
            cur = getattr(self._reflection_lm, "total_token_usage", None) or {}
            for key in ("prompt", "completion", "total"):
                round_token_usage[key] = max(
                    0,
                    int(cur.get(key, 0)) - self._tokens_at_iter_start.get(key, 0),
                )

        validation_pass_rate = (self._iter_val_score if self._iter_val_score is not None else 0.0)
        candidate_prompts = (dict(self._iter_candidate) if candidate_seen else {})
        # Authoritative source: components captured from on_proposal_end.
        # Fallback to full candidate keys for rounds without a proposal
        # event (e.g. merge rounds — "rewrite" doesn't apply, listing all
        # keys is the least misleading default).
        if self._iter_changed_components is not None:
            optimized_field_names = list(self._iter_changed_components)
        elif candidate_seen:
            optimized_field_names = list(self._iter_candidate.keys())
        else:
            optimized_field_names = []

        skip_reason = self._iter_skip_reason
        if (not candidate_seen and skip_reason is None and self._iter_error_message is None):
            skip_reason = _NO_PROPOSAL_FALLBACK

        record = RoundRecord(
            round=iteration,
            optimized_field_names=optimized_field_names,
            candidate_prompts=candidate_prompts,
            train_pass_rate=0.0,
            validation_pass_rate=validation_pass_rate,
            metric_breakdown=dict(self._iter_metric_breakdown),
            accepted=accepted,
            acceptance_reason=reason,
            failed_case_ids=list(self._iter_failed_case_ids),
            reflection_lm_calls=reflection_calls_delta,
            round_llm_cost=round_llm_cost,
            round_token_usage=round_token_usage,
            started_at=started_at.isoformat(),
            duration_seconds=duration,
            kind=self._iter_kind if self._iter_kind in ("reflective", "merge") else "reflective",
            train_minibatch_size=self._iter_train_minibatch_size,
            train_subsample_parent_score=self._iter_train_parent_score,
            train_subsample_candidate_score=self._iter_train_candidate_score,
            skip_reason=skip_reason,
            error_message=self._iter_error_message,
            budget_used=self._budget_used if self._budget_used else None,
            budget_total=self._budget_total,
        )
        self.rounds.append(record)

        if self._reporter is not None:
            try:
                self._emit_round_completed(record)
            except Exception:  # pragma: no cover - never break loop on reporter error
                pass

    def _emit_round_completed(self, record: RoundRecord) -> None:
        """Translate a freshly buffered RoundRecord into a RoundView event."""
        from ._optimize_reporter import RoundView

        view = RoundView(
            round=record.round,
            kind=record.kind,
            train_minibatch_size=record.train_minibatch_size,
            train_size=self._iter_train_size or self._train_size,
            train_subsample_parent_score=record.train_subsample_parent_score,
            train_subsample_candidate_score=record.train_subsample_candidate_score,
            val_pass_rate=(record.validation_pass_rate if record.candidate_prompts else None),
            accepted=record.accepted,
            skip_reason=record.skip_reason,
            error_message=record.error_message,
            duration_seconds=record.duration_seconds,
            budget_used=record.budget_used,
            budget_total=record.budget_total,
        )
        self._reporter.round_completed(view)
