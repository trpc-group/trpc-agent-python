# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit logging and monitoring events for the Tool Script Safety Guard.

This module provides the AuditLogger class which converts scan results
into structured audit events, writes JSONL audit logs, and integrates
with OpenTelemetry for distributed tracing.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Optional

from trpc_agent_sdk.log import logger

from ._types import AuditEvent
from ._types import SafetyReport


class AuditLogger:
    """Logger for safety scan audit events.

    Converts SafetyReport objects into structured AuditEvent entries,
    writes them to JSONL files, and provides OTel-compatible attributes.

    Usage::

        logger = AuditLogger()
        logger.log_report(report)
        # Or write to a file:
        logger = AuditLogger(log_file="tool_safety_audit.jsonl")
        logger.log_report(report)
    """

    def __init__(
        self,
        log_file: Optional[str] = None,
        enable_otel: bool = True,
    ) -> None:
        """Initialize the audit logger.

        Args:
            log_file: Optional path to a JSONL file for persistent logging.
            enable_otel: Whether to emit OpenTelemetry span attributes.
        """
        self._log_file = log_file
        self._enable_otel = enable_otel

    def log_report(self, report: SafetyReport) -> AuditEvent:
        """Log a SafetyReport as an audit event.

        Creates an AuditEvent from the report, writes it to the JSONL
        log file (if configured), and emits OTel span attributes
        (if enabled).

        Args:
            report: The SafetyReport to log.

        Returns:
            The created AuditEvent.
        """
        event = self._report_to_event(report)

        # Write to JSONL file if configured
        if self._log_file:
            self._write_jsonl(event)

        # Emit telemetry events
        if self._enable_otel:
            self._emit_otel(event, report)

        # Log at info level
        if report.is_blocked:
            logger.warning(
                "Safety audit: tool=%s decision=%s risk=%s rules=%s duration=%.1fms blocked=%s",
                event.tool_name,
                event.decision,
                event.risk_level,
                event.rule_id,
                event.scan_duration_ms,
                event.blocked,
            )
        else:
            logger.info(
                "Safety audit: tool=%s decision=%s risk=%s rules=%s duration=%.1fms blocked=%s",
                event.tool_name,
                event.decision,
                event.risk_level,
                event.rule_id,
                event.scan_duration_ms,
                event.blocked,
            )

        return event

    def log_decision(
        self,
        tool_name: str,
        decision: str,
        risk_level: str,
        rule_id: str,
        scan_duration_ms: float,
        blocked: bool,
        masked: bool = False,
        script_type: str = "",
    ) -> AuditEvent:
        """Create and log an audit event directly from decision parameters.

        Useful when you want to log an audit event without creating a
        full SafetyReport first.

        Args:
            tool_name: Name of the tool.
            decision: The safety decision (ALLOW / DENY / NEEDS_HUMAN_REVIEW).
            risk_level: The risk level (LOW / MEDIUM / HIGH / CRITICAL).
            rule_id: Comma-separated rule IDs.
            scan_duration_ms: Scan duration in milliseconds.
            blocked: Whether execution was blocked.
            masked: Whether sensitive data was redacted.
            script_type: Type of script scanned.

        Returns:
            The created AuditEvent.
        """
        event = AuditEvent(
            tool_name=tool_name,
            decision=decision,
            risk_level=risk_level,
            rule_id=rule_id,
            scan_duration_ms=scan_duration_ms,
            masked=masked,
            blocked=blocked,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            script_type=script_type,
        )

        if self._log_file:
            self._write_jsonl(event)

        if self._enable_otel:
            self._emit_otel_event(event)

        logger.info(
            "Safety audit: tool=%s decision=%s risk=%s rules=%s blocked=%s",
            tool_name,
            decision,
            risk_level,
            rule_id,
            blocked,
        )

        return event

    # ── Event Conversion ─────────────────────────────────────────────

    @staticmethod
    def _report_to_event(report: SafetyReport) -> AuditEvent:
        """Convert a SafetyReport to an AuditEvent.

        Args:
            report: The SafetyReport to convert.

        Returns:
            A corresponding AuditEvent.
        """
        rule_ids = ",".join(sorted(set(m.rule_id for m in report.matches)))
        any_masked = any(m.masked for m in report.matches)

        return AuditEvent(
            tool_name=report.tool_name,
            decision=report.decision.name,
            risk_level=report.risk_level.name,
            rule_id=rule_ids if rule_ids else "NONE",
            scan_duration_ms=report.scan_duration_ms,
            masked=any_masked,
            blocked=report.is_blocked,
            timestamp=report.timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            script_type=report.script_type.name,
        )

    # ── JSONL Logging ────────────────────────────────────────────────

    def _write_jsonl(self, event: AuditEvent) -> None:
        """Write an audit event to the JSONL log file.

        Args:
            event: The AuditEvent to write.
        """
        try:
            dir_path = os.path.dirname(self._log_file) if self._log_file else ""
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("AuditLogger: Failed to write audit log: %s", e)

    # ── OpenTelemetry Integration ────────────────────────────────────

    @staticmethod
    def _emit_otel(report: SafetyReport, event: AuditEvent) -> None:
        """Emit OpenTelemetry span attributes for a safety scan.

        Attempts to set span attributes on the current OTel span.
        This is a best-effort operation; if OTel is not configured,
        the call is silently ignored.

        Args:
            report: The original SafetyReport.
            event: The converted AuditEvent.
        """
        try:
            from opentelemetry import trace  # noqa: E811

            span = trace.get_current_span()
            if span and span.is_recording():
                span.set_attributes(event.to_otel_attributes())
        except Exception:  # pylint: disable=broad-except
            # OTel may not be installed or configured — that's fine
            pass

    @staticmethod
    def _emit_otel_event(event: AuditEvent) -> None:
        """Emit OTel span attributes from a direct AuditEvent.

        Args:
            event: The AuditEvent to emit.
        """
        try:
            from opentelemetry import trace  # noqa: E811

            span = trace.get_current_span()
            if span and span.is_recording():
                span.set_attributes(event.to_otel_attributes())
        except Exception:  # pylint: disable=broad-except
            pass
