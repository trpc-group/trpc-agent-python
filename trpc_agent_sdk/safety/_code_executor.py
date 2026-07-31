# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Composition wrapper that scans a complete code batch before delegation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome

from ._models import SafetyDecision
from ._models import SafetyScanRequest
from ._scanner import SafetyScanner
from ._tool_filter import blocked_envelope


class SafetyCodeExecutor(BaseCodeExecutor):
    """Scan every block, then either delegate once or return a blocked result."""

    inner: BaseCodeExecutor = Field(exclude=True, repr=False)
    scanner: SafetyScanner = Field(exclude=True, repr=False)

    def __init__(self, *, inner: BaseCodeExecutor, scanner: SafetyScanner, **data: Any):
        data.setdefault("optimize_data_file", inner.optimize_data_file)
        data.setdefault("stateful", inner.stateful)
        data.setdefault("error_retry_attempts", inner.error_retry_attempts)
        data.setdefault("execute_once_per_invocation", inner.execute_once_per_invocation)
        data.setdefault("code_block_delimiters", list(inner.code_block_delimiters))
        data.setdefault("execution_result_delimiters", list(inner.execution_result_delimiters))
        data.setdefault("workspace_runtime", inner.workspace_runtime)
        data.setdefault("ignore_codes", list(inner.ignore_codes))
        super().__init__(inner=inner, scanner=scanner, **data)

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language="python", code=code_execution_input.code)]

        reports = []
        for index, block in enumerate(blocks):
            reports.append(
                self.scanner.scan(
                    SafetyScanRequest(
                        script=block.code,
                        language=block.language or "python",
                        source_type="code_executor",
                        source_name=f"code_block_{index}",
                        block_index=index,
                        invocation_id=getattr(invocation_context, "invocation_id", None),
                        session_id=getattr(invocation_context, "session_id", None),
                    )))

        blocked = next((item for item in reports if item.decision is SafetyDecision.DENY), None)
        if blocked is None:
            blocked = next(
                (item for item in reports if item.decision is SafetyDecision.NEEDS_HUMAN_REVIEW),
                None,
            )
        if blocked is not None:
            output = json.dumps(blocked_envelope(blocked), ensure_ascii=False, allow_nan=False, sort_keys=True)
            return CodeExecutionResult(
                outcome=Outcome.OUTCOME_FAILED,
                output=output,
                id=code_execution_input.execution_id,
            )
        return await self.inner.execute_code(invocation_context, code_execution_input)
