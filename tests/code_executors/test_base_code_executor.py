# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing_extensions import override
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeBlockDelimiter
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome


class ConcreteCodeExecutor(BaseCodeExecutor):
    """Concrete implementation of BaseCodeExecutor for testing."""

    @override
    async def execute_code(self, invocation_context: InvocationContext,
                           code_execution_input: CodeExecutionInput) -> CodeExecutionResult:
        """Concrete implementation of execute_code."""
        return create_code_execution_result(stdout="test output")

    @override
    def code_block_delimiter(self) -> CodeBlockDelimiter:
        """Concrete implementation of code_block_delimiter."""
        return CodeBlockDelimiter(start="```python\n", end="\n```")


class TestCodeExecutionInput:
    """Test suite for CodeExecutionInput class."""

    def test_create_code_execution_input(self):
        """Test creating code execution input."""
        code_blocks = [CodeBlock(language="python", code="print('hello')")]
        input_files = [CodeFile(name="test.txt", content="test", mime_type="text/plain")]

        execution_input = CodeExecutionInput(
            code_blocks=code_blocks,
            code="print('hello')",
            input_files=input_files,
            execution_id="exec-123",
        )

        assert len(execution_input.code_blocks) == 1
        assert execution_input.code == "print('hello')"
        assert len(execution_input.input_files) == 1
        assert execution_input.execution_id == "exec-123"

    def test_create_code_execution_input_defaults(self):
        """Test creating code execution input with defaults."""
        execution_input = CodeExecutionInput()

        assert execution_input.code_blocks == []
        assert execution_input.code == ""
        assert execution_input.input_files == []
        assert execution_input.execution_id is None


class TestCodeExecutionResponse:
    """Test suite for CodeExecutionResponse class."""

    def test_create_code_execution_response(self):
        """Test creating code execution response."""
        output_files = [CodeFile(name="output.txt", content="output", mime_type="text/plain")]

        result = CodeExecutionResult(
            outcome=Outcome.OUTCOME_OK,
            output="hello\nworld",
        )

        assert result.outcome == Outcome.OUTCOME_OK
        assert result.output == "hello\nworld"

    def test_create_code_execution_result_defaults(self):
        """Test creating code execution result with defaults."""
        result = CodeExecutionResult()

        assert result.outcome is None
        assert result.output is None


class TestBaseCodeExecutor:
    """Test suite for BaseCodeExecutor class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseCodeExecutor cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseCodeExecutor()

    def test_concrete_executor_instantiation(self):
        """Test that concrete executor can be instantiated."""
        executor = ConcreteCodeExecutor()

        assert isinstance(executor, BaseCodeExecutor)
        assert executor.optimize_data_file is False
        assert executor.stateful is False
        assert executor.error_retry_attempts == 2

    def test_concrete_executor_defaults(self):
        """Test concrete executor default values."""
        executor = ConcreteCodeExecutor()

        assert executor.optimize_data_file is False
        assert executor.stateful is False
        assert executor.error_retry_attempts == 2
        assert len(executor.code_block_delimiters) == 2
        assert len(executor.execution_result_delimiters) == 1
        assert executor.workspace_runtime is None

    def test_concrete_executor_custom_values(self):
        """Test concrete executor with custom values."""
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        custom_delimiters = [
            CodeBlockDelimiter(start="```bash\n", end="\n```"),
        ]

        executor = ConcreteCodeExecutor(
            optimize_data_file=True,
            stateful=True,
            error_retry_attempts=5,
            code_block_delimiters=custom_delimiters,
            workspace_runtime=mock_runtime,
        )

        assert executor.optimize_data_file is True
        assert executor.stateful is True
        assert executor.error_retry_attempts == 5
        assert len(executor.code_block_delimiters) == 1
        assert executor.workspace_runtime == mock_runtime

    @pytest.mark.asyncio
    async def test_execute_code_implementation(self):
        """Test execute_code method implementation."""
        executor = ConcreteCodeExecutor()
        mock_ctx = Mock(spec=InvocationContext)
        execution_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('test')")])

        result = await executor.execute_code(mock_ctx, execution_input)

        assert isinstance(result, CodeExecutionResult)
        assert result.outcome == Outcome.OUTCOME_OK
        assert result.output == "Code execution result:\ntest output\n"

    def test_code_block_delimiter_implementation(self):
        """Test code_block_delimiter method implementation."""
        executor = ConcreteCodeExecutor()

        delimiter = executor.code_block_delimiter()

        assert isinstance(delimiter, CodeBlockDelimiter)
        assert delimiter.start == "```python\n"
        assert delimiter.end == "\n```"

    def test_code_block_delimiters_default(self):
        """Test default code block delimiters."""
        executor = ConcreteCodeExecutor()

        assert len(executor.code_block_delimiters) == 2
        assert executor.code_block_delimiters[0].start == "```tool_code\n"
        assert executor.code_block_delimiters[0].end == "\n```"
        assert executor.code_block_delimiters[1].start == "```python\n"
        assert executor.code_block_delimiters[1].end == "\n```"

    def test_execution_result_delimiters_default(self):
        """Test default execution result delimiters."""
        executor = ConcreteCodeExecutor()

        assert len(executor.execution_result_delimiters) == 1
        assert executor.execution_result_delimiters[0].start == "```tool_output\n"
        assert executor.execution_result_delimiters[0].end == "\n```"

    def test_workspace_runtime_assignment(self):
        """Test assigning workspace runtime."""
        executor = ConcreteCodeExecutor()
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)

        executor.workspace_runtime = mock_runtime

        assert executor.workspace_runtime == mock_runtime
