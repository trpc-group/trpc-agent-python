# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Abstract base class for prompt optimization algorithms."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Sequence

from ._eval_callbacks import Callbacks
from ._optimize_config import FrameworkStopConfig
from ._optimize_config import OptimizeConfigFile
from ._optimize_result import OptimizeResult
from ._remote_eval_service import CallAgent
from ._target_prompt import TargetPrompt

if TYPE_CHECKING:
    from ._optimize_reporter import OptimizeReporter


class BaseOptimizer(ABC):
    """Abstract base class for prompt optimization algorithms.

    Subclasses implement `run()` to execute one full optimization loop
    against the supplied config, evaluator inputs, and TargetPrompt.
    """

    def __init__(
        self,
        *,
        config: OptimizeConfigFile,
        call_agent: CallAgent,
        target_prompt: TargetPrompt,
        train_dataset_path: str,
        validation_dataset_path: str,
        callbacks: Optional[Callbacks] = None,
        output_dir: Optional[str] = None,
        extra_stop_callbacks: Optional[Sequence[Any]] = None,
        extra_gepa_callbacks: Optional[Sequence[Any]] = None,
    ) -> None:
        self.config = config
        self.call_agent = call_agent
        self.target_prompt = target_prompt
        self.train_dataset_path = train_dataset_path
        self.validation_dataset_path = validation_dataset_path
        self.callbacks = callbacks
        self.output_dir = output_dir
        # Runtime-only hooks are not part of the JSON config schema
        # because they're Python callables (SLO monitors, kill switches,
        # custom telemetry sinks) whose identity is meaningful and
        # cannot be serialised. Plain stoppers surface a generic
        # ``"completed"`` stop_reason unless wrapped in
        # ``_LabeledStopper``.
        self.extra_stop_callbacks: list[Any] = (list(extra_stop_callbacks) if extra_stop_callbacks else [])
        self.extra_gepa_callbacks: list[Any] = (list(extra_gepa_callbacks) if extra_gepa_callbacks else [])

    @abstractmethod
    async def run(
        self,
        *,
        reporter: Optional["OptimizeReporter"] = None,
    ) -> OptimizeResult:
        """Execute the optimization loop and return the final OptimizeResult.

        Args:
            reporter: Progress sink for ``baseline_evaluated`` and
                ``round_completed`` events. The facade always supplies
                a non-None instance (``_NullReporter`` when
                ``verbose=0``); subclasses may treat ``None`` as a noop
                for direct invocations.
        """

    @staticmethod
    def resolve_required_thresholds(
        stop_config: FrameworkStopConfig,
        metric_thresholds: dict[str, float],
    ) -> dict[str, float]:
        """Return the subset of thresholds the framework stop policy enforces.

        Resolution rules:
          - ``required_metrics`` is None or empty list → ``{}`` (disabled).
          - ``required_metrics == "all"``              → copy of all thresholds.
          - non-empty list                             → ``metric_thresholds``
            filtered to listed names. Unknown names are silently dropped
            (cross-field validation on :class:`OptimizeConfigFile`
            already rejects them at config load time).

        Algorithms call this once per run and feed the result to
        :meth:`metrics_meet_thresholds`.
        """
        required = stop_config.required_metrics
        if required is None:
            return {}
        if isinstance(required, list):
            if not required:
                return {}
            allowed = set(required)
            return {name: thr for name, thr in metric_thresholds.items() if name in allowed}
        return dict(metric_thresholds)

    @staticmethod
    def metrics_meet_thresholds(
        metric_breakdown: dict[str, float],
        required_thresholds: dict[str, float],
    ) -> bool:
        """True iff every required metric meets its threshold.

        Returns ``False`` when ``required_thresholds`` is empty so the
        policy is a no-op when nothing is required. Callers obtain
        ``required_thresholds`` from :meth:`resolve_required_thresholds`
        for consistent "all / list / None / empty" semantics.
        """
        if not required_thresholds:
            return False
        return all(
            metric_breakdown.get(name, float("-inf")) >= threshold for name, threshold in required_thresholds.items())
