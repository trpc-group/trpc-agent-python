# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Telemetry — OpenTelemetry tracing for the review pipeline (SDK reuse).

Initialises an OTel ``TracerProvider`` and wraps each pipeline stage in a
span via :func:`trace_stage`. A :class:`TelemetryRecorder` collects per-stage
durations and exception-type distribution, then folds them into the
``monitor_summary`` row (total/sandbox duration, exception_types JSON).

The SDK auto-instruments LLM/tool/agent calls when running under its
standard agent pipeline; this module covers the CR Agent's own pipeline
stages (L1–L6) with explicit spans + a recorder, since CR Agent is a
programmatic orchestrator (not an LLM-driven agent loop).
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_TRACER_NAME = "cr_agent"
_initialized = False


def init_telemetry(exporter: Any | None = None, *, enabled: bool = True) -> None:
    """Initialise the OTel tracer provider (idempotent).

    ``exporter=None`` defaults to :class:`ConsoleSpanExporter` (spans printed
    to stdout). Pass a custom exporter (e.g. ``OTLPSpanExporter``) for
    production. When ``enabled=False`` the global NoOp tracer is kept —
    :func:`trace_stage` still works but records nothing.
    """
    global _initialized
    if _initialized or not enabled:
        return
    provider = TracerProvider()
    exp = exporter if exporter is not None else ConsoleSpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer():
    """Return the CR Agent tracer (NoOp until :func:`init_telemetry`)."""
    return trace.get_tracer(_TRACER_NAME)


@asynccontextmanager
async def trace_stage(name: str, recorder: "TelemetryRecorder | None" = None):
    """Wrap a pipeline stage in an OTel span + measure duration.

    On exception, records it on the span (``record_exception`` + ERROR status)
    and on the recorder's ``exception_types`` tally, then re-raises. The
    duration (ms) is set as a span attribute and forwarded to the recorder.
    """
    tracer = get_tracer()
    start = time.monotonic()
    # try/except/finally must live INSIDE the `with span` so attributes and
    # record_exception are set while the span is still active (before it ends
    # on context exit).
    with tracer.start_as_current_span(name) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            if recorder is not None:
                recorder.record_exception(type(exc).__name__)
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            span.set_attribute("duration_ms", duration_ms)
            if recorder is not None:
                recorder.record_stage(name, duration_ms)


@dataclass
class TelemetryRecorder:
    """Collects per-stage durations + exception distribution for monitor_summary."""

    stages: dict[str, int] = field(default_factory=dict)
    exceptions: dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    sandbox_duration_ms: int = 0

    def record_stage(self, name: str, duration_ms: int) -> None:
        self.stages[name] = duration_ms
        # Any stage tagged l4/sandbox contributes to sandbox_duration_ms.
        if "l4" in name or "sandbox" in name:
            self.sandbox_duration_ms += duration_ms

    def record_exception(self, exc_type: str) -> None:
        self.exceptions[exc_type] = self.exceptions.get(exc_type, 0) + 1

    def to_monitor_summary(
        self,
        finding_count: int = 0,
        sev_counts: dict[str, int] | None = None,
        blocks: int = 0,
    ) -> dict:
        """Build the ``monitor_summary`` payload from collected telemetry.

        ``exception_types`` is emitted as a dict (the store JSON-encodes it).
        """
        sev = sev_counts or {}
        return {
            "total_duration_ms": sum(self.stages.values()),
            "sandbox_duration_ms": self.sandbox_duration_ms,
            "tool_calls": self.tool_calls,
            "blocks": blocks,
            "finding_count": finding_count,
            "sev_critical": sev.get("critical", 0),
            "sev_high": sev.get("high", 0),
            "sev_medium": sev.get("medium", 0),
            "sev_low": sev.get("low", 0),
            "exception_types": dict(self.exceptions),
        }
