# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import tempfile
from pathlib import Path

import pytest
from trpc_agent_sdk.utils import CommandExecResult
from trpc_agent_sdk.utils import async_execute_command


class TestAsyncExecuteCommand:
    """Test suite for async_execute_command function."""

    @pytest.mark.asyncio
    async def test_async_execute_command_success(self):
        """Test successful async command execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            cmd_args = ["echo", "hello world"]

            result = await async_execute_command(work_dir, cmd_args)

            assert isinstance(result, CommandExecResult)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert "hello" in result.stdout
            assert "world" in result.stdout
            assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_async_execute_command_with_timeout(self):
        """Test async command execution with timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            cmd_args = ["echo", "test"]

            result = await async_execute_command(work_dir, cmd_args, timeout=5.0)

            assert isinstance(result, CommandExecResult)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert "test" in result.stdout
            assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_async_execute_command_failure(self):
        """Test async command execution failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            cmd_args = ["false"]  # Command that always fails

            result = await async_execute_command(work_dir, cmd_args)

            assert isinstance(result, CommandExecResult)
            assert result.exit_code != 0
            assert result.is_timeout is False
            assert "command failed" in result.stderr

    @pytest.mark.asyncio
    async def test_async_execute_command_timeout(self):
        """Test async command execution timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Use a command that will timeout
            cmd_args = ["sleep", "10"]

            result = await async_execute_command(work_dir, cmd_args, timeout=0.1)

            assert isinstance(result, CommandExecResult)
            assert result.is_timeout is True
            assert result.exit_code == -1
            assert "timed out" in result.stderr.lower() or "timeout" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_async_execute_command_empty_output(self):
        """Test async command execution with empty output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            cmd_args = ["true"]  # Command that succeeds but produces no output

            result = await async_execute_command(work_dir, cmd_args)

            assert isinstance(result, CommandExecResult)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert result.stdout == ""
            assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_async_execute_command_multiline_output(self):
        """Test async command execution with multiline output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            cmd_args = ["sh", "-c", "echo -e 'line1\nline2\nline3'"]

            result = await async_execute_command(work_dir, cmd_args)

            assert isinstance(result, CommandExecResult)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert "line1" in result.stdout
            assert "line2" in result.stdout
            assert "line3" in result.stdout
            assert result.stderr == ""
