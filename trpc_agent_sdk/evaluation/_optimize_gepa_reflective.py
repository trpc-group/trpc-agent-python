# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GEPA reflective optimizer: BaseOptimizer subclass driving ``gepa.optimize()``.

Hosts the ``gepa_reflective`` algorithm and its registry entry. The GEPA
protocol adapter and trajectory helpers live in
:mod:`_optimize_gepa_adapter`; the reflection-LM wrapper lives in
:mod:`_optimize_model_callable`.

``gepa`` is an optional dependency: ``gepa.optimize`` and the stopper
classes are imported lazily inside :meth:`GepaReflectiveOptimizer._call_gepa_optimize`
and :meth:`GepaReflectiveOptimizer._build_stop_callbacks`, so importing
this module without ``gepa`` installed succeeds but ``run()`` then fails
fast with an informative ImportError.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional

from ._base_optimizer import BaseOptimizer
from ._eval_case import EvalCase
from ._eval_config import EvalConfig
from ._eval_set import EvalSet
from ._optimize_config import FrameworkStopConfig
from ._optimize_config import GepaReflectiveAlgo
from ._optimize_gepa_adapter import _AgentGEPAAdapter
from ._optimize_gepa_callback import _AgentGEPACallback
from ._optimize_metric_info import build_metric_reference_doc
from ._optimize_metric_info import build_reflection_prompt_template
from ._optimize_model_callable import _OptimizeModelCallable
from ._optimize_reporter import OptimizeReporter
from ._optimize_reporter import _SilentGepaLogger
from ._optimize_result import OptimizeResult
from ._optimize_result import RoundRecord
from ._optimize_result import StopReason


def _load_evalset_cases(path: str) -> list[EvalCase]:
    """Read an EvalSet JSON file and return its eval_cases list.

    Raises:
        FileNotFoundError: if path does not exist.
        pydantic.ValidationError: on schema violations.
    """
    content = Path(path).read_text(encoding="utf-8")
    evalset = EvalSet.model_validate_json(content)
    return list(evalset.eval_cases)


def _collect_metric_thresholds(eval_config: EvalConfig) -> dict[str, float]:
    """Return ``{metric_name: threshold}`` for every metric in the evaluator config.

    Mirrors what the local evaluator and per-metric evaluators consume so the
    reporter and the persisted result share one source of truth for thresholds.
    """
    return {metric.metric_name: float(metric.threshold) for metric in eval_config.get_eval_metrics()}


class _LabeledStopper:
    """Wrap a gepa StopperProtocol with a stable :data:`StopReason` label.

    Delegates ``__call__`` to the inner stopper and exposes a sticky
    ``last_triggered`` flag set the first time the inner stopper returns
    ``True``. ``_classify_stop_reason`` reads the label after gepa
    returns to map back to a single ``stop_reason`` enum value.
    """

    def __init__(self, inner: Any, label: StopReason) -> None:
        self._inner = inner
        self.label: StopReason = label
        self.last_triggered: bool = False

    def __call__(self, *args: Any, **kwargs: Any) -> bool:
        result = bool(self._inner(*args, **kwargs))
        if result:
            self.last_triggered = True
        return result


class _RequiredMetricsAboveThresholdStopper:
    """gepa Stopper that fires once every required metric meets its threshold.

    Backs the framework-level ``stop.required_metrics`` policy. Each
    iteration's per-metric breakdown is pushed via ``update`` (called by
    ``_AgentGEPACallback.on_valset_breakdown``); ``__call__`` returns
    True as soon as that breakdown clears every threshold, halting the
    run with ``stop_reason="required_metrics_passing"``.

    Attributes:
        last_triggered: Sticky flag set the first time ``__call__``
            returned True.
    """

    def __init__(self, required_thresholds: dict[str, float]) -> None:
        self._thresholds: dict[str, float] = dict(required_thresholds)
        self._latest: dict[str, float] = {}
        self.last_triggered: bool = False

    def update(self, breakdown: dict[str, float]) -> None:
        """Record the most recent per-metric breakdown observed on the valset."""
        self._latest = dict(breakdown)

    def __call__(self, gepa_state: Any = None) -> bool:
        triggered = BaseOptimizer.metrics_meet_thresholds(self._latest, self._thresholds)
        if triggered:
            self.last_triggered = True
        return triggered


def _build_optimize_result(
    *,
    gepa_result: Any,
    baseline_prompts: dict[str, str],
    best_candidate: dict[str, str],
    reflection_lm_cost: float,
    started_at: datetime,
    finished_at: datetime,
    algo_name: str,
    finish_reason: str = "completed",
    callback_rounds: Optional[list[RoundRecord]] = None,
    baseline_metric_breakdown: Optional[dict[str, float]] = None,
    metric_thresholds: Optional[dict[str, float]] = None,
    stop_reason: Optional[StopReason] = None,
    total_reflection_lm_calls: int = 0,
    total_judge_model_calls: int = 0,
    total_judge_cost: float = 0.0,
    total_token_usage: Optional[dict[str, int]] = None,
) -> OptimizeResult:
    """Map a successful GEPAResult into the framework's OptimizeResult schema.

    Round source priority:
      1. ``callback_rounds`` — real-time RoundRecord buffer from
         :class:`_AgentGEPACallback` (used in production whenever gepa
         emits iteration events).
      2. Post-hoc reconstruction from ``gepa_result.candidates`` /
         ``val_aggregate_scores`` — fallback for callers that don't
         install the callback (e.g. mock-driven unit tests, older gepa
         versions).

    Args:
        baseline_metric_breakdown: Per-metric mean for the baseline
            candidate, captured by callback at iteration 0.
        total_reflection_lm_calls: Reflection LM invocation count.
        total_judge_model_calls: Evaluator-internal judge LM count.
        total_judge_cost: USD cost charged to the judge LM (added to
            reflection-LM cost).
        total_token_usage: ``{"prompt", "completion", "total"}`` for the
            reflection LM, optionally merged with judge token usage.
    """
    val_scores = list(gepa_result.val_aggregate_scores)
    baseline_pass_rate = float(val_scores[0]) if val_scores else 0.0
    best_idx = int(gepa_result.best_idx)
    best_pass_rate = float(val_scores[best_idx]) if val_scores else 0.0

    started_iso = started_at.isoformat()
    if callback_rounds:
        rounds = list(callback_rounds)
    else:
        # Fallback path: no callback event stream available. gepa_result
        # alone doesn't carry per-round mutation metadata, so fields
        # below use the most-conservative approximation:
        #   * optimized_field_names: all candidate keys (no signal for
        #     which subset the reflection LM actually rewrote — the
        #     callback path narrows this via on_proposal_end).
        #   * accepted: equated with is_best, since GEPAResult only
        #     reports the final winner, not per-round acceptance.
        candidates = list(gepa_result.candidates)
        rounds = []
        for i in range(1, len(candidates)):
            candidate = dict(candidates[i])
            score = float(val_scores[i]) if i < len(val_scores) else 0.0
            is_best = i == best_idx
            rounds.append(
                RoundRecord(
                    round=i,
                    optimized_field_names=list(candidate.keys()),
                    candidate_prompts=candidate,
                    train_pass_rate=0.0,
                    validation_pass_rate=score,
                    accepted=is_best,
                    acceptance_reason=(f"Selected as best by GEPA (val_score={score:.4f})"
                                       if is_best else f"Explored by GEPA (val_score={score:.4f})"),
                    started_at=started_iso,
                    duration_seconds=0.0,
                ))

    best_metric_breakdown: dict[str, float] = {}
    for record in rounds:
        if record.candidate_prompts == best_candidate and record.metric_breakdown:
            best_metric_breakdown = dict(record.metric_breakdown)
            break

    # When gepa finds no improvement (best_idx == 0), best_candidate equals
    # the seed prompts and the loop above never matches — iteration 0 is
    # captured as ``baseline_metric_breakdown`` rather than a RoundRecord.
    # Mirror baseline data into ``best`` so summary.txt shows
    # ``baseline -> baseline`` (no improvement) instead of
    # ``baseline -> nan`` (looks like data loss).
    if (not best_metric_breakdown and best_candidate == baseline_prompts and baseline_metric_breakdown):
        best_metric_breakdown = dict(baseline_metric_breakdown)

    extras: dict[str, Any] = {}
    total_metric_calls = getattr(gepa_result, "total_metric_calls", None)
    if total_metric_calls is not None:
        extras["total_metric_calls"] = int(total_metric_calls)

    duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
    token_usage = dict(total_token_usage) if total_token_usage else {
        "prompt": 0,
        "completion": 0,
        "total": 0,
    }

    # GEPA's per_objective_best_candidates is dict[str, set[int]] | None;
    # convert to dict[str, list[int]] (sorted) for stable JSON output.
    raw_per_metric_best = getattr(gepa_result, "per_objective_best_candidates", None)
    per_metric_best: dict[str, list[int]] = {}
    if isinstance(raw_per_metric_best, dict):
        for metric_name, indices in raw_per_metric_best.items():
            try:
                per_metric_best[str(metric_name)] = sorted(int(i) for i in indices)
            except (TypeError, ValueError):
                continue

    return OptimizeResult(
        algorithm=algo_name,
        status="SUCCEEDED",
        finish_reason=finish_reason,
        stop_reason=stop_reason,
        baseline_pass_rate=baseline_pass_rate,
        best_pass_rate=best_pass_rate,
        pass_rate_improvement=best_pass_rate - baseline_pass_rate,
        baseline_metric_breakdown=dict(baseline_metric_breakdown or {}),
        best_metric_breakdown=best_metric_breakdown,
        metric_thresholds=dict(metric_thresholds or {}),
        per_metric_best_candidates=per_metric_best,
        baseline_prompts=dict(baseline_prompts),
        best_prompts=dict(best_candidate),
        total_rounds=len(rounds),
        rounds=rounds,
        total_reflection_lm_calls=int(total_reflection_lm_calls),
        total_judge_model_calls=int(total_judge_model_calls),
        total_llm_cost=float(reflection_lm_cost) + float(total_judge_cost),
        total_token_usage=token_usage,
        duration_seconds=duration_seconds,
        started_at=started_iso,
        finished_at=finished_at.isoformat(),
        extras=extras,
    )


def _build_failed_result(
    *,
    baseline_prompts: dict[str, str],
    started_at: datetime,
    finished_at: datetime,
    error_message: str,
    algo_name: str,
    metric_thresholds: Optional[dict[str, float]] = None,
) -> OptimizeResult:
    """Build a FAILED OptimizeResult preserving the baseline as the best prompts."""
    return OptimizeResult(
        algorithm=algo_name,
        status="FAILED",
        finish_reason="error",
        error_message=error_message,
        baseline_pass_rate=0.0,
        best_pass_rate=0.0,
        pass_rate_improvement=0.0,
        metric_thresholds=dict(metric_thresholds or {}),
        baseline_prompts=dict(baseline_prompts),
        best_prompts=dict(baseline_prompts),
        total_rounds=0,
        rounds=[],
        total_reflection_lm_calls=0,
        total_judge_model_calls=0,
        total_llm_cost=0.0,
        duration_seconds=max(0.0, (finished_at - started_at).total_seconds()),
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        extras={},
    )


def _build_stop_callbacks(
    algo: GepaReflectiveAlgo,
    stop_config: FrameworkStopConfig,
    metric_thresholds: dict[str, float],
    *,
    output_dir: Optional[str] = None,
) -> tuple[list[Any], Optional[_RequiredMetricsAboveThresholdStopper]]:
    """Translate stop fields into gepa StopperProtocol instances.

    Each non-None ``algo`` field maps to one gepa-native stopper
    (max_metric_calls, no_improvement, timeout, score_threshold,
    max_candidate_proposals, max_tracked_candidates).

    The framework-level :class:`FrameworkStopConfig` adds the
    metric-thresholds policy via
    :class:`_RequiredMetricsAboveThresholdStopper` when
    ``stop_config.required_metrics`` resolves to a non-empty subset of
    ``metric_thresholds``. That instance is also returned so the caller
    can inspect ``last_triggered`` for stop-reason classification.

    When ``output_dir`` is supplied, a :class:`gepa.utils.FileStopper`
    watches ``<output_dir>/optimize.stop``: creating that file (e.g.
    ``touch $OUTPUT_DIR/optimize.stop``) halts gepa cleanly at the next
    poll and surfaces as ``stop_reason="user_requested_stop"``.

    Returns:
        ``(stop_callbacks, framework_stopper)`` — ``framework_stopper``
        is ``None`` when no per-metric thresholds are enforced.
    """
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper
    from gepa.utils.stop_condition import MaxMetricCallsStopper
    from gepa.utils.stop_condition import MaxTrackedCandidatesStopper
    from gepa.utils.stop_condition import NoImprovementStopper
    from gepa.utils.stop_condition import ScoreThresholdStopper
    from gepa.utils.stop_condition import TimeoutStopCondition

    callbacks: list[Any] = []
    if algo.max_metric_calls is not None:
        callbacks.append(_LabeledStopper(
            MaxMetricCallsStopper(int(algo.max_metric_calls)),
            "budget_exhausted",
        ))
    if algo.max_iterations_without_improvement is not None:
        callbacks.append(
            _LabeledStopper(
                NoImprovementStopper(int(algo.max_iterations_without_improvement)),
                "no_improvement",
            ))
    if algo.timeout_seconds is not None:
        callbacks.append(_LabeledStopper(
            TimeoutStopCondition(float(algo.timeout_seconds)),
            "timeout",
        ))
    if algo.score_threshold is not None:
        callbacks.append(_LabeledStopper(
            ScoreThresholdStopper(float(algo.score_threshold)),
            "score_threshold",
        ))
    if algo.max_candidate_proposals is not None:
        callbacks.append(
            _LabeledStopper(
                MaxCandidateProposalsStopper(int(algo.max_candidate_proposals)),
                "max_candidate_proposals",
            ))
    if algo.max_tracked_candidates is not None:
        callbacks.append(
            _LabeledStopper(
                MaxTrackedCandidatesStopper(int(algo.max_tracked_candidates)),
                "max_tracked_candidates",
            ))

    framework_stopper: Optional[_RequiredMetricsAboveThresholdStopper] = None
    required = BaseOptimizer.resolve_required_thresholds(stop_config, metric_thresholds)
    if required:
        framework_stopper = _RequiredMetricsAboveThresholdStopper(required)
        callbacks.append(framework_stopper)

    if output_dir is not None:
        import os as _os
        from gepa.utils import FileStopper

        callbacks.append(
            _LabeledStopper(
                FileStopper(_os.path.join(output_dir, "optimize.stop")),
                "user_requested_stop",
            ))

    return callbacks, framework_stopper


def _classify_stop_reason(
    *,
    stop_callbacks: list[Any],
    framework_stopper: Optional[_RequiredMetricsAboveThresholdStopper],
) -> StopReason:
    """Pick the most-specific :data:`StopReason` for an ended gepa run.

    Resolution order:
      1. Framework-level ``required_metrics`` policy (highest priority
         because users explicitly opt in).
      2. First :class:`_LabeledStopper` whose ``last_triggered`` is True
         (insertion order breaks ties when gepa polled multiple stoppers
         in the same tick).
      3. ``"completed"`` when no stopper fired (gepa loop ended
         naturally, e.g. exhausted candidate proposals).
    """
    if framework_stopper is not None and framework_stopper.last_triggered:
        return "required_metrics_passing"
    for stopper in stop_callbacks:
        if isinstance(stopper, _LabeledStopper) and stopper.last_triggered:
            return stopper.label
    return "completed"


class GepaReflectiveOptimizer(BaseOptimizer):
    """BaseOptimizer driving ``gepa.optimize()`` with the framework adapter.

    Flow inside :meth:`run`:
      1. Snapshot baseline prompts via ``TargetPrompt.read_all``.
      2. Load training / validation eval cases.
      3. Build :class:`_AgentGEPAAdapter` and
         :class:`_OptimizeModelCallable` (gepa-compatible reflection LM).
      4. Run ``gepa.optimize`` in a worker thread (``asyncio.to_thread``)
         so its sync main loop does not block the surrounding event loop.
      5. On success, return a populated :class:`OptimizeResult`; on
         failure, return a FAILED result preserving the baseline prompts.

    The facade (``AgentOptimizer.optimize``) decides whether to persist
    the winning candidate based on the ``update_source`` flag.
    """

    async def _call_gepa_optimize(self, **kwargs: Any) -> Any:
        """Run gepa.optimize in a thread; isolated for tests to monkeypatch."""
        from gepa import optimize as gepa_optimize  # lazy import; gepa is optional

        return await asyncio.to_thread(gepa_optimize, **kwargs)

    async def run(
        self,
        *,
        reporter: Optional[OptimizeReporter] = None,
    ) -> OptimizeResult:
        algo: GepaReflectiveAlgo = self.config.optimize.algorithm
        algo_name = algo.name
        metric_thresholds = _collect_metric_thresholds(self.config.evaluate)

        started_at = datetime.now(timezone.utc)
        baseline_prompts = await self.target_prompt.read_all()
        seed_candidate = dict(baseline_prompts)

        try:
            trainset = _load_evalset_cases(self.train_dataset_path)
            valset = _load_evalset_cases(self.validation_dataset_path)
        except Exception as ex:
            return _build_failed_result(
                baseline_prompts=baseline_prompts,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error_message=f"dataset load failed: {ex}",
                algo_name=algo_name,
                metric_thresholds=metric_thresholds,
            )

        adapter = _AgentGEPAAdapter(
            target_prompt=self.target_prompt,
            eval_config=self.config.evaluate,
            call_agent=self.call_agent,
            callbacks=self.callbacks,
            num_runs=self.config.evaluate.num_runs,
            case_parallelism=self.config.optimize.eval_case_parallelism,
            top_k_per_case=int(algo.reflection_history_top_k),
            output_dir=self.output_dir,
        )
        reflection_lm = _OptimizeModelCallable(algo.reflection_lm)

        try:
            return await self._run_with_adapter(
                adapter=adapter,
                reflection_lm=reflection_lm,
                algo=algo,
                algo_name=algo_name,
                baseline_prompts=baseline_prompts,
                seed_candidate=seed_candidate,
                trainset=trainset,
                valset=valset,
                metric_thresholds=metric_thresholds,
                started_at=started_at,
                reporter=reporter,
            )
        finally:
            adapter.close()

    async def _run_with_adapter(
        self,
        *,
        adapter: _AgentGEPAAdapter,
        reflection_lm: _OptimizeModelCallable,
        algo: GepaReflectiveAlgo,
        algo_name: str,
        baseline_prompts: dict[str, str],
        seed_candidate: dict[str, str],
        trainset: list,
        valset: list,
        metric_thresholds: dict[str, float],
        started_at: datetime,
        reporter: Optional[OptimizeReporter],
    ) -> OptimizeResult:
        try:
            stop_callbacks, framework_stopper = _build_stop_callbacks(
                algo,
                self.config.optimize.stop,
                metric_thresholds,
                output_dir=self.output_dir,
            )
        except ImportError as ex:
            return _build_failed_result(
                baseline_prompts=baseline_prompts,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error_message=f"gepa stop_callbacks unavailable: {ex}",
                algo_name=algo_name,
                metric_thresholds=metric_thresholds,
            )

        gepa_callback = _AgentGEPACallback(
            adapter=adapter,
            reflection_lm=reflection_lm,
            reporter=reporter,
            train_size=len(trainset),
            budget_total=algo.max_metric_calls,
            metric_thresholds=metric_thresholds,
            on_valset_breakdown=(framework_stopper.update if framework_stopper is not None else None),
        )

        # Embed a metric reference doc in the reflection prompt template so
        # the reflection LM understands each feedback row. Empty doc still
        # yields a GEPA-valid template.
        reflection_prompt_template = build_reflection_prompt_template(build_metric_reference_doc(self.config.evaluate))

        gepa_kwargs: dict[str, Any] = dict(
            seed_candidate=seed_candidate,
            trainset=trainset,
            valset=valset,
            adapter=adapter,
            reflection_lm=reflection_lm,
            reflection_prompt_template=reflection_prompt_template,
            callbacks=[gepa_callback, *self.extra_gepa_callbacks],
            candidate_selection_strategy=algo.candidate_selection_strategy,
            module_selector=algo.module_selector,
            reflection_minibatch_size=algo.reflection_minibatch_size,
            skip_perfect_score=algo.skip_perfect_score,
            perfect_score=algo.perfect_score,
            use_merge=algo.use_merge,
            max_merge_invocations=algo.max_merge_invocations,
            merge_val_overlap_floor=algo.merge_val_overlap_floor,
            frontier_type=algo.frontier_type,
            cache_evaluation=algo.cache_evaluation,
            track_best_outputs=algo.track_best_outputs,
            raise_on_exception=True,
            seed=algo.seed,
            display_progress_bar=False,
            stop_callbacks=[*stop_callbacks, *self.extra_stop_callbacks],
        )
        # ``max_metric_calls`` is also a direct kwarg for backwards
        # compatibility with gepa builds lacking ``MaxMetricCallsStopper``.
        if algo.max_metric_calls is not None:
            gepa_kwargs["max_metric_calls"] = int(algo.max_metric_calls)

        # Silence gepa's stdout logger when a reporter is attached so its
        # internal messages don't collide with the reporter timeline.
        if reporter is not None:
            gepa_kwargs["logger"] = _SilentGepaLogger(verbose=1)
        try:
            gepa_result = await self._call_gepa_optimize(**gepa_kwargs)
        except Exception as ex:
            return _build_failed_result(
                baseline_prompts=baseline_prompts,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error_message=str(ex),
                algo_name=algo_name,
                metric_thresholds=metric_thresholds,
            )

        best_idx = int(gepa_result.best_idx)
        best_candidate = dict(gepa_result.candidates[best_idx])

        val_scores = list(gepa_result.val_aggregate_scores)
        baseline_pass_rate = float(val_scores[0]) if val_scores else 0.0
        best_pass_rate = float(val_scores[best_idx]) if val_scores else 0.0
        if best_pass_rate >= 1.0 and baseline_pass_rate >= 1.0:
            finish_reason = "perfect_pass_rate"
        elif best_pass_rate <= baseline_pass_rate:
            finish_reason = "no_improvement"
        else:
            finish_reason = "completed"

        stop_reason: StopReason = _classify_stop_reason(
            stop_callbacks=stop_callbacks,
            framework_stopper=framework_stopper,
        )

        return _build_optimize_result(
            gepa_result=gepa_result,
            baseline_prompts=baseline_prompts,
            best_candidate=best_candidate,
            reflection_lm_cost=reflection_lm.total_cost,
            callback_rounds=gepa_callback.rounds,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            algo_name=algo_name,
            finish_reason=finish_reason,
            baseline_metric_breakdown=dict(gepa_callback.baseline_metric_breakdown),
            metric_thresholds=metric_thresholds,
            stop_reason=stop_reason,
            total_reflection_lm_calls=int(reflection_lm.total_calls),
            total_judge_model_calls=0,
            total_judge_cost=0.0,
            total_token_usage=dict(reflection_lm.total_token_usage),
        )
