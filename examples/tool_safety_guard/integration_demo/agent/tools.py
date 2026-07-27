# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool factories wired for the Tool Script Safety Guard."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ToolSafetyFilter

DEMO_DIR = Path(__file__).resolve().parents[1]
POLICY_PATH = DEMO_DIR.parent / "tool_safety_policy.yaml"
AUDIT_LOG = DEMO_DIR / "integration_demo_safety_audit.jsonl"
SKILL_ROOT = DEMO_DIR / "skills"
MCP_SERVER = DEMO_DIR / "mcp_server.py"


def create_safety_scanner():
    """Create scanner and policy from the example policy file or defaults.

    Returns:
        Tuple of (SafetyScanner, PolicyConfig).
    """
    if POLICY_PATH.exists():
        policy = PolicyConfig.from_yaml(str(POLICY_PATH))
    else:
        policy = PolicyConfig.default()
    return SafetyScanner(policy), policy


def create_safety_filter(
    scanner: SafetyScanner,
    *,
    block_on_review: bool,
) -> ToolSafetyFilter:
    """Create a ToolSafetyFilter for Skill/MCP tool execution."""
    return ToolSafetyFilter(
        scanner=scanner,
        audit_path=str(AUDIT_LOG),
        block_on_review=block_on_review,
    )


def create_bash_tool(
    scanner: SafetyScanner,
    *,
    block_on_review: bool,
) -> BashTool:
    """Create a Bash tool with safety guard enabled before shell execution."""
    return BashTool(
        enable_safety_guard=True,
        safety_scanner=scanner,
        safety_audit_log_path=str(AUDIT_LOG),
        block_on_review=block_on_review,
    )


def create_code_executor(
    scanner: SafetyScanner,
    *,
    block_on_review: bool,
):
    """Create a local code executor with safety guard before code blocks run."""
    from trpc_agent_sdk.code_executors.local._unsafe_local_code_executor import (
        UnsafeLocalCodeExecutor, )
    return UnsafeLocalCodeExecutor(
        timeout=10,
        enable_safety_guard=True,
        safety_scanner=scanner,
        safety_audit_log_path=str(AUDIT_LOG),
        block_on_review=block_on_review,
    )


def create_skill_toolset(safety_filter: ToolSafetyFilter,
                         policy: Optional[PolicyConfig] = None,
                         block_on_review: bool = False):
    """Create a Skill toolset with safety filter on skill_run commands.

    SkillToolSet does not accept BaseFilter directly, so we wrap it
    with SafetyWrappedToolSet which injects the filter via
    add_tool_safety_filter when get_tools() is called.
    """
    from trpc_agent_sdk.skills import SkillToolSet
    from trpc_agent_sdk.tools.safety import SafetyWrappedToolSet
    inner = SkillToolSet(paths=[str(SKILL_ROOT)])
    return SafetyWrappedToolSet(
        inner=inner,
        policy=policy,
        audit_path=str(AUDIT_LOG),
        block_on_review=block_on_review,
    )


def create_mcp_toolset(safety_filter: ToolSafetyFilter):
    """Create a local stdio MCP toolset with safety filter.

    MCPToolset accepts filters=[BaseFilter] natively. The MCP server
    is intentionally a dry-run endpoint to demonstrate that denied
    commands are blocked before reaching the server.
    """
    from trpc_agent_sdk.tools import MCPToolset
    from trpc_agent_sdk.tools import StdioConnectionParams
    return MCPToolset(
        connection_params=StdioConnectionParams(server_params={
            "command": sys.executable,
            "args": [str(MCP_SERVER)]
        }, ),
        filters=[safety_filter],
    )
