# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Delegating CodeExecutor wrapper that scans each code block before run.

Usage:
    guarded = SafetyGuardedCodeExecutor(delegate=UnsafeLocalCodeExecutor())
The delegate's execute_code only receives blocks the guard allowed.
"""
from __future__ import annotations

import os
from typing import Optional
from typing_extensions import override

from pydantic import PrivateAttr

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext

from trpc_agent_sdk.tools.safety._audit import record_safety_decision
from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._scanner import scan
from trpc_agent_sdk.tools.safety._types import Decision


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Wraps a delegate executor; blocks unsafe code blocks pre-execution."""

    block_on_review: bool = True
    _delegate: BaseCodeExecutor = PrivateAttr()
    _policy: Optional[Policy] = PrivateAttr(default=None)

    def __init__(self,
                 delegate: BaseCodeExecutor,
                 policy: Optional[Policy] = None,
                 block_on_review: bool = True) -> None:
        super().__init__(block_on_review=block_on_review)
        self._delegate = delegate
        self._policy = policy

    def _ensure_policy(self) -> Policy:
        if self._policy is None:
            self._policy = load_policy(os.environ.get("TRPC_AGENT_TOOL_SAFETY_POLICY"))
        return self._policy

    @override
    async def execute_code(self, invocation_context: InvocationContext,
                           input_data: CodeExecutionInput) -> CodeExecutionResult:
        if not input_data.code_blocks and input_data.code:
            input_data.code_blocks = [CodeBlock(code=input_data.code, language="python")]

        kept: list[CodeBlock] = []
        blocked_msgs: list[str] = []
        for block in input_data.code_blocks:
            report = scan(self._ensure_policy(), block.code, language=block.language or "auto")
            decision = report.decision
            block_allowed = (decision == Decision.ALLOW
                             or (decision == Decision.NEEDS_REVIEW and not self.block_on_review))
            # Audit each scanned block (issue #90): tool name, decision, risk,
            # rule ids, duration, sanitized, intercepted.
            record_safety_decision(
                report,
                tool_name="code_executor",
                language=block.language or "auto",
                intercepted=not block_allowed,
            )
            if block_allowed:
                kept.append(block)
            else:
                ids = ",".join(sorted({f.rule_id for f in report.findings}))
                blocked_msgs.append(f"TOOL_SAFETY_BLOCKED [{block.language}] {decision.name} ({ids})")

        out_parts: list[str] = []
        err_parts: list[str] = []
        if kept:
            safe_input = input_data.model_copy(update={"code_blocks": kept})
            result = await self._delegate.execute_code(invocation_context, safe_input)
            if result.output:
                out_parts.append(result.output)
        if blocked_msgs:
            err_parts.append("Blocked code blocks:\n" + "\n".join(blocked_msgs))

        return create_code_execution_result(
            stdout="\n".join(out_parts),
            stderr="\n".join(err_parts),
        )
