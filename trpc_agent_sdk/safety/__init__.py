# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard.

A pluggable pre-execution safety scanner for Tool / Skill / CodeExecutor
scripts. Scans Python and Bash content for dangerous file ops, network
egress, process spawning, dependency installs, resource abuse, and secret
leakage, then emits allow / deny / needs_human_review decisions plus
structured reports and audit events.

This package is **opt-in**. Importing it does not change default Tool or
CodeExecutor behavior until a filter or wrapper is attached.

Public API::

    from trpc_agent_sdk.safety import (
        PolicyConfig, SafetyScanner, ToolSafetyFilter, AuditLogger,
        Decision, RiskLevel, SafetyReport, SafetyFinding, ScanInput,
        wrap_tool, safety_wrapper, SafetyGuardedCodeExecutor,
        register_custom_rule,
    )
"""
from __future__ import annotations

from ._audit import AuditLogger
from ._audit import emit_telemetry
from ._policy import PolicyConfig
from ._rules import SafetyRule
from ._rules import default_rules
from ._scanner import SCANNER_VERSION
from ._scanner import SafetyScanner
from ._scanner import clear_custom_rules
from ._scanner import register_custom_rule
from ._scanner import register_rule
from ._scanner import unregister_custom_rule
from ._types import Decision
from ._types import RiskLevel
from ._types import SafetyFinding
from ._types import SafetyReport
from ._types import ScanInput
from ._types import max_risk_level

# Integration layers may fail when optional SDK deps are missing.
_SDK_AVAILABLE = False
try:  # pragma: no cover
    from ._filter import ToolSafetyFilter
    _SDK_AVAILABLE = True
except Exception:  # pylint: disable=broad-except
    ToolSafetyFilter = None  # type: ignore[assignment]

try:  # pragma: no cover
    from ._wrapper import SafeCodeExecutor
    from ._wrapper import SafetyDeniedError
    from ._wrapper import SafetyGuardedCodeExecutor
    from ._wrapper import SafetyReviewedSkillRunner
    from ._wrapper import safe_code_executor
    from ._wrapper import safety_wrapper
    from ._wrapper import wrap_tool
except Exception:  # pylint: disable=broad-except
    SafeCodeExecutor = None  # type: ignore[assignment]
    SafetyDeniedError = None  # type: ignore[assignment]
    SafetyGuardedCodeExecutor = None  # type: ignore[assignment]
    SafetyReviewedSkillRunner = None  # type: ignore[assignment]
    safe_code_executor = None  # type: ignore[assignment]
    safety_wrapper = None  # type: ignore[assignment]
    wrap_tool = None  # type: ignore[assignment]

__all__ = [
    "AuditLogger",
    "emit_telemetry",
    "PolicyConfig",
    "default_rules",
    "SafetyRule",
    "SCANNER_VERSION",
    "SafetyScanner",
    "register_custom_rule",
    "register_rule",
    "unregister_custom_rule",
    "clear_custom_rules",
    "ToolSafetyFilter",
    "Decision",
    "max_risk_level",
    "RiskLevel",
    "SafetyFinding",
    "SafetyReport",
    "ScanInput",
    "SafeCodeExecutor",
    "safe_code_executor",
    "SafetyGuardedCodeExecutor",
    "wrap_tool",
    "safety_wrapper",
    "SafetyDeniedError",
    "SafetyReviewedSkillRunner",
    "_SDK_AVAILABLE",
]
