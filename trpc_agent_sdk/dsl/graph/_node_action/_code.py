# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code node action executor."""

from typing import Any
from typing import Optional

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.context import InvocationContext

from .._constants import STATE_KEY_LAST_RESPONSE
from .._constants import STATE_KEY_NODE_RESPONSES
from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._state import State
from ._base import BaseNodeAction


class CodeNodeAction(BaseNodeAction):
    """Execute static code and map execution output to state."""

    def __init__(
        self,
        name: str,
        code_executor: BaseCodeExecutor,
        code: str,
        language: str,
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
    ):
        super().__init__(name, writer, async_writer, ctx)
        self.code = code
        self.language = language
        self.executor = code_executor

    async def execute(self, state: State) -> dict[str, Any]:
        del state
        if self.ctx is None:
            raise RuntimeError(
                f"Code node '{self.name}' requires InvocationContext but none was set. "
                "Pass context via config['configurable']['invocation_context'] when executing the graph.")

        result = await self.executor.execute_code(
            self.ctx,
            CodeExecutionInput(
                code_blocks=[CodeBlock(language=self.language, code=self.code)],
                execution_id=self.ctx.session.id,
            ),
        )
        output_text = result.output or ""
        state_update: dict[str, Any] = {
            STATE_KEY_LAST_RESPONSE: output_text,
            STATE_KEY_NODE_RESPONSES: {
                self.name: output_text
            },
        }
        return state_update
