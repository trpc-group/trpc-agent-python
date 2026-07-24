# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard — Module Entry Point.

This module provides a pluggable security scanning system for scripts
executed by Tools, MCP Tools, Skills, and CodeExecutors in the TRPC
Agent framework.

Usage::

    # Via Filter (recommended)
    from trpc_agent_sdk.tools import BashTool
    tool = BashTool(filters_name=["safety_filter"])

    # Via Wrapper (standalone)
    from trpc_agent_sdk.tools.safety import SafetyWrapper
    wrapper = SafetyWrapper()
    result = await wrapper.run_safe(tool_name="Bash", script_content="ls -la")
"""

from trpc_agent_sdk.tools.safety._audit import AuditLogger
from trpc_agent_sdk.tools.safety._bash_scanner import BashScanner
from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._python_scanner import PythonScanner
from trpc_agent_sdk.tools.safety._safety_filter import SafetyFilter
from trpc_agent_sdk.tools.safety._scanner import SafetyScanner
from trpc_agent_sdk.tools.safety._types import AuditEvent
from trpc_agent_sdk.tools.safety._types import RiskCategory
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import RuleMatch
from trpc_agent_sdk.tools.safety._types import SafetyDecision
from trpc_agent_sdk.tools.safety._types import SafetyReport
from trpc_agent_sdk.tools.safety._types import ScanInput
from trpc_agent_sdk.tools.safety._types import ScriptType
from trpc_agent_sdk.tools.safety._wrapper import SafetyWrapper

__all__ = [
    "AuditEvent",
    "AuditLogger",
    "BashScanner",
    "PythonScanner",
    "RiskCategory",
    "RiskLevel",
    "RuleMatch",
    "SafetyDecision",
    "SafetyFilter",
    "SafetyPolicy",
    "SafetyReport",
    "SafetyScanner",
    "SafetyWrapper",
    "ScanInput",
    "ScriptType",
]
