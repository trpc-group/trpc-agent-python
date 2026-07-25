# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SafeCodeExecutor and SafetyWrappedToolSet — safety wrappers for executor and toolset."""

from __future__ import annotations

from typing import List
from typing import Optional

from pydantic import Field

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import CodeExecutionResult
from trpc_agent_sdk.code_executors._types import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext

from ._audit import AuditLogger
from ._filter import add_tool_safety_filter
from ._policy import PolicyConfig
from ._scanner import SafetyScanner
from ._telemetry import set_safety_telemetry
from ._types import Decision
from ._types import SafetyFinding
from ._types import ScanRequest
from ._types import ScanTarget
from ._types import aggregate_decision
from ._types import normalize_language


class SafeCodeExecutor(BaseCodeExecutor):
    """CodeExecutor that scans code blocks before delegating to inner executor.

    Constructor args:
        inner_executor: The wrapped BaseCodeExecutor to delegate to after scan.
        scanner_policy: PolicyConfig instance (defaults to PolicyConfig.default()).
        tool_name: Name used in scan reports (default "CodeExecutor").
        audit_path: If set, audit events are written to this JSONL file.
        block_on_review: If True, NEEDS_HUMAN_REVIEW also blocks. Default False.
    """

    model_config = {"arbitrary_types_allowed": True}

    inner_executor: BaseCodeExecutor = Field(description="Wrapped executor for post-scan delegation.")
    scanner_policy: Optional[PolicyConfig] = Field(default=None, description="PolicyConfig for the scanner.")
    tool_name: str = Field(default="CodeExecutor", description="Name in scan reports.")
    audit_path: Optional[str] = Field(default=None, description="Audit log JSONL path.")
    block_on_review: bool = Field(default=False, description="If True, NEEDS_HUMAN_REVIEW also blocks.")

    async def execute_code(self, invocation_context: InvocationContext,
                           code_execution_input: CodeExecutionInput) -> CodeExecutionResult:
        policy = self.scanner_policy or PolicyConfig.default()
        scanner = SafetyScanner(policy)
        audit = AuditLogger(self.audit_path) if self.audit_path else None
        all_findings: List[SafetyFinding] = []

        for block in code_execution_input.code_blocks:
            lang = normalize_language(block.language or "")
            req = ScanRequest(
                script=block.code,
                language=lang,
                tool_name=self.tool_name,
                target=ScanTarget.CODE_EXECUTOR,
            )
            report = scanner.scan(req)
            all_findings.extend(report.findings)
            if audit:
                audit.record(report)
            set_safety_telemetry(report)

        # Aggregate across all blocks
        combined_decision = aggregate_decision(all_findings)
        should_block = (combined_decision == Decision.DENY
                        or (combined_decision == Decision.NEEDS_HUMAN_REVIEW and self.block_on_review))

        if should_block:
            return create_code_execution_result(
                stderr=f"Code execution blocked by safety guard: {combined_decision.value}", )

        return await self.inner_executor.execute_code(invocation_context, code_execution_input)


class SafetyWrappedToolSet(ToolSetABC):
    """ToolSet wrapper that injects ToolSafetyFilter into dynamically provided tools.

    Constructor args:
        inner: The wrapped ToolSetABC to delegate to after injecting filters.
        policy: PolicyConfig instance (defaults to PolicyConfig.default()).
        audit_path: If set, audit events are written to this JSONL file.
        block_on_review: If True, NEEDS_HUMAN_REVIEW also blocks. Default False.
    """

    def __init__(self,
                 inner: ToolSetABC,
                 policy: Optional[PolicyConfig] = None,
                 audit_path: Optional[str] = None,
                 block_on_review: bool = False) -> None:
        super().__init__(name=getattr(inner, 'name', '') or '')
        self._inner = inner
        self._policy = policy or PolicyConfig.default()
        self._audit_path = audit_path
        self._block_on_review = block_on_review

    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> list:
        """Return tools from inner ToolSet with ToolSafetyFilter injected into each."""
        tools = await self._inner.get_tools(invocation_context)
        add_tool_safety_filter(tools,
                               policy=self._policy,
                               audit_path=self._audit_path,
                               block_on_review=self._block_on_review)
        return tools

    async def close(self) -> None:
        """Delegate close to inner ToolSet."""
        await self._inner.close()
