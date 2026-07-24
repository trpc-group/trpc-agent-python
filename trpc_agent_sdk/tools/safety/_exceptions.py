"""Typed exceptions for the safety guard.

Each error class covers a distinct failure mode so execution adapters can
decide whether to fail closed (deny) or surface the underlying problem.
"""

from __future__ import annotations


class SafetyGuardError(Exception):
    """Base type for all safety guard errors."""


class SafetyPolicyError(SafetyGuardError):
    """Raised when a policy file is malformed, unknown, or inconsistent.

    Construction-time failure: the guard must not start with an unsafe or
    ambiguous policy.
    """


class SafetyScannerError(SafetyGuardError):
    """Raised when a scanner encounters an internal defect.

    Production execution adapters convert this into a ``deny/critical``
    decision so the path fails closed without leaking request content.
    """


class SafetyAuditError(SafetyGuardError):
    """Raised by an :class:`AuditSink` when it cannot persist an event.

    When ``audit.required`` is true the surrounding adapter treats this as
    a fail-closed signal.
    """


class ToolRequestError(SafetyGuardError):
    """Raised when a tool adapter cannot build a scan request from inputs.

    Examples: missing script field, declared execution-capable tool without
    a usable field mapping, or non-stringifiable argument values.
    """
