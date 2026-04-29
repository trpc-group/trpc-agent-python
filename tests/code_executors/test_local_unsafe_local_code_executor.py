# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors._base_code_executor import CodeExecutionResult
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeBlockDelimiter
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.utils import CommandExecResult


class TestUnsafeLocalCodeExecutor:
    """Test suite for UnsafeLocalCodeExecutor class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.mock_ctx = Mock(spec=InvocationContext)

    def test_init_defaults(self):
        """Test initialization with defaults."""
        executor = UnsafeLocalCodeExecutor()

        assert executor.stateful is False
        assert executor.optimize_data_file is False
        assert executor.work_dir == ""
        assert executor.timeout == 0
        assert executor.clean_temp_files is True

    def test_init_cannot_set_stateful(self):
        """Test initialization fails when stateful is set to True."""
        with pytest.raises(ValueError, match="Cannot set `stateful=True`"):
            UnsafeLocalCodeExecutor(stateful=True)

    def test_init_cannot_set_optimize_data_file(self):
        """Test initialization fails when optimize_data_file is set to True."""
        with pytest.raises(ValueError, match="Cannot set `optimize_data_file=True`"):
            UnsafeLocalCodeExecutor(optimize_data_file=True)

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        executor = UnsafeLocalCodeExecutor(
            work_dir="/tmp/work",
            timeout=30.0,
            clean_temp_files=False,
        )

        assert executor.work_dir == "/tmp/work"
        assert executor.timeout == 30.0
        assert executor.clean_temp_files is False

    def test_code_block_delimiters(self):
        """Test default code_block_delimiters value."""
        executor = UnsafeLocalCodeExecutor()
        delimiters = executor.code_block_delimiters

        assert isinstance(delimiters, list)
        assert all(isinstance(delimiter, CodeBlockDelimiter) for delimiter in delimiters)

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_python(self, mock_async_execute):
        """Test executing Python code."""
        mock_async_execute.return_value = CommandExecResult(stdout="hello\nworld",
                                                            stderr="",
                                                            exit_code=0,
                                                            is_timeout=False)

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code="print('hello')\nprint('world')")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert isinstance(result, CodeExecutionResult)
        assert "hello" in result.output
        assert "world" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        mock_async_execute.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_bash(self, mock_async_execute):
        """Test executing Bash code."""
        mock_async_execute.return_value = CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False)

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="echo output")])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "output" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        mock_async_execute.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_from_code_field(self, mock_async_execute):
        """Test executing code from code field when no code_blocks."""
        mock_async_execute.return_value = CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False)

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code="print('output')")

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "output" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        assert len(code_input.code_blocks) == 1
        assert code_input.code_blocks[0].language == "python"

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_multiple_blocks(self, mock_async_execute):
        """Test executing multiple code blocks."""
        mock_async_execute.side_effect = [
            CommandExecResult(stdout="first", stderr="", exit_code=0, is_timeout=False),
            CommandExecResult(stdout="second", stderr="", exit_code=0, is_timeout=False),
        ]

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('first')"),
            CodeBlock(language="python", code="print('second')"),
        ])

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "first" in result.output
        assert "second" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        assert mock_async_execute.call_count == 2

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_with_execution_id(self, mock_async_execute):
        """Test executing code with execution_id."""
        mock_async_execute.return_value = CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False)

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('output')")],
                                        execution_id="exec-123")

        result = await executor.execute_code(self.mock_ctx, code_input)

        assert "output" in result.output
        assert result.outcome == Outcome.OUTCOME_OK

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_with_work_dir(self, mock_async_execute):
        """Test executing code with custom work directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_async_execute.return_value = CommandExecResult(stdout="output",
                                                                stderr="",
                                                                exit_code=0,
                                                                is_timeout=False)

            executor = UnsafeLocalCodeExecutor(work_dir=tmpdir)
            code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('output')")])

            result = await executor.execute_code(self.mock_ctx, code_input)

            assert "output" in result.output
            assert result.outcome == Outcome.OUTCOME_OK
            # Verify work_dir was used (first keyword argument)
            call_kwargs = mock_async_execute.call_args[1]
            assert tmpdir in str(call_kwargs.get('work_dir', ''))

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_subprocess_error(self, mock_async_execute):
        """Test handling subprocess errors."""
        # Simulate command failure with non-zero return code
        mock_async_execute.return_value = CommandExecResult(stdout="",
                                                            stderr="command failed",
                                                            exit_code=1,
                                                            is_timeout=False)

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('output')")])

        result = await executor.execute_code(self.mock_ctx, code_input)
        assert "command failed" in result.output
        assert "failed" in result.output.lower()
        assert result.outcome == Outcome.OUTCOME_FAILED

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    async def test_execute_code_unsupported_language(self, mock_async_execute):
        """Test executing code with unsupported language."""
        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="javascript", code="console.log('hello')")])

        result = await executor.execute_code(self.mock_ctx, code_input)
        assert "unsupported language" in result.output
        assert result.outcome == Outcome.OUTCOME_FAILED

    def test_prepare_work_dir_with_work_dir(self):
        """Test _prepare_work_dir with configured work_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = UnsafeLocalCodeExecutor(work_dir=tmpdir)
            work_path, should_cleanup = executor._prepare_work_dir("exec-123")

            assert work_path == Path(tmpdir)
            assert should_cleanup is False

    def test_prepare_work_dir_temp(self):
        """Test _prepare_work_dir creates temporary directory."""
        executor = UnsafeLocalCodeExecutor()
        work_path, should_cleanup = executor._prepare_work_dir("exec-123")

        assert work_path.exists()
        assert should_cleanup == executor.clean_temp_files
        # Cleanup
        if should_cleanup:
            import shutil
            shutil.rmtree(work_path, ignore_errors=True)

    def test_prepare_code_file_python(self):
        """Test _prepare_code_file for Python."""
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = UnsafeLocalCodeExecutor()
            work_dir = Path(tmpdir)
            block = CodeBlock(language="python", code="print('hello')")

            file_path = executor._prepare_code_file(work_dir, block, 0)

            assert file_path.exists()
            assert file_path.suffix == ".py"
            assert file_path.read_text() == "print('hello')"

    def test_prepare_code_file_bash(self):
        """Test _prepare_code_file for Bash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = UnsafeLocalCodeExecutor()
            work_dir = Path(tmpdir)
            block = CodeBlock(language="bash", code="echo hello")

            file_path = executor._prepare_code_file(work_dir, block, 0)

            assert file_path.exists()
            assert file_path.suffix == ".sh"
            assert file_path.read_text() == "echo hello"

    def test_build_command_args_python(self):
        """Test _build_command_args for Python."""
        executor = UnsafeLocalCodeExecutor()
        file_path = Path("/tmp/code_0.py")

        args = executor._build_command_args("python", file_path)

        assert args == ["python3", str(file_path)]

    def test_build_command_args_bash(self):
        """Test _build_command_args for Bash."""
        executor = UnsafeLocalCodeExecutor()
        file_path = Path("/tmp/code_0.sh")

        args = executor._build_command_args("bash", file_path)

        assert args == ["bash", str(file_path)]

    def test_build_command_args_unsupported(self):
        """Test _build_command_args for unsupported language."""
        executor = UnsafeLocalCodeExecutor()
        file_path = Path("/tmp/code_0.js")

        with pytest.raises(ValueError, match="unsupported language: javascript"):
            executor._build_command_args("javascript", file_path)
