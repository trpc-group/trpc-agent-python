# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit sinks for tool safety decisions."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import os
from pathlib import Path
import stat
import threading
from typing import IO
from typing import Protocol
import weakref

from opentelemetry import trace

from trpc_agent_sdk.log import logger

from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import RiskLevel
from ._sanitizer import SafetySanitizer


class _PathLock:
    """Weak-referenceable lock shared by sinks targeting one path."""

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self._lock.release()


_PATH_LOCKS: weakref.WeakValueDictionary[str, _PathLock] = weakref.WeakValueDictionary()
_PATH_LOCKS_GUARD = threading.Lock()
_SINK_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SINK_LOCKS_GUARD = threading.Lock()
_FALLBACK_SINK_LOCK = threading.RLock()
_AUDIT_SANITIZER = SafetySanitizer()
_TELEMETRY_SANITIZER = SafetySanitizer()
_AUDIT_FILE_MODE = 0o600


class SafetyAuditError(RuntimeError):
    """Raised when no configured audit sink accepted an event."""


class SafetyAuditDegradedError(SafetyAuditError):
    """Raised when only a fallback audit sink accepted an event."""


class AuditSink(Protocol):
    """Minimal synchronous audit sink."""

    def emit(self, event: SafetyAuditEvent) -> None:
        """Persist one event or raise."""


class JsonlAuditSink:
    """Single-process, thread-safe JSONL audit sink."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = _shared_path_lock(self._path)

    def emit(self, event: SafetyAuditEvent) -> None:
        """Append and flush one JSON event."""
        line = event.model_dump_json() + "\n"
        try:
            missing_parents = []
            parent = self._path.parent
            while not parent.exists():
                missing_parents.append(parent)
                parent = parent.parent
            self._path.parent.mkdir(parents=True, exist_ok=True)
            for directory in missing_parents:
                directory.chmod(0o700)
            with self._lock:
                with _open_secure_file(self._path) as stream:
                    stream.write(line)
                    stream.flush()
        except OSError as error:
            del error
            raise SafetyAuditError("tool safety audit write failed") from None


class LoggingAuditSink:
    """Fallback sink using the project structured logger."""

    def emit(self, event: SafetyAuditEvent) -> None:
        """Write a sanitized JSON event."""
        try:
            logger.warning("tool_safety_audit %s", event.model_dump_json())
        except Exception as error:  # pylint: disable=broad-except
            del error
            raise SafetyAuditError("tool safety fallback audit failed") from None


class CompositeAuditSink:
    """Use a primary sink, then a fallback sink."""

    def __init__(self, primary: AuditSink, fallback: AuditSink | None = None):
        self._primary = primary
        self._fallback = fallback or LoggingAuditSink()

    def emit(self, event: SafetyAuditEvent) -> None:
        """Persist through at least one sink."""
        try:
            self._primary.emit(event)
            return
        except Exception:  # pylint: disable=broad-except
            pass
        degraded = event.model_copy(
            update={
                "decision": (
                    event.decision if event.decision == SafetyDecision.DENY else SafetyDecision.NEEDS_HUMAN_REVIEW),
                "risk_level": (
                    event.risk_level if event.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} else RiskLevel.MEDIUM),
                "redacted":
                True,
                "execution_blocked":
                True,
            })
        try:
            self._fallback.emit(degraded)
        except Exception as error:  # pylint: disable=broad-except
            del error
            raise SafetyAuditError("all tool safety audit sinks failed") from None
        raise SafetyAuditDegradedError("primary tool safety audit sink failed")


def _shared_path_lock(path: Path) -> _PathLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PathLock()
            _PATH_LOCKS[key] = lock
        return lock


def _open_secure_file(path: Path) -> IO[str]:
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, _AUDIT_FILE_MODE)
    try:
        link_stat = os.lstat(path)
        file_stat = os.fstat(descriptor)
        if not os.path.samestat(link_stat, file_stat) or not stat.S_ISREG(file_stat.st_mode):
            raise OSError("audit path must be a regular non-symlink file")
        if os.name == "posix":
            os.fchmod(descriptor, _AUDIT_FILE_MODE)
        return os.fdopen(descriptor, "a", encoding="utf-8", newline="\n")
    except Exception:
        os.close(descriptor)
        raise


def _shared_sink_lock(sink: AuditSink) -> threading.RLock:
    if not isinstance(sink, JsonlAuditSink):
        return _FALLBACK_SINK_LOCK
    with _SINK_LOCKS_GUARD:
        lock = _SINK_LOCKS.get(sink)
        if lock is None:
            lock = threading.RLock()
            _SINK_LOCKS[sink] = lock
        return lock


def create_audit_event(report: SafetyReport, tool_name: str, execution_blocked: bool) -> SafetyAuditEvent:
    """Build the stable audit schema."""
    safe_tool_name, tool_redacted = _AUDIT_SANITIZER.sanitize(tool_name)
    rule_ids = []
    rule_redacted = False
    for rule_id in report.rule_ids:
        safe_rule_id, changed = _AUDIT_SANITIZER.sanitize(rule_id)
        rule_ids.append(safe_rule_id)
        rule_redacted = rule_redacted or changed
    return SafetyAuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tool_name=safe_tool_name,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_ids=rule_ids,
        duration_ms=report.duration_ms,
        redacted=report.redacted or tool_redacted or rule_redacted,
        execution_blocked=execution_blocked,
    )


def emit_report(sink: AuditSink, report: SafetyReport, tool_name: str) -> None:
    """Emit an audit event for a report."""
    blocked = report.decision != SafetyDecision.ALLOW
    event = create_audit_event(report, tool_name, blocked)
    try:
        with _shared_sink_lock(sink):
            sink.emit(event)
    except SafetyAuditDegradedError:
        raise SafetyAuditDegradedError("primary tool safety audit sink failed") from None
    except Exception:  # pylint: disable=broad-except
        raise SafetyAuditError("tool safety audit failed") from None


def set_safety_span_attributes(report: SafetyReport) -> None:
    """Set attributes on the current span; telemetry is best effort."""
    try:
        span = trace.get_current_span()
        rule_ids, rule_ids_redacted = _TELEMETRY_SANITIZER.sanitize(",".join(report.rule_ids))
        span.set_attribute("tool.safety.decision", report.decision.value)
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
        span.set_attribute("tool.safety.rule_id", rule_ids)
        span.set_attribute("tool.safety.duration_ms", report.duration_ms)
        span.set_attribute("tool.safety.redacted", report.redacted or rule_ids_redacted)
        span.set_attribute(
            "tool.safety.execution_blocked",
            report.decision != SafetyDecision.ALLOW,
        )
    except Exception:  # pylint: disable=broad-except
        logger.debug("unable to set tool safety span attributes")
