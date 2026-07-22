# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool factories for the real tool-safety agent demo."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import StdioServerParameters
from typing_extensions import override

from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills.tools import CopySkillStager
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import StdioConnectionParams
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

DEMO_DIR = Path(__file__).resolve().parents[1]
TOOL_SAFETY_DIR = DEMO_DIR.parent
POLICY_PATH = TOOL_SAFETY_DIR / "tool_safety_policy.yaml"
AUDIT_LOG_PATH = DEMO_DIR / "real_agent_safety_audit.jsonl"
SKILL_ROOT = DEMO_DIR / "skills"
MCP_SERVER_PATH = DEMO_DIR / "mcp_server.py"


class DemoLocalCodeExecutor(UnsafeLocalCodeExecutor):
    """Use the current interpreter so the demo runs on Windows and Unix."""

    @override
    def _build_command_args(self, language: str, file_path: Path) -> list[str]:
        language = language.lower()
        if language in ("python", "py", "python3"):
            return [sys.executable, str(file_path)]
        return super()._build_command_args(language, file_path)


class DemoCopySkillStager(CopySkillStager):
    """Copy skill files without optional POSIX symlink/chmod helpers."""

    async def _link_workspace_dirs(self, *args, **kwargs) -> None:
        return None

    async def _read_only_except_symlinks(self, *args, **kwargs) -> None:
        return None


def create_safety_scanner() -> ToolScriptSafetyScanner:
    """Create the scanner used by every execution boundary in this demo."""
    policy = ToolSafetyPolicy.from_file(POLICY_PATH)
    if "python" not in policy.allowed_commands:
        # The public sample policy allows python3. The real-agent demo also
        # allows python so skill_allow can execute on Windows installations
        # where python3 is not a separate executable.
        policy.allowed_commands.append("python")
    return ToolScriptSafetyScanner(policy)


def create_safety_filter(
    scanner: ToolScriptSafetyScanner,
    *,
    block_on_review: bool,
) -> ToolSafetyFilter:
    """Create a filter for generic Tool, Skill, and MCP execution requests."""
    return ToolSafetyFilter(
        scanner=scanner,
        audit_log_path=AUDIT_LOG_PATH,
        block_on_review=block_on_review,
    )


def create_bash_tool(
    scanner: ToolScriptSafetyScanner,
    *,
    block_on_review: bool,
) -> BashTool:
    """Create a Bash tool guarded before real shell execution."""
    return BashTool(
        cwd=str(DEMO_DIR),
        safety_scanner=scanner,
        safety_audit_log_path=str(AUDIT_LOG_PATH),
        enable_safety_guard=True,
        block_on_review=block_on_review,
    )


def create_code_executor(
    scanner: ToolScriptSafetyScanner,
    *,
    block_on_review: bool,
) -> DemoLocalCodeExecutor:
    """Create a local code executor guarded before code blocks run."""
    return DemoLocalCodeExecutor(
        timeout=10,
        safety_scanner=scanner,
        safety_audit_log_path=str(AUDIT_LOG_PATH),
        enable_safety_guard=True,
        block_on_review=block_on_review,
    )


def create_skill_toolset(safety_filter: ToolSafetyFilter) -> SkillToolSet:
    """Create a Skill toolset whose skill_run command is checked by the filter."""
    return SkillToolSet(
        paths=[str(SKILL_ROOT)],
        filters=[safety_filter],
        allowed_cmds=["python", "python3", "echo", "cat"],
        skill_stager=DemoCopySkillStager(),
    )


def create_mcp_toolset(safety_filter: ToolSafetyFilter) -> MCPToolset:
    """Create a local stdio MCP toolset guarded before MCP tool execution."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env=None,
    )
    return MCPToolset(
        connection_params=StdioConnectionParams(server_params=server_params, timeout=5),
        filters=[safety_filter],
    )
