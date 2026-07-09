"""OpenTelemetry metrics for Script Safety Guard.

This module defines OTel metrics that the Guard engine records during safety checks.
All metrics use the `trpc.python.agent` meter name so they share the same namespace
as other SDK telemetry.

When the OTel SDK is not installed or not configured, the opentelemetry-api package
provides NoOp implementations that silently discard data — no runtime error occurs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-init metric instruments
# ---------------------------------------------------------------------------

_meter = None
_check_count: object | None = None
_scan_duration: object | None = None
_rule_hit_count: object | None = None


def _ensure_instruments() -> bool:
    """Lazily create metric instruments on first use.

    Returns True if instruments are available, False if OTel is not installed.
    """
    global _meter, _check_count, _scan_duration, _rule_hit_count

    if _meter is not None:
        return True

    try:
        from opentelemetry import metrics

        _meter = metrics.get_meter("trpc.python.agent")

        _check_count = _meter.create_counter(
            name="tool.safety.check_count",
            description="Number of safety checks performed, partitioned by decision.",
            unit="1",
        )
        _scan_duration = _meter.create_histogram(
            name="tool.safety.scan_duration",
            description="Duration of safety scan in milliseconds.",
            unit="ms",
        )
        _rule_hit_count = _meter.create_counter(
            name="tool.safety.rule_hit_count",
            description="Number of rule hits, partitioned by rule_id, category, and severity.",
            unit="1",
        )
        return True
    except ImportError:
        logger.debug("opentelemetry-api not installed; safety metrics disabled.")
        return False


# ---------------------------------------------------------------------------
# Public recording functions
# ---------------------------------------------------------------------------


def record_check(decision: str, language: str, tool_name: str = "") -> None:
    """Record a safety check completion.

    Args:
        decision: The final decision (allow/deny/needs_human_review).
        language: Script language that was scanned.
        tool_name: Name of the tool that triggered the check.
    """
    if not _ensure_instruments():
        return
    _check_count.add(  # type: ignore[union-attr]
        1,
        attributes={
            "decision": decision,
            "language": language,
            "tool_name": tool_name,
        },
    )


def record_scan_duration(duration_ms: float, language: str, decision: str) -> None:
    """Record scan duration in milliseconds.

    Args:
        duration_ms: Scan duration in milliseconds.
        language: Script language.
        decision: Final decision.
    """
    if not _ensure_instruments():
        return
    _scan_duration.record(  # type: ignore[union-attr]
        duration_ms,
        attributes={
            "language": language,
            "decision": decision,
        },
    )


def record_rule_hit(rule_id: str, category: str, severity: str) -> None:
    """Record a single rule hit.

    Args:
        rule_id: Rule identifier (e.g. 'FS-001').
        category: Risk category.
        severity: Severity level.
    """
    if not _ensure_instruments():
        return
    _rule_hit_count.add(  # type: ignore[union-attr]
        1,
        attributes={
            "rule_id": rule_id,
            "category": category,
            "severity": severity,
        },
    )
