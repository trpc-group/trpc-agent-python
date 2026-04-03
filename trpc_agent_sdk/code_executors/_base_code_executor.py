# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base code executor for TRPC Agent framework.

This module provides the abstract base class for all code executors.
The code executor allows the agent to execute code blocks from model responses
and incorporate the execution results into the final response.
"""

from __future__ import annotations

import abc
from typing import Optional

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext

from ._base_workspace_runtime import BaseWorkspaceRuntime
from ._types import CodeBlockDelimiter
from ._types import CodeExecutionInput
from ._types import CodeExecutionResult


class BaseCodeExecutor(BaseModel):
    """Abstract base class for all code executors.

    The code executor allows the agent to execute code blocks from model responses
    and incorporate the execution results into the final response.

    Attributes:
        optimize_data_file: If true, extract and process data files from the model
            request and attach them to the code executor. Supported data file
            MimeTypes are [text/csv]. Default to False.
        stateful: Whether the code executor is stateful. Default to False.
        error_retry_attempts: The number of attempts to retry on consecutive code
            execution errors. Default to 2.
        code_block_delimiters: The list of the enclosing delimiters to identify the
            code blocks.
        execution_result_delimiters: The delimiters to format the code execution
            result.
    """

    model_config = {"arbitrary_types_allowed": True}

    optimize_data_file: bool = False
    """If true, extract and process data files from the model request
    and attach them to the code executor.

    Supported data file MimeTypes are [text/csv].
    Default to False.
    """

    stateful: bool = False
    """Whether the code executor is stateful. Default to False."""

    error_retry_attempts: int = 2
    """The number of attempts to retry on consecutive code execution errors. Default to 2."""

    code_block_delimiters: list[CodeBlockDelimiter] = [
        CodeBlockDelimiter(start="```tool_code\n", end="\n```"),
        CodeBlockDelimiter(start="```python\n", end="\n```"),
    ]
    """The list of the enclosing delimiters to identify the code blocks.

    For example, the delimiter ('```python\\n', '\\n```') can be
    used to identify code blocks with the following format::

        ```python
        print("hello")
        ```
    """

    execution_result_delimiters: list[CodeBlockDelimiter] = [
        CodeBlockDelimiter(start="```tool_output\n", end="\n```"),
    ]
    """The delimiters to format the code execution result."""

    workspace_runtime: Optional[BaseWorkspaceRuntime] = None
    """The workspace runtime for the code execution."""

    @abc.abstractmethod
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Executes code and return the code execution result.

        Args:
            invocation_context: The invocation context of the code execution.
            code_execution_input: The code execution input.

        Returns:
            The code execution result.
        """

    @abc.abstractmethod
    def code_block_delimiter(self) -> CodeBlockDelimiter:
        """Return the code block delimiter used by this executor.

        Returns:
            CodeBlockDelimiter instance
        """
