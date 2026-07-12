# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OpenTelemetry attributes for tool safety decisions."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.log import logger

from ._models import SafetyReport

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover - OpenTelemetry is a declared dependency.
    trace = None  # type: ignore[assignment]


def trace_safety_report(report: SafetyReport) -> None:
    """Annotate the current span using only bounded, redacted report fields."""

    if trace is None:
        return
    try:
        span = trace.get_current_span()
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("Unable to obtain current span for tool safety report: %s", exc)
        return

    rule_ids = tuple(report.rule_ids)
    primary_rule_id = getattr(report, "rule_id", None) or (rule_ids[0] if rule_ids else "")
    attributes: dict[str, Any] = {
        "tool.safety.decision": report.decision.value,
        "tool.safety.risk_level": report.risk_level.value,
        "tool.safety.rule_id": primary_rule_id,
        "tool.safety.rule_ids": rule_ids,
        "tool.safety.blocked": report.blocked,
        "tool.safety.redacted": report.redacted,
        "tool.safety.duration_ms": float(report.duration_ms),
    }
    for key, value in attributes.items():
        try:
            span.set_attribute(key, value)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Unable to set tool safety span attribute %s: %s", key, exc)
