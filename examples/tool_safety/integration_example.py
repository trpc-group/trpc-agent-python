# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Non-executing callable/Skill-boundary safety integration example."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import gettempdir

from trpc_agent_sdk.safety import CallbackMonitorSink
from trpc_agent_sdk.safety import JsonlAuditSink
from trpc_agent_sdk.safety import PolicyLoader
from trpc_agent_sdk.safety import SafetyCallable
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner


def request_factory(args, kwargs):
    """Map only the callable's documented script field to a scan request."""
    script = kwargs.get("script") if "script" in kwargs else args[0]
    language = kwargs.get("language", "python")
    return SafetyScanRequest(
        script=script,
        language=language,
        tool_name="submit_script",
        source_type="skill_callable",
    )


async def submit_script(script: str, language: str = "python") -> dict[str, str]:
    """Acknowledge a submission; this demonstration never executes source."""
    del script
    return {"status": "accepted", "language": language}


def monitor(event) -> None:
    """Consume only the immutable, redacted observation."""
    print(f"safety decision: {getattr(event, 'decision', 'health')}")


async def main() -> None:
    root = Path(__file__).resolve().parent
    policy = PolicyLoader(root / "tool_safety_policy.yaml").load()
    audit = JsonlAuditSink(Path(gettempdir()) / "trpc-tool-safety-example.jsonl")
    scanner = SafetyScanner(
        policy,
        audit_sink=audit,
        monitor_sinks=(CallbackMonitorSink(monitor), ),
    )
    protected = SafetyCallable(submit_script, scanner, request_factory)
    result = await protected(script="print('hello')", language="python")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
