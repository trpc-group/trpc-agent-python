# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard for tRPC-Agent-Python.

Provides pre-execution safety scanning for Python scripts and Bash commands
executed by tools. Integrates as a BaseFilter and as a standalone scanner.
"""

from ._audit import SafetyAuditLogger
from ._filter import ToolSafetyFilter
from ._policy import SafetyPolicy
from ._scanner import ToolSafetyScanner
from ._telemetry import set_safety_span_attrs
from ._types import AuditEvent
from ._types import Decision
from ._types import RiskLevel
from ._types import RiskType
from ._types import RuleFinding
from ._types import ScanReport

__all__ = [
    "ToolSafetyScanner",
    "ToolSafetyFilter",
    "SafetyPolicy",
    "SafetyAuditLogger",
    "set_safety_span_attrs",
    "AuditEvent",
    "Decision",
    "RiskLevel",
    "RiskType",
    "RuleFinding",
    "ScanReport",
]
