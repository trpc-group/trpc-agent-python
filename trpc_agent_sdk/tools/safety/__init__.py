# Copyright (c) 2026 Tencent Inc. All rights reserved.
# Script Safety Guard - 脚本安全护栏组件
"""Script Safety Guard module for pre-execution static analysis of LLM-generated scripts."""

from trpc_agent_sdk.tools.safety.adapters import SafeCodeExecutor, ScriptSafetyFilter
from trpc_agent_sdk.tools.safety.guard import ScriptSafetyGuard
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    SafetyCheckInput,
    SafetyCheckResult,
    ScanContext,
    Severity,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.policy import (
    ENV_POLICY_PATH,
    AuditOutputConfig,
    FileOperationsPolicy,
    NetworkPolicy,
    OutputConfig,
    PolicyConfig,
    ProcessPolicy,
    ReportOutputConfig,
    ResourcePolicy,
    load_policy,
)

__all__ = [
    # Guard engine
    "ScriptSafetyGuard",
    # Adapters
    "ScriptSafetyFilter",
    "SafeCodeExecutor",
    # Models
    "Decision",
    "Finding",
    "Language",
    "RiskCategory",
    "SafetyCheckInput",
    "SafetyCheckResult",
    "ScanContext",
    "Severity",
    "ToolMetadata",
    # Policy
    "ENV_POLICY_PATH",
    "AuditOutputConfig",
    "FileOperationsPolicy",
    "NetworkPolicy",
    "OutputConfig",
    "PolicyConfig",
    "ProcessPolicy",
    "ReportOutputConfig",
    "ResourcePolicy",
    "load_policy",
]
