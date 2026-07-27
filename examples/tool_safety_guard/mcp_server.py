# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Local stdio MCP command server used by the real-agent safety demo."""

from __future__ import annotations

import asyncio
import math
import shlex
from pathlib import Path

from mcp.server import FastMCP
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard

APP = FastMCP("tool-safety-demo")
WORK_DIR = Path(__file__).resolve().parent
POLICY_PATH = WORK_DIR / "tool_safety_policy.yaml"
GUARD = ToolScriptSafetyGuard.from_policy(POLICY_PATH)
MAX_OUTPUT_CHARS = 4096
PROCESS_REAP_TIMEOUT_SECONDS = 1.0


@APP.tool()
async def execute_command(command: str, timeout: float | None = None) -> dict:
    """Execute an approved shell command in the disposable example directory."""
    requested_timeout = float(timeout) if isinstance(timeout, (int, float)) else None
    if requested_timeout is not None and not math.isfinite(requested_timeout):
        return {
            "decision": SafetyDecision.NEEDS_HUMAN_REVIEW.value,
            "summary": "needs_human_review: timeout must be finite.",
            "execution_blocked": True,
        }
    if requested_timeout is not None and requested_timeout <= 0:
        requested_timeout = None
    effective_timeout = min(
        requested_timeout or float(GUARD.policy.max_timeout_seconds),
        float(GUARD.policy.max_timeout_seconds),
    )
    request = ScriptScanRequest(
        payloads=[ScriptPayload(
            language=ScriptLanguage.BASH,
            content=command,
            source="mcp.execute_command",
        )],
        cwd=str(WORK_DIR),
        execution_root=str(WORK_DIR.anchor),
        metadata=ToolMetadata(name="execute_command"),
        requested_timeout_seconds=requested_timeout,
        effective_timeout_seconds=effective_timeout,
        max_output_bytes=GUARD.policy.max_output_bytes,
    )
    report = GUARD.scan(request)
    if report.decision != SafetyDecision.ALLOW:
        return {
            **report.as_dict(),
            "execution_blocked": True,
        }
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as error:
        return {
            "decision": SafetyDecision.NEEDS_HUMAN_REVIEW.value,
            "summary": "needs_human_review: malformed shell command.",
            "error": str(error),
            "execution_blocked": True,
        }
    if not argv:
        return {
            "decision": SafetyDecision.NEEDS_HUMAN_REVIEW.value,
            "summary": "needs_human_review: empty shell command.",
            "execution_blocked": True,
        }
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=WORK_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=request.effective_timeout_seconds,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=PROCESS_REAP_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        response = {
            "return_code": None,
            "stdout": "",
            "stderr": "Command exceeded the tool safety timeout.",
            "timed_out": True,
        }
        return GUARD.limit_output(response)
    response = {
        "return_code": process.returncode,
        "stdout": stdout.decode(errors="replace")[:MAX_OUTPUT_CHARS],
        "stderr": stderr.decode(errors="replace")[:MAX_OUTPUT_CHARS],
    }
    return GUARD.limit_output(response)


if __name__ == "__main__":
    APP.run(transport="stdio")
