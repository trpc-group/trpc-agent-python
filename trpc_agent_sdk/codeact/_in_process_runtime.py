# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# This CodeAct implementation is inspired by and adapted from
# NVIDIA NeMo Labs OO Agents (NOOA):
# https://github.com/NVIDIA-NeMo/labs-OO-Agents
#
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unsafe persistent in-process runtime for CodeAct development."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import io
import traceback
from dataclasses import dataclass
from typing import Any
from typing import ClassVar

from pydantic import Field
from pydantic import PrivateAttr
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext

from ._objects import CodeActAgentProxy
from ._objects import CodeActObjectStore
from ._objects import describe_codeact_object
from ._runtime import BaseCodeActRuntime
from ._runtime import CodeActExecutionResult
from ._runtime import CodeActReturnSignal
from ._runtime import create_codeact_execution_result


@dataclass
class _ExecutionState:
    globals: dict[str, Any]
    store: CodeActObjectStore
    agent_proxy: CodeActAgentProxy


class InProcessCodeActRuntime(BaseCodeActRuntime):
    """Run persistent CodeAct control cells in the Agent host process.

    Warning:
        Generated code has the same filesystem, network, environment, and
        process privileges as the Agent host. This runtime is not a sandbox.
    """

    supports_tools: ClassVar[bool] = True

    inline_object_threshold: int = Field(default=256, ge=0)
    object_preview_chars: int = Field(default=240, ge=32)

    _states: dict[str, _ExecutionState] = PrivateAttr(default_factory=dict)
    _execution_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    @override
    async def execute(
        self,
        invocation_context: InvocationContext,
        cells: list[str],
    ) -> CodeActExecutionResult:
        """Execute Python cells while retaining globals for the invocation."""
        execution_id = invocation_context.invocation_id
        state = self._states.get(execution_id)
        if state is None:
            state = self._create_state(invocation_context)
            self._states[execution_id] = state
        await state.agent_proxy.refresh(invocation_context)

        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            async with self._execution_lock:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    for cell in cells:
                        await self._execute_cell(cell, state.globals)
        except CodeActReturnSignal:
            pass
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            stderr.write(traceback.format_exc())

        return create_codeact_execution_result(stdout=stdout.getvalue(), stderr=stderr.getvalue())

    @override
    async def release(self, execution_id: str) -> None:
        """Release globals and live objects retained for one invocation."""
        state = self._states.pop(execution_id, None)
        if state is not None:
            state.store.clear()

    @override
    async def close(self) -> None:
        """Release every retained invocation."""
        for execution_id in list(self._states):
            await self.release(execution_id)

    def has_execution(self, execution_id: str) -> bool:
        """Return whether an invocation currently has persistent REPL state."""
        return execution_id in self._states

    def _create_state(self, context: InvocationContext) -> _ExecutionState:
        store = CodeActObjectStore(
            inline_threshold=self.inline_object_threshold,
            preview_chars=self.object_preview_chars,
        )
        proxy = CodeActAgentProxy(context, store)
        globals_: dict[str, Any] = {
            "__builtins__": __builtins__,
            "__name__": "__codeact__",
            "self": proxy,
            "doc": describe_codeact_object,
            "__trpc_codeact_return_signal": CodeActReturnSignal,
        }
        return _ExecutionState(globals=globals_, store=store, agent_proxy=proxy)

    @staticmethod
    async def _execute_cell(code: str, globals_: dict[str, Any]) -> None:
        compiled = compile(
            code,
            "<codeact>",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            dont_inherit=True,
        )
        result = eval(compiled, globals_)  # pylint: disable=eval-used
        if inspect.isawaitable(result):
            await result
