#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Best-effort OpenTelemetry attributes for tool safety checks."""

from __future__ import annotations

from opentelemetry import trace

from trpc_agent_sdk.log import logger

from ._models import SafetyReport


def record_safety_attributes(report: SafetyReport) -> None:
    """Attach bounded, non-secret attributes without affecting the decision."""

    try:
        span = trace.get_current_span()
        if not span.is_recording():
            return
        span.set_attribute("tool.safety.decision", report.decision.value)
        span.set_attribute("tool.safety.risk_level", report.risk_level.value)
        span.set_attribute("tool.safety.rule_id", report.rule_ids)
        span.set_attribute("tool.safety.duration_ms", report.duration_ms)
        span.set_attribute("tool.safety.redacted", report.redacted)
        span.set_attribute("tool.safety.execution_blocked", report.blocked)
        span.set_attribute("tool.safety.policy_version", report.policy_version)
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("tool safety telemetry failed: %s", type(exc).__name__)
