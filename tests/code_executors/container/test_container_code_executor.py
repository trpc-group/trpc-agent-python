# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ContainerCodeExecutor (_container_code_executor.py).

Covers:
- Initialization validation (image, docker_path, stateful, optimize_data_file)
- execute_code for various languages (python, python3, py, bash, sh, empty, unsupported)
- Multiple code blocks with mixed success/failure
- Environment and timeout forwarding via CommandArgs
- code_block_delimiter return value
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.code_executors._types import (
    CodeBlock,
    CodeBlockDelimiter,
    CodeExecutionInput,
    CodeExecutionResult,
    create_code_execution_result,
)
from trpc_agent_sdk.code_executors.container._container_code_executor import (
    ContainerCodeExecutor,
)
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.utils import CommandExecResult


@pytest.fixture
def mock_ctx():
    return Mock(spec=InvocationContext)


def _ok_result(stdout="ok"):
    return CommandExecResult(stdout=stdout, stderr="", exit_code=0, is_timeout=False)


def _err_result(stderr="error"):
    return CommandExecResult(stdout="", stderr=stderr, exit_code=1, is_timeout=False)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestContainerCodeExecutorInit:

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_init_with_image_only(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(image="python:3-slim")
        assert executor.image == "python:3-slim"
        assert executor.docker_path is None
        assert executor.base_url is None

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_init_with_docker_path_only(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(docker_path="/some/path")
        assert executor.docker_path == "/some/path"
        assert executor.image is None

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_init_with_base_url(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(base_url="tcp://host:2375", image="img")
        assert executor.base_url == "tcp://host:2375"

    def test_init_no_image_no_docker_path_raises(self):
        with pytest.raises(ValueError, match="Either image or docker_path must be set"):
            ContainerCodeExecutor()

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_init_stateful_true_raises(self, mock_cc_cls):
        with pytest.raises(ValueError, match="Cannot set `stateful=True`"):
            ContainerCodeExecutor(image="img", stateful=True)

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_init_optimize_data_file_true_raises(self, mock_cc_cls):
        with pytest.raises(ValueError, match="Cannot set `optimize_data_file=True`"):
            ContainerCodeExecutor(image="img", optimize_data_file=True)

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_stateful_default_is_false(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(image="img")
        assert executor.stateful is False

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_optimize_data_file_default_is_false(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(image="img")
        assert executor.optimize_data_file is False

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_environment_and_timeout(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(
            image="img", environment={"A": "B"}, timeout=42.0)
        assert executor.environment == {"A": "B"}
        assert executor.timeout == 42.0


# ---------------------------------------------------------------------------
# execute_code
# ---------------------------------------------------------------------------


class TestExecuteCode:

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_python_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("hello"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('hello')")])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_OK
        assert "hello" in result.output
        cmd = mock_cc.exec_run.await_args.kwargs.get("cmd", mock_cc.exec_run.await_args[0][0] if mock_cc.exec_run.await_args[0] else None)
        assert cmd[0] == "python3"

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_python3_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("out"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python3", code="1+1")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_py_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("out"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="py", code="1+1")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_empty_language_defaults_to_python(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("out"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="", code="x=1")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK
        call_kwargs = mock_cc.exec_run.await_args
        cmd = call_kwargs.kwargs.get("cmd") or call_kwargs[0][0]
        assert "python3" in cmd

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_bash_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("output"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="echo output")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK
        call_kwargs = mock_cc.exec_run.await_args
        cmd = call_kwargs.kwargs.get("cmd") or call_kwargs[0][0]
        assert cmd[0] == "/bin/bash"

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_sh_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("output"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="sh", code="echo output")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_unsupported_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock()
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="ruby", code="puts 'hi'")])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "unsupported language: ruby" in result.output
        mock_cc.exec_run.assert_not_called()

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_python_upper_case_language(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("out"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="PYTHON", code="x=1")])
        result = await executor.execute_code(mock_ctx, inp)
        assert result.outcome == Outcome.OUTCOME_OK

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_multiple_blocks_all_succeed(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(side_effect=[_ok_result("first"), _ok_result("second")])
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="1"),
            CodeBlock(language="python", code="2"),
        ])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_OK
        assert "first" in result.output
        assert "second" in result.output

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_multiple_blocks_mixed_success_failure(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(side_effect=[
            _ok_result("good"),
            _err_result("bad"),
            _ok_result("also good"),
        ])
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(language="python", code="ok"),
            CodeBlock(language="python", code="fail"),
            CodeBlock(language="python", code="ok2"),
        ])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "good" in result.output
        assert "bad" in result.output
        assert "also good" in result.output

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_stderr_on_nonzero_exit(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_err_result("traceback"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="bad")])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "traceback" in result.output

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_empty_code_blocks(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock()
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[])
        result = await executor.execute_code(mock_ctx, inp)

        assert result.outcome == Outcome.OUTCOME_OK
        mock_cc.exec_run.assert_not_called()

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_environment_and_timeout_forwarded(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(return_value=_ok_result("ok"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(
            image="img", environment={"MY_VAR": "123"}, timeout=5.0)
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="1")])
        await executor.execute_code(mock_ctx, inp)

        call_kwargs = mock_cc.exec_run.await_args.kwargs
        command_args = call_kwargs.get("command_args")
        assert command_args.environment == {"MY_VAR": "123"}
        assert command_args.timeout == 5.0

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    async def test_exec_run_exception_propagates(self, mock_cc_cls, mock_ctx):
        mock_cc = Mock()
        mock_cc.exec_run = AsyncMock(side_effect=RuntimeError("Docker crash"))
        mock_cc_cls.return_value = mock_cc

        executor = ContainerCodeExecutor(image="img")
        inp = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="1")])

        with pytest.raises(RuntimeError, match="Docker crash"):
            await executor.execute_code(mock_ctx, inp)


# ---------------------------------------------------------------------------
# code_block_delimiter
# ---------------------------------------------------------------------------


class TestCodeBlockDelimiter:

    @patch("trpc_agent_sdk.code_executors.container._container_code_executor.ContainerClient")
    def test_returns_default_delimiters(self, mock_cc_cls):
        mock_cc_cls.return_value = Mock()
        executor = ContainerCodeExecutor(image="img")
        delims = executor.code_block_delimiters

        assert isinstance(delims, list)
        assert all(isinstance(d, CodeBlockDelimiter) for d in delims)
        assert delims[0].start == "```tool_code\n"
        assert delims[0].end == "\n```"
