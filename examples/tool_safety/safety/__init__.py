# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Tool Script Safety Guard.

A pluggable pre-execution safety scanner for Tool / Skill / CodeExecutor
scripts. Scans Python and Bash content for dangerous file ops, network
egress, process spawning, dependency installs, resource abuse, and secret
leakage, then emits allow / deny / needs_human_review decisions plus
structured reports and audit events.

Public API::

    from examples.tool_safety.safety import (
        PolicyConfig, SafetyScanner, ToolSafetyFilter, AuditLogger,
        Decision, RiskLevel, SafetyReport, SafetyFinding, ScanInput,
    )
"""
from __future__ import annotations

from .audit import AuditLogger
from .audit import emit_telemetry
from .policy import PolicyConfig
from .rules import default_rules
from .rules.base import SafetyRule
from .scanner import SCANNER_VERSION
from .scanner import SafetyScanner
from .types import Decision
from .types import max_risk_level
from .types import RiskLevel
from .types import SafetyFinding
from .types import SafetyReport
from .types import ScanInput

# SDK-bound integration layers. Imported lazily so the core scanner works even
# when the full tRPC-Agent SDK dependency tree (e.g. google-genai) is absent.
# tool_filter and wrapper are imported independently so one failing does not
# disable the other.
_SDK_AVAILABLE = False
try:  # pragma: no cover - exercised only when SDK is importable
    from .tool_filter import ToolSafetyFilter
    _SDK_AVAILABLE = True
except Exception:  # pylint: disable=broad-except
    ToolSafetyFilter = None  # type: ignore[assignment]

try:  # pragma: no cover
    from .wrapper import SafeCodeExecutor
    from .wrapper import wrap_tool
except Exception:  # pylint: disable=broad-except
    SafeCodeExecutor = None  # type: ignore[assignment]
    wrap_tool = None  # type: ignore[assignment]

__all__ = [
    "AuditLogger",
    "emit_telemetry",
    "PolicyConfig",
    "default_rules",
    "SafetyRule",
    "SCANNER_VERSION",
    "SafetyScanner",
    "ToolSafetyFilter",
    "Decision",
    "max_risk_level",
    "RiskLevel",
    "SafetyFinding",
    "SafetyReport",
    "ScanInput",
    "SafeCodeExecutor",
    "wrap_tool",
    "_SDK_AVAILABLE",
]
