# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Demonstrate that ToolSafetyFilter blocks a high-risk tool BEFORE it runs.

A tool sets ``executed = True`` the moment its body runs. With the
``tool_safety_guard`` filter attached, a dangerous command is denied and the
body never runs (``executed`` stays False), while a safe command runs normally.
Each attempt also writes one auditable event.

Run::

    python examples/tool_safety_guard/run_with_filter.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
# Load the example policy and write the demo audit log next to this file.
os.environ.setdefault("TOOL_SAFETY_POLICY_PATH", str(HERE / "tool_safety_policy.yaml"))
AUDIT_PATH = HERE / "filter_demo_audit.jsonl"
os.environ.setdefault("TOOL_SAFETY_AUDIT_PATH", str(AUDIT_PATH))

from trpc_agent_sdk.context import InvocationContext  # noqa: E402
from trpc_agent_sdk.tools._base_tool import BaseTool  # noqa: E402
import trpc_agent_sdk.tools.safety.filter  # noqa: E402,F401  (registers tool_safety_guard)


class DemoBashTool(BaseTool):
    """A stand-in tool that records whether its body actually executed."""

    def __init__(self) -> None:
        super().__init__(name="Bash", description="demo bash tool",
                         filters_name=["tool_safety_guard"])
        self.executed = False
        self.last_command: Any = None

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        self.executed = True  # side-effect marker: proves the body ran
        self.last_command = args.get("command")
        return {"success": True, "ran": args.get("command")}


def _make_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.before_tool_callback = None
    ctx.agent.after_tool_callback = None
    return ctx


async def main() -> int:
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()

    # 1) Dangerous command -> must be blocked before execution.
    dangerous = DemoBashTool()
    result = await dangerous.run_async(tool_context=_make_context(), args={"command": "rm -rf /"})
    print("dangerous command : 'rm -rf /'")
    print(f"  executed body?  -> {dangerous.executed}  (expected: False)")
    print(f"  blocked result  -> {result.get('blocked')}  decision={result.get('safety', {}).get('decision')}")

    # 2) Safe command -> runs normally.
    safe = DemoBashTool()
    result2 = await safe.run_async(tool_context=_make_context(), args={"command": "ls -la"})
    print("\nsafe command      : 'ls -la'")
    print(f"  executed body?  -> {safe.executed}  (expected: True)")
    print(f"  result          -> {result2}")

    # Show the auditable events recorded for both attempts.
    print(f"\naudit log: {AUDIT_PATH}")
    if AUDIT_PATH.exists():
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            print(f"  decision={record['decision']:>18} blocked={record['blocked']} "
                  f"rule={record['rule_id']}")

    ok = (dangerous.executed is False) and (safe.executed is True)
    print("\nDEMO PASSED" if ok else "\nDEMO FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
