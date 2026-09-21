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
"""Runtime contracts for model-generated CodeAct Python cells."""

from __future__ import annotations

import abc
from typing import ClassVar

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome


class CodeActReturnSignal(BaseException):
    """Internal control-flow signal raised after ``return_result``."""


class CodeActExecutionResult(BaseModel):
    """Result of executing one batch of CodeAct Python cells."""

    outcome: Outcome = Outcome.OUTCOME_UNSPECIFIED
    output: str = ""


def create_codeact_execution_result(
    *,
    stdout: str = "",
    stderr: str = "",
) -> CodeActExecutionResult:
    """Build a model-visible CodeAct execution result."""
    output = ""
    outcome = Outcome.OUTCOME_OK
    if stderr:
        output = f"Code execution error:\n{stderr}\n"
        outcome = Outcome.OUTCOME_FAILED
    if stdout:
        output += f"Code execution result:\n{stdout}\n"
    return CodeActExecutionResult(outcome=outcome, output=output)


class BaseCodeActRuntime(BaseModel, abc.ABC):
    """Execute CodeAct control cells independently from user code executors."""

    model_config = {"arbitrary_types_allowed": True}

    supports_tools: ClassVar[bool] = False
    """Whether cells can call configured Agent tools through ``self``."""

    @abc.abstractmethod
    async def execute(
        self,
        invocation_context: InvocationContext,
        cells: list[str],
    ) -> CodeActExecutionResult:
        """Execute Python cells for one invocation."""

    async def release(self, execution_id: str) -> None:
        """Release state retained for one invocation."""

    async def close(self) -> None:
        """Release all resources retained by this runtime."""
