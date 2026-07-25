# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Minimal Tool Filter integration example."""

from pathlib import Path

from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner
from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter

EXAMPLE_DIR = Path(__file__).resolve().parent
policy = ToolSafetyPolicy.from_yaml(EXAMPLE_DIR / "tool_safety_policy.yaml")
guard = ToolScriptSafetyFilter(
    scanner=ToolSafetyScanner(policy),
    audit_sink=JsonlAuditSink(EXAMPLE_DIR / "tool_safety_audit.jsonl"),
)


def execute_script(script: str, language: str) -> dict:
    """Placeholder script-capable Tool implementation."""
    return {"executed": True, "language": language, "script_size": len(script)}


# The guard runs before execute_script. It can also be attached to MCP or Skill
# tools that expose script/code/command fields in their argument dictionaries.
safe_tool = FunctionTool(execute_script, filters=[guard])
