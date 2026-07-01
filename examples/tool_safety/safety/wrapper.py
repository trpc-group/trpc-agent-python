# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Wrapper helpers showing how to attach the safety filter to existing tools
and code executors without modifying SDK internals.

Two shapes are provided:
- wrap_tool: returns a new tool with the safety filter prepended.
- wrap_executor: returns a new BaseCodeExecutor subclass whose execute_code
  runs the scanner before delegating to the wrapped executor.
"""
from __future__ import annotations

from typing import Any
from typing import Optional

from .policy import PolicyConfig
from .scanner import SafetyScanner
from .tool_filter import ToolSafetyFilter
from .types import Decision
from .types import ScanInput


def wrap_tool(tool, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Return *tool* with a :class:`ToolSafetyFilter` prepended to its filters.

    *tool* must be a ``trpc_agent_sdk.tools.BaseTool``; imported lazily so this
    module does not hard-depend on the full SDK dependency tree.
    """
    safety_filter = ToolSafetyFilter(policy=policy, audit_path=audit_path, tool_name=tool.name)
    tool.add_one_filter(safety_filter, force=True)
    return tool


def SafeCodeExecutor(inner, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Create a code-executor wrapper that scans code before delegating.

    Returns a new instance of a dynamically built ``BaseCodeExecutor`` subclass
    whose ``execute_code`` runs the safety scanner first. Built lazily so the
    ``trpc_agent_sdk.code_executors`` import (which may pull optional deps like
    docker) only happens when this wrapper is actually used.
    """
    from trpc_agent_sdk.code_executors import BaseCodeExecutor
    from trpc_agent_sdk.code_executors import CodeExecutionResult as CER
    from .audit import AuditLogger

    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(audit_path)

    class _SafeCodeExecutor(BaseCodeExecutor):
        async def execute_code(self, invocation_context, input_data):
            code = input_data.code or "\n".join(b.code for b in input_data.code_blocks)
            report = scanner.scan(ScanInput(script=code, language="python", tool_name="code_executor"))
            audit.log(report, intercepted=report.blocked)
            if report.blocked:
                return CER(stderr=f"TOOL_SAFETY_DENY: {report.rule_ids}", exit_code=126, stdout="")
            return await inner.execute_code(invocation_context, input_data)

    return _SafeCodeExecutor()
