# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A ``BaseCodeExecutor`` decorator that scans code blocks before execution.

``SafeCodeExecutor`` wraps *any* :class:`BaseCodeExecutor`. Each code block is
scanned first; if any block is blocked the wrapper returns a failed
:class:`CodeExecutionResult` and the inner executor is never invoked. This is
the second, complementary integration point to the tool filter — same scanner,
same audit, different attachment surface, again with zero core intrusion.

Note: this is a *static* pre-execution guard, not a sandbox. It reduces risk
but does not replace OS/container isolation (see the README).
"""

from __future__ import annotations

from pydantic import Field
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext

from ._audit import SafetyAuditLogger
from ._scanner import SafetyScanner
from ._types import SafetyDecision
from ._types import ScanInput
from ._types import ScanReport
from ._types import ScriptLanguage


class SafeCodeExecutor(BaseCodeExecutor):
    """Wrap an executor with a pre-execution safety scan of each code block."""

    inner: BaseCodeExecutor
    """The wrapped executor that actually runs the code."""

    scanner: SafetyScanner = Field(default_factory=SafetyScanner)
    """Scanner used to vet each code block; defaults to the bundled policy."""

    audit_logger: SafetyAuditLogger = Field(default_factory=SafetyAuditLogger)
    """Audit sink for the resulting decisions."""

    block_on_review: bool = True
    """Whether ``needs_human_review`` also blocks; True is fail-safe."""

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan every code block; delegate to the inner executor if all pass.

        Args:
            invocation_context: The invocation context of the code execution.
            code_execution_input: The blocks (and/or raw code) to execute.

        Returns:
            A failed :class:`CodeExecutionResult` describing the block if any
            code is denied, otherwise the inner executor's result.
        """
        for scan_input in self._iter_scan_inputs(code_execution_input):
            report = self.scanner.scan(scan_input)
            blocked = self._is_blocking(report.decision)
            self.audit_logger.record(report, blocked=blocked)
            if blocked:
                return create_code_execution_result(stderr=self._deny_message(report))

        return await self.inner.execute_code(invocation_context, code_execution_input)

    # -- helpers ---------------------------------------------------------------
    def _is_blocking(self, decision: SafetyDecision) -> bool:
        """Whether a decision should prevent execution under this wrapper."""
        if decision is SafetyDecision.DENY:
            return True
        if decision is SafetyDecision.NEEDS_HUMAN_REVIEW:
            return self.block_on_review
        return False

    @staticmethod
    def _iter_scan_inputs(code_execution_input: CodeExecutionInput):
        """Yield a :class:`ScanInput` per code block (or the raw code field)."""
        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            yield ScanInput(
                script=code_execution_input.code,
                language=ScriptLanguage.UNKNOWN,
                tool_name="code_executor",
            )
            return
        for block in blocks:
            if not block.code.strip():
                continue
            yield ScanInput(
                script=block.code,
                language=ScriptLanguage.from_str(block.language),
                tool_name="code_executor",
            )

    @staticmethod
    def _deny_message(report: ScanReport) -> str:
        """Build the stderr message returned when a code block is blocked."""
        rule_ids = ", ".join(report.rule_ids()) or "<none>"
        return (f"SAFETY_BLOCKED [{report.decision.value}]: {report.summary} "
                f"Triggered rules: {rule_ids}. Execution was prevented by the "
                f"Tool Safety Guard.")
