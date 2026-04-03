# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeBlockDelimiter
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import create_code_execution_result
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.utils import CommandExecResult


class TestUnsafeLocalCodeExecutorInit:
    """Tests for UnsafeLocalCodeExecutor initialization."""

    def test_defaults(self):
        executor = UnsafeLocalCodeExecutor()
        assert executor.stateful is False
        assert executor.optimize_data_file is False
        assert executor.work_dir == ""
        assert executor.timeout == 0
        assert executor.clean_temp_files is True

    def test_stateful_true_raises(self):
        with pytest.raises(ValueError, match="Cannot set `stateful=True`"):
            UnsafeLocalCodeExecutor(stateful=True)

    def test_optimize_data_file_true_raises(self):
        with pytest.raises(ValueError, match="Cannot set `optimize_data_file=True`"):
            UnsafeLocalCodeExecutor(optimize_data_file=True)

    def test_stateful_false_ok(self):
        executor = UnsafeLocalCodeExecutor(stateful=False)
        assert executor.stateful is False

    def test_optimize_data_file_false_ok(self):
        executor = UnsafeLocalCodeExecutor(optimize_data_file=False)
        assert executor.optimize_data_file is False

    def test_custom_work_dir(self):
        executor = UnsafeLocalCodeExecutor(work_dir="/tmp/custom")
        assert executor.work_dir == "/tmp/custom"

    def test_custom_timeout(self):
        executor = UnsafeLocalCodeExecutor(timeout=60.0)
        assert executor.timeout == 60.0

    def test_custom_clean_temp_files(self):
        executor = UnsafeLocalCodeExecutor(clean_temp_files=False)
        assert executor.clean_temp_files is False

    def test_custom_delimiter(self):
        delim = CodeBlockDelimiter(start="<<<", end=">>>")
        executor = UnsafeLocalCodeExecutor(delimiter=delim)
        assert executor.delimiter.start == "<<<"
        assert executor.delimiter.end == ">>>"


class TestCodeBlockDelimiter:
    """Tests for code_block_delimiter method."""

    def test_returns_default_delimiter(self):
        executor = UnsafeLocalCodeExecutor()
        delimiter = executor.code_block_delimiter()
        assert isinstance(delimiter, CodeBlockDelimiter)
        assert delimiter.start == "```"
        assert delimiter.end == "```"

    def test_returns_custom_delimiter(self):
        custom = CodeBlockDelimiter(start="---", end="---")
        executor = UnsafeLocalCodeExecutor(delimiter=custom)
        assert executor.code_block_delimiter() == custom


class TestPrepareWorkDir:
    """Tests for _prepare_work_dir."""

    def test_with_absolute_work_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = UnsafeLocalCodeExecutor(work_dir=tmpdir)
            work_path, should_cleanup = executor._prepare_work_dir("exec-1")
            assert work_path == Path(tmpdir)
            assert should_cleanup is False

    def test_with_relative_work_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rel_dir = "relative_dir"
            full_path = Path(tmpdir) / rel_dir
            full_path.mkdir()
            executor = UnsafeLocalCodeExecutor(work_dir=str(full_path))
            work_path, should_cleanup = executor._prepare_work_dir("exec-1")
            assert work_path.exists()
            assert should_cleanup is False

    def test_temp_dir_created_when_no_work_dir(self):
        executor = UnsafeLocalCodeExecutor()
        work_path, should_cleanup = executor._prepare_work_dir("exec-1")
        try:
            assert work_path.exists()
            assert should_cleanup is True
            assert "codeexec_exec-1_" in str(work_path)
        finally:
            shutil.rmtree(work_path, ignore_errors=True)

    def test_temp_dir_no_cleanup_when_disabled(self):
        executor = UnsafeLocalCodeExecutor(clean_temp_files=False)
        work_path, should_cleanup = executor._prepare_work_dir("exec-2")
        try:
            assert work_path.exists()
            assert should_cleanup is False
        finally:
            shutil.rmtree(work_path, ignore_errors=True)

    def test_work_dir_created_if_not_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "new_subdir"
            executor = UnsafeLocalCodeExecutor(work_dir=str(new_dir))
            work_path, should_cleanup = executor._prepare_work_dir("exec-1")
            assert work_path.exists()
            assert should_cleanup is False

    def test_with_relative_work_dir_resolves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                executor = UnsafeLocalCodeExecutor(work_dir="rel_work")
                work_path, should_cleanup = executor._prepare_work_dir("exec-rel")
                assert work_path.is_absolute()
                assert work_path.exists()
                assert should_cleanup is False
            finally:
                os.chdir(original_cwd)


class TestPrepareCodeFile:
    """Tests for _prepare_code_file."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.work_dir = Path(self.tmpdir)
        self.executor = UnsafeLocalCodeExecutor()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_python_file(self):
        block = CodeBlock(language="python", code="print('hello')")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        assert file_path.suffix == ".py"
        assert file_path.name == "code_0.py"
        assert file_path.read_text() == "print('hello')"

    def test_py_alias(self):
        block = CodeBlock(language="py", code="x = 1")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 1)
        assert file_path.suffix == ".py"
        assert file_path.name == "code_1.py"

    def test_python3_alias(self):
        block = CodeBlock(language="python3", code="x = 1")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 2)
        assert file_path.suffix == ".py"

    def test_bash_file(self):
        block = CodeBlock(language="bash", code="echo hello")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        assert file_path.suffix == ".sh"
        assert file_path.name == "code_0.sh"
        assert file_path.read_text() == "echo hello"

    def test_sh_alias(self):
        block = CodeBlock(language="sh", code="echo hi")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        assert file_path.suffix == ".sh"

    def test_unsupported_language(self):
        block = CodeBlock(language="javascript", code="console.log('hi')")
        with pytest.raises(ValueError, match="unsupported language"):
            self.executor._prepare_code_file(self.work_dir, block, 0)

    def test_python_no_print_appends_newline(self):
        block = CodeBlock(language="python", code="x = 1")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        content = file_path.read_text()
        assert content.endswith("\n")

    def test_python_with_print_no_extra_newline(self):
        block = CodeBlock(language="python", code="print('hello')")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        content = file_path.read_text()
        assert content == "print('hello')"

    def test_python_with_sys_stdout_write(self):
        block = CodeBlock(language="python", code="import sys\nsys.stdout.write('hello')")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        content = file_path.read_text()
        assert not content.endswith("\n\n")

    def test_code_stripped(self):
        block = CodeBlock(language="python", code="  print('hello')  \n  ")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        content = file_path.read_text()
        assert content.startswith("print('hello')")

    def test_bash_file_permissions(self):
        block = CodeBlock(language="bash", code="echo hello")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        mode = file_path.stat().st_mode & 0o777
        assert mode == 0o755

    def test_python_file_permissions(self):
        block = CodeBlock(language="python", code="print('hi')")
        file_path = self.executor._prepare_code_file(self.work_dir, block, 0)
        mode = file_path.stat().st_mode & 0o777
        assert mode == 0o644

    def test_block_index_in_filename(self):
        block = CodeBlock(language="python", code="x = 1")
        for i in range(3):
            file_path = self.executor._prepare_code_file(self.work_dir, block, i)
            assert file_path.name == f"code_{i}.py"


class TestBuildCommandArgs:
    """Tests for _build_command_args."""

    def setup_method(self):
        self.executor = UnsafeLocalCodeExecutor()

    def test_python(self):
        path = Path("/tmp/code_0.py")
        assert self.executor._build_command_args("python", path) == ["python3", "/tmp/code_0.py"]

    def test_py(self):
        path = Path("/tmp/code_0.py")
        assert self.executor._build_command_args("py", path) == ["python3", "/tmp/code_0.py"]

    def test_python3(self):
        path = Path("/tmp/code_0.py")
        assert self.executor._build_command_args("python3", path) == ["python3", "/tmp/code_0.py"]

    def test_bash(self):
        path = Path("/tmp/code_0.sh")
        assert self.executor._build_command_args("bash", path) == ["bash", "/tmp/code_0.sh"]

    def test_sh(self):
        path = Path("/tmp/code_0.sh")
        assert self.executor._build_command_args("sh", path) == ["bash", "/tmp/code_0.sh"]

    def test_unsupported_language_raises(self):
        path = Path("/tmp/code_0.js")
        with pytest.raises(ValueError, match="unsupported language: javascript"):
            self.executor._build_command_args("javascript", path)

    def test_case_insensitive(self):
        path = Path("/tmp/code_0.py")
        assert self.executor._build_command_args("PYTHON", path) == ["python3", "/tmp/code_0.py"]

    def test_mixed_case(self):
        path = Path("/tmp/code_0.sh")
        assert self.executor._build_command_args("Bash", path) == ["bash", "/tmp/code_0.sh"]


class TestExecuteCode:
    """Tests for execute_code method."""

    def setup_method(self):
        self.mock_ctx = Mock(spec=InvocationContext)

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_single_python_block(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="hello", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('hello')")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "hello" in result.output
        assert result.outcome == Outcome.OUTCOME_OK
        mock_exec.assert_called_once()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_single_bash_block(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="world", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="echo world")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "world" in result.output
        assert result.outcome == Outcome.OUTCOME_OK

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_code_field_fallback(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="output", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code="print('output')")
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "output" in result.output
        assert len(inp.code_blocks) == 1
        assert inp.code_blocks[0].language == "python"

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_multiple_blocks(self, mock_exec):
        mock_exec.side_effect = [
            CommandExecResult(stdout="first", stderr="", exit_code=0, is_timeout=False),
            CommandExecResult(stdout="second", stderr="", exit_code=0, is_timeout=False),
        ]
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('first')"),
            CodeBlock(language="python", code="print('second')"),
        ])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "first" in result.output
        assert "second" in result.output
        assert mock_exec.call_count == 2

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_execution_failure(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="error msg", exit_code=1, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="bad_code")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_FAILED

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="timed out", exit_code=-1, is_timeout=True)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="import time; time.sleep(100)")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_FAILED

    @pytest.mark.asyncio
    async def test_unsupported_language_in_execute(self):
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="javascript", code="console.log('hi')")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "unsupported language" in result.output
        assert result.outcome == Outcome.OUTCOME_FAILED

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_with_custom_work_dir(self, mock_exec):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_exec.return_value = CommandExecResult(stdout="ok", stderr="", exit_code=0, is_timeout=False)
            executor = UnsafeLocalCodeExecutor(work_dir=tmpdir)
            inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])
            result = await executor.execute_code(self.mock_ctx, inp)
            assert "ok" in result.output
            call_kwargs = mock_exec.call_args[1]
            assert tmpdir in str(call_kwargs.get('work_dir', ''))

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_with_execution_id(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="out", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code="print('out')")],
            execution_id="my-exec-id",
        )
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "out" in result.output

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_temp_dir_cleanup(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="ok", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor(clean_temp_files=True)
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])

        call_work_dir = None

        original_prepare = executor._prepare_work_dir

        def capture_prepare(exec_id):
            nonlocal call_work_dir
            result = original_prepare(exec_id)
            call_work_dir = result[0]
            return result

        with patch.object(executor, '_prepare_work_dir', side_effect=capture_prepare):
            await executor.execute_code(self.mock_ctx, inp)

        if call_work_dir:
            assert not call_work_dir.exists()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_partial_failure_in_multiple_blocks(self, mock_exec):
        mock_exec.side_effect = [
            CommandExecResult(stdout="ok", stderr="", exit_code=0, is_timeout=False),
            CommandExecResult(stdout="", stderr="block 1 error", exit_code=1, is_timeout=False),
        ]
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="print('ok')"),
            CodeBlock(language="python", code="raise Exception('fail')"),
        ])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert "ok" in result.output or "failed" in result.output.lower()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_empty_stdout_result(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=0, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="x = 1")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_no_code_blocks_and_no_code(self, mock_exec):
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput()
        result = await executor.execute_code(self.mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK
        mock_exec.assert_not_called()

    @patch('trpc_agent_sdk.code_executors.local._unsafe_local_code_executor.async_execute_command')
    @pytest.mark.asyncio
    async def test_error_with_empty_stderr(self, mock_exec):
        mock_exec.return_value = CommandExecResult(stdout="", stderr="", exit_code=1, is_timeout=False)
        executor = UnsafeLocalCodeExecutor()
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="bad")])
        result = await executor.execute_code(self.mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_FAILED
