# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool script safety guard public API."""

from ._integration import adapt_cli_request
from ._integration import adapt_code_execution_input
from ._integration import adapt_tool_request
from ._audit import AuditSink
from ._audit import CompositeAuditSink
from ._audit import JsonlAuditSink
from ._audit import LoggingAuditSink
from ._audit import SafetyAuditError
from ._audit import SafetyAuditDegradedError
from ._integration import SafetyGuardedCodeExecutor
from ._integration import ToolSafetyFilter
from ._integration import ToolSafetyViolation
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolMetadata
from ._models import ToolSafetyPolicy
from ._sanitizer import SafetySanitizer
from ._scanner import ToolScriptSafetyGuard

__all__ = [
    "RiskCategory",
    "RiskLevel",
    "AuditSink",
    "CompositeAuditSink",
    "JsonlAuditSink",
    "LoggingAuditSink",
    "SafetyAuditError",
    "SafetyAuditDegradedError",
    "SafetyAuditEvent",
    "SafetyDecision",
    "SafetyFinding",
    "SafetyReport",
    "SafetySanitizer",
    "SafetyGuardedCodeExecutor",
    "ScriptLanguage",
    "ScriptPayload",
    "ScriptScanRequest",
    "ToolMetadata",
    "ToolSafetyFilter",
    "ToolSafetyViolation",
    "ToolScriptSafetyGuard",
    "ToolSafetyPolicy",
    "adapt_cli_request",
    "adapt_code_execution_input",
    "adapt_tool_request",
]
