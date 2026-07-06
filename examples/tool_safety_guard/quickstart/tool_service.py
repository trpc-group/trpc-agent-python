# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tiny script execution service used by the safety quickstart example.

The service intentionally does not execute untrusted scripts. Its methods are
small enough to show exactly where the safety guard is inserted in front of a
tool-like function and a CodeExecutor-like runtime.
"""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext


class DryRunScriptExecutor(BaseCodeExecutor):
    """CodeExecutor delegate that records what would have executed."""

    work_dir: str = ""

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        snippets = [block.code for block in code_execution_input.code_blocks]
        if code_execution_input.code:
            snippets.append(code_execution_input.code)
        line_count = sum(len(snippet.splitlines()) for snippet in snippets)
        return create_code_execution_result(stdout=f"dry-run accepted {line_count} line(s)")


async def dry_run_tool(script: str, *, language: str) -> dict[str, str]:
    """Pretend to execute a tool script after filters have approved it."""

    first_line = next((line.strip() for line in script.splitlines() if line.strip()), "")
    return {
        "status": "executed",
        "language": language,
        "first_line": first_line,
    }


def read_script(path: Path) -> str:
    """Read a UTF-8 script file for the quickstart runner."""

    return path.read_text(encoding="utf-8")
