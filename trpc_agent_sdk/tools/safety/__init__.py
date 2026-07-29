# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard.

A pluggable safety layer that scans Python scripts and Bash commands
*before* they are executed by a Tool, MCP Tool, Skill or CodeExecutor.

Quick start::

    from trpc_agent_sdk.tools.safety import SafetyGuard, Decision

    guard = SafetyGuard.default()
    report = guard.scan("os.system('rm -rf /')", tool_name="BashTool")
    print(report.decision)  # Decision.DENY

Attach to a tool via the Filter system::

    from trpc_agent_sdk.tools import BashTool
    from trpc_agent_sdk.tools.safety import SafetyGuard, ToolSafetyFilter

    guard = SafetyGuard.default()
    bash = BashTool(filters=[ToolSafetyFilter(guard)])

See ``docs/tool_safety_guard.md`` for the full design document.
"""

from ._audit import AuditLogger
from ._bash_scanner import BashAllowedCommandRule
from ._bash_scanner import BashDangerousFileOpsRule
from ._bash_scanner import BashDependencyInstallRule
from ._bash_scanner import BashNetworkEgressRule
from ._bash_scanner import BashProcessSystemRule
from ._bash_scanner import BashResourceAbuseRule
from ._bash_scanner import BashSecretLeakRule
from ._bash_scanner import BashShellInjectionRule
from ._models import AuditEvent
from ._models import compute_script_hash
from ._models import Decision
from ._models import Finding
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyReport
from ._models import ScriptType
from ._policy import RuleOverride
from ._policy import SafetyPolicy
from ._python_scanner import PyDangerousFileOpsRule
from ._python_scanner import PyDependencyInstallRule
from ._python_scanner import PyNetworkEgressRule
from ._python_scanner import PyProcessSystemRule
from ._python_scanner import PyResourceAbuseRule
from ._python_scanner import PySecretLeakRule
from ._rules import Rule
from ._rules import RuleRegistry
from ._rules import ScanContext
from ._rules import global_rule_registry
from ._safety_filter import ToolSafetyFilter
from ._safety_guard import SafetyGuard
from ._safety_guard import detect_script_type
from ._telemetry import report_to_span
from ._telemetry import report_audit_to_span

__all__ = [
    # Core guard
    "SafetyGuard",
    "detect_script_type",
    # Policy
    "SafetyPolicy",
    "RuleOverride",
    # Models
    "Decision",
    "RiskLevel",
    "RiskCategory",
    "ScriptType",
    "Finding",
    "SafetyReport",
    "AuditEvent",
    "compute_script_hash",
    # Rules
    "Rule",
    "RuleRegistry",
    "ScanContext",
    "global_rule_registry",
    # Python rules
    "PyDangerousFileOpsRule",
    "PyNetworkEgressRule",
    "PyProcessSystemRule",
    "PyDependencyInstallRule",
    "PyResourceAbuseRule",
    "PySecretLeakRule",
    # Bash rules
    "BashAllowedCommandRule",
    "BashDangerousFileOpsRule",
    "BashNetworkEgressRule",
    "BashProcessSystemRule",
    "BashDependencyInstallRule",
    "BashResourceAbuseRule",
    "BashSecretLeakRule",
    "BashShellInjectionRule",
    # Integration
    "ToolSafetyFilter",
    "AuditLogger",
    # Telemetry
    "report_to_span",
    "report_audit_to_span",
]
