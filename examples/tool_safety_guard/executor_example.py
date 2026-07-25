# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Minimal CodeExecutor wrapper integration example."""

from pathlib import Path

from trpc_agent_sdk.code_executors import ContainerCodeExecutor
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanner

EXAMPLE_DIR = Path(__file__).resolve().parent
policy = ToolSafetyPolicy.from_yaml(EXAMPLE_DIR / "tool_safety_policy.yaml")

# Static scanning is an additional boundary, not a sandbox replacement. The
# delegated executor should still enforce network, filesystem, process, and
# resource isolation.
sandbox_executor = ContainerCodeExecutor(
    image="python:3.12-slim",
    timeout=policy.max_timeout_seconds,
)
guarded_executor = SafetyGuardedCodeExecutor(
    executor=sandbox_executor,
    scanner=ToolSafetyScanner(policy),
    audit_sink=JsonlAuditSink(EXAMPLE_DIR / "tool_safety_audit.jsonl"),
)
