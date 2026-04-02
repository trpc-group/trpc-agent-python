# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import ContainerCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.utils import CommandExecResult


class TestContainerCodeExecutor:
    """Test suite for ContainerCodeExecutor class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.mock_ctx = Mock(spec=InvocationContext)

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_init_with_image(self, mock_container_client_class):
        """Test initialization with image."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        executor = ContainerCodeExecutor(image="python:3-slim")

        assert executor.image == "python:3-slim"
        assert executor.docker_path is None
        assert executor.stateful is False
        assert executor.optimize_data_file is False
        mock_container_client_class.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_init_with_docker_path(self, mock_container_client_class):
        """Test initialization with docker_path."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        executor = ContainerCodeExecutor(docker_path="/path/to/dockerfile")

        assert executor.docker_path == "/path/to/dockerfile"
        assert executor.image is None
        mock_container_client_class.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_init_neither_image_nor_docker_path(self, mock_container_client_class):
        """Test initialization fails when neither image nor docker_path is set."""
        with pytest.raises(ValueError, match="Either image or docker_path must be set"):
            ContainerCodeExecutor()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_init_cannot_set_stateful(self, mock_container_client_class):
        """Test initialization fails when stateful is set to True."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        with pytest.raises(ValueError, match="Cannot set `stateful=True`"):
            ContainerCodeExecutor(image="python:3-slim", stateful=True)

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_init_cannot_set_optimize_data_file(self, mock_container_client_class):
        """Test initialization fails when optimize_data_file is set to True."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        with pytest.raises(ValueError, match="Cannot set `optimize_data_file=True`"):
            ContainerCodeExecutor(image="python:3-slim", optimize_data_file=True)

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_python(self, mock_container_client_class):
        """Test executing Python code."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method to return CommandExecResult
        mock_container_client.exec_run = AsyncMock(
            return_value=CommandExecResult(stdout="hello\nworld", stderr="", exit_code=0, is_timeout=False))

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code="print('hello')\nprint('world')")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert isinstance(result, CodeExecutionResult)
        assert "hello" in result.output
        assert "world" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        mock_container_client.exec_run.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_bash(self, mock_container_client_class):
        """Test executing Bash code."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method to return CommandExecResult
        mock_container_client.exec_run = AsyncMock(
            return_value=CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False))

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="echo output")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert isinstance(result, CodeExecutionResult)
        assert "output" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        mock_container_client.exec_run.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_multiple_blocks(self, mock_container_client_class):
        """Test executing multiple code blocks."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method to return CommandExecResult
        mock_container_client.exec_run = AsyncMock(side_effect=[
            CommandExecResult(stdout="first", stderr="", exit_code=0, is_timeout=False),
            CommandExecResult(stdout="second", stderr="", exit_code=0, is_timeout=False),
        ])

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('first')"),
            CodeBlock(language="python", code="print('second')"),
        ])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "first" in result.output
        assert "second" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        assert mock_container_client.exec_run.call_count == 2

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_with_stderr(self, mock_container_client_class):
        """Test executing code that produces stderr."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method to return CommandExecResult with stderr and non-zero exit code
        # Only when exit_code != 0, stderr is added to all_errors
        mock_container_client.exec_run = AsyncMock(
            return_value=CommandExecResult(stdout="output", stderr="error", exit_code=1, is_timeout=False))

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('output')")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "error" in result.output
        assert result.outcome == Outcome.OUTCOME_FAILED

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_unsupported_language(self, mock_container_client_class):
        """Test executing code with unsupported language."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="javascript", code="console.log('hello')")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "unsupported language" in result.output
        assert result.outcome == Outcome.OUTCOME_FAILED
        # exec_run should not be called for unsupported languages
        mock_container_client.exec_run.assert_not_called()

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_exception_handling(self, mock_container_client_class):
        """Test handling exceptions during code execution."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method that raises exception
        mock_container_client.exec_run = AsyncMock(side_effect=Exception("Container error"))

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('hello')")])

        with pytest.raises(Exception, match="Container error"):
            await executor.execute_code(self.mock_ctx, code_input)

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    def test_code_block_delimiter(self, mock_container_client_class):
        """Test code_block_delimiter method."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        executor = ContainerCodeExecutor(image="python:3-slim")
        delimiter = executor.code_block_delimiter()

        assert delimiter.start == "```tool_code\n"
        assert delimiter.end == "\n```"

    @patch('trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient')
    async def test_execute_code_empty_language_defaults_to_python(self, mock_container_client_class):
        """Test executing code with empty language defaults to Python."""
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        # Mock exec_run as async method to return CommandExecResult
        mock_container_client.exec_run = AsyncMock(
            return_value=CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False))

        executor = ContainerCodeExecutor(image="python:3-slim")
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="", code="print('output')")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "output" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        # Verify python3 command was used
        # For AsyncMock, use await_args instead of call_args
        call_args = mock_container_client.exec_run.await_args
        # exec_run is called with (cmd=..., command_args=...)
        cmd_arg = call_args.kwargs.get("cmd") if call_args and call_args.kwargs else (
            call_args.args[0] if call_args and call_args.args else None)
        assert cmd_arg is not None
        assert "python3" in str(cmd_arg)
