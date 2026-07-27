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


def _safe_response(response: dict) -> dict:
    """Redact secret-looking output before returning it to the Agent."""
    safe = dict(response)
    redacted = bool(safe.get("redacted", False))
    for key in ("stdout", "stderr", "output", "formatted_output"):
        item = safe.get(key)
        if not isinstance(item, str):
            continue
        safe_text, changed = GUARD.sanitizer.sanitize(item)
        safe[key] = safe_text
        redacted = redacted or changed
    if redacted:
        safe["redacted"] = True
    return GUARD.limit_output(safe)


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
    timeout_limit = float(GUARD.policy.max_timeout_seconds)
    requested_or_default = requested_timeout if requested_timeout is not None else timeout_limit
    effective_timeout = min(
        requested_or_default,
        timeout_limit,
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
        reap_timed_out = False
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(
                process.communicate(),
                timeout=PROCESS_REAP_TIMEOUT_SECONDS,
            )
        except Exception:
            reap_timed_out = True
        response = {
            "return_code": None,
            "stdout": "",
            "stderr": "Command exceeded the tool safety timeout.",
            "timed_out": True,
            "reap_timed_out": reap_timed_out,
        }
        return _safe_response(response)
    response = {
        "return_code": process.returncode,
        "stdout": stdout.decode(errors="replace")[:MAX_OUTPUT_CHARS],
        "stderr": stderr.decode(errors="replace")[:MAX_OUTPUT_CHARS],
    }
    return _safe_response(response)


if __name__ == "__main__":
    APP.run(transport="stdio")
