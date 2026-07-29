# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Monitoring: event-stream collector plus self-instrumented counters.

Two data sources, kept apart on purpose (documented per-field in the report):

* taken straight from the runner event stream: tool call count, sandbox
  execution time (tool-response ``custom_metadata["execution_time"]``),
  model/tool error distribution, token usage;
* self-instrumented (the filter system and the pipeline have no built-in
  telemetry): filter decisions, finding counts, severity distribution,
  phase timings.

OTel spans stay enabled through the SDK defaults with a no-op exporter; this
module is about queryable per-task numbers in the ``metrics`` table, not
about re-reporting what tracing already captures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .store import Metrics


@dataclass
class MetricsCollector:
    """Accumulates monitoring data for one review task."""

    task_id: str
    started_monotonic: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    sandbox_ms: int = 0
    llm_calls: int = 0
    error_dist: dict = field(default_factory=dict)
    token_usage: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    phase_timings: dict = field(default_factory=dict)
    _phase_started: dict = field(default_factory=dict)

    # -- event stream ------------------------------------------------------

    def observe_event(self, event) -> None:
        """Consume one runner event (source: event stream)."""
        calls = event.get_function_calls() or []
        self.tool_calls += len(calls)

        responses = event.get_function_responses() or []
        if responses:
            execution_time = (event.custom_metadata or {}).get("execution_time")
            if execution_time is not None:
                self.sandbox_ms += int(float(execution_time) * 1000)

        if event.error_code:
            key = str(event.error_code)
            self.error_dist[key] = self.error_dist.get(key, 0) + 1

    def observe_model_response(self, response) -> None:
        """Token accounting from after_model_callback (events may drop usage)."""
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        self.llm_calls += 1
        self.token_usage["prompt"] += int(getattr(usage, "prompt_token_count", 0) or 0)
        self.token_usage["completion"] += int(getattr(usage, "candidates_token_count", 0) or 0)
        self.token_usage["total"] += int(getattr(usage, "total_token_count", 0) or 0)

    # -- self instrumentation ---------------------------------------------

    def phase(self, name: str) -> None:
        """Mark the start of a pipeline phase; closes the previous one."""
        now = time.monotonic()
        for started_name, started_at in list(self._phase_started.items()):
            self.phase_timings[started_name] = int((now - started_at) * 1000)
            del self._phase_started[started_name]
        self._phase_started[name] = now

    def record_error(self, kind: str) -> None:
        self.error_dist[kind] = self.error_dist.get(kind, 0) + 1

    def finish(self,
               *,
               filter_blocks: int,
               finding_count: int,
               severity_dist: dict,
               sandbox_ms_fallback: Optional[int] = None) -> Metrics:
        """Close open phases and produce the DB row."""
        self.phase("_end")
        self._phase_started.clear()
        self.phase_timings.pop("_end", None)
        total_ms = int((time.monotonic() - self.started_monotonic) * 1000)
        if self.sandbox_ms == 0 and sandbox_ms_fallback:
            self.sandbox_ms = sandbox_ms_fallback
        return Metrics(
            task_id=self.task_id,
            total_ms=total_ms,
            sandbox_ms=self.sandbox_ms,
            tool_calls=self.tool_calls,
            filter_blocks=filter_blocks,
            finding_count=finding_count,
            severity_dist_json=severity_dist,
            error_dist_json=self.error_dist,
            token_usage_json={
                **self.token_usage, "llm_calls": self.llm_calls,
                "source": "event_stream(usage_metadata); zeros in dry-run"
            },
            phase_timings_json={
                **self.phase_timings, "source_note":
                "tool_calls/sandbox_ms/errors/tokens from event stream; "
                "filter_blocks/findings/phases self-instrumented"
            },
        )
