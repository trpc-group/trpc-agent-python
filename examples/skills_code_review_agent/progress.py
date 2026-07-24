# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Progress reporter for the code review agent.

Provides a callback-based progress reporting mechanism that allows
the review pipeline to emit stage progress events in real-time.
This enables streaming output in CLI, A2A, and AG-UI modes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class ReviewStage(str, Enum):
    """Stages of the code review pipeline."""

    INIT = "initializing"
    PARSE = "parsing_diff"
    FILTER = "filter_governance"
    SANDBOX = "sandbox_execution"
    DEDUP = "deduplication"
    REPORT = "report_generation"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class ProgressEvent:
    """A progress event emitted during the review pipeline."""

    stage: ReviewStage
    message: str
    progress_pct: float  # 0.0 to 100.0
    detail: Optional[str] = None
    duration_ms: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


# Type alias for progress callbacks
ProgressCallback = Callable[[ProgressEvent], None]


class ProgressReporter:
    """Emits progress events during the review pipeline.

    Usage:
        reporter = ProgressReporter()
        reporter.on_progress(lambda evt: print(f"[{evt.stage}] {evt.message}"))

        # In the pipeline:
        reporter.report(ReviewStage.PARSE, "Parsing diff...", 10.0)
        # ... do work ...
        reporter.report(ReviewStage.FILTER, "Running filters...", 30.0)
    """

    def __init__(self) -> None:
        self._callbacks: list[ProgressCallback] = []
        self._start_time: Optional[float] = None
        self._last_event: Optional[ProgressEvent] = None

    @property
    def last_event(self) -> Optional[ProgressEvent]:
        return self._last_event

    def start(self) -> None:
        """Start the progress timer."""
        self._start_time = time.time()

    def on_progress(self, callback: ProgressCallback) -> None:
        """Register a progress callback."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: ProgressCallback) -> None:
        """Remove a previously registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def report(
        self,
        stage: ReviewStage,
        message: str,
        progress_pct: float,
        detail: Optional[str] = None,
    ) -> ProgressEvent:
        """Emit a progress event to all registered callbacks.

        Args:
            stage: The current review stage.
            message: A human-readable progress message.
            progress_pct: Progress percentage (0.0 to 100.0).
            detail: Optional detailed information.

        Returns:
            The emitted ProgressEvent.
        """
        duration_ms = None
        if self._start_time is not None:
            duration_ms = (time.time() - self._start_time) * 1000

        event = ProgressEvent(
            stage=stage,
            message=message,
            progress_pct=progress_pct,
            detail=detail,
            duration_ms=duration_ms,
        )
        self._last_event = event

        for callback in self._callbacks:
            callback(event)

        return event


# Pre-defined progress sequences for the review pipeline
REVIEW_PROGRESS_STEPS = [
    (ReviewStage.INIT, "Initializing review pipeline...", 0.0),
    (ReviewStage.PARSE, "Parsing diff input...", 10.0),
    (ReviewStage.PARSE, "Extracting changed files and hunks...", 20.0),
    (ReviewStage.FILTER, "Running filter governance...", 30.0),
    (ReviewStage.FILTER, "Checking for high-risk patterns...", 35.0),
    (ReviewStage.SANDBOX, "Setting up sandbox environment...", 40.0),
    (ReviewStage.SANDBOX, "Executing static analysis scripts...", 50.0),
    (ReviewStage.SANDBOX, "Running security checks...", 60.0),
    (ReviewStage.DEDUP, "Deduplicating and classifying findings...", 70.0),
    (ReviewStage.DEDUP, "Computing confidence scores...", 75.0),
    (ReviewStage.REPORT, "Masking sensitive information...", 80.0),
    (ReviewStage.REPORT, "Generating review report...", 90.0),
    (ReviewStage.COMPLETE, "Review complete!", 100.0),
]


def print_progress_callback(event: ProgressEvent) -> None:
    """Default progress callback that prints to stdout.

    Suitable for CLI mode. Each stage prints a colored indicator.
    """
    stage_icons = {
        ReviewStage.INIT: "🔧",
        ReviewStage.PARSE: "📄",
        ReviewStage.FILTER: "🔒",
        ReviewStage.SANDBOX: "⚡",
        ReviewStage.DEDUP: "🔍",
        ReviewStage.REPORT: "📝",
        ReviewStage.COMPLETE: "✅",
        ReviewStage.FAILED: "❌",
    }
    icon = stage_icons.get(event.stage, "•")
    duration = f" ({event.duration_ms:.0f}ms)" if event.duration_ms else ""
    print(f"  {icon} [{event.progress_pct:3.0f}%] {event.message}{duration}")
    if event.detail:
        for line in event.detail.split("\n"):
            print(f"     {line}")