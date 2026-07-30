# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit sinks and OpenTelemetry integration for safety decisions."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Protocol

from opentelemetry import trace

from trpc_agent_sdk.log import logger

from ._models import SafetyAuditEvent
from ._models import SafetyReport

_JSONL_LOCK_STRIPES = tuple(Lock() for _ in range(64))


class AuditSink(Protocol):
    """Destination for one sanitized safety audit event."""

    def emit(self, event: SafetyAuditEvent) -> None:
        """Persist or publish one event."""


class LoggerAuditSink:
    """Default sink that emits a structured event through the SDK logger."""

    def emit(self, event: SafetyAuditEvent) -> None:
        logger.info("tool_safety_audit %s", event.model_dump_json())


class JsonlAuditSink:
    """Append sanitized audit events to a JSONL file.

    Writes are protected within this process. Sharing one file across multiple
    processes requires an external locking or logging system.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        lock_key = self.path.resolve(strict=False)
        self._lock = _JSONL_LOCK_STRIPES[hash(lock_key) % len(_JSONL_LOCK_STRIPES)]

    def emit(self, event: SafetyAuditEvent) -> None:
        line = event.model_dump_json() + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)


class NullAuditSink:
    """Explicit opt-out sink. It is never selected by default."""

    def emit(self, event: SafetyAuditEvent) -> None:
        del event


def record_safety_telemetry(report: SafetyReport) -> None:
    """Attach bounded, non-sensitive safety fields to the current span."""

    try:
        span = trace.get_current_span()
        if not span.is_recording():
            return
        rule_ids = list(dict.fromkeys(finding.rule_id for finding in report.findings))[:32]
        span.set_attribute("tool.safety.decision", report.decision.value)
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
        span.set_attribute("tool.safety.rule_id", report.rule_id)
        span.set_attribute("tool.safety.rule_ids", rule_ids)
        span.set_attribute("tool.safety.blocked", report.decision.value != "allow")
        span.set_attribute("tool.safety.duration_ms", report.duration_ms)
        span.set_attribute("tool.safety.sanitized", report.sanitized)
        span.set_attribute("tool.safety.analysis_complete", report.analysis_complete)
        span.set_attribute("tool.safety.analysis_status", report.analysis_status.value)
        span.set_attribute("tool.safety.blocks_scanned", report.blocks_scanned)
    except Exception as ex:  # pylint: disable=broad-except
        logger.debug("failed to record tool safety telemetry: %s", type(ex).__name__)
