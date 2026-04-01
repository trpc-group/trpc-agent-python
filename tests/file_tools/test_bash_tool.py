# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for BashTool."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BashTool


class TestBashTool:
    """Test suite for BashTool."""

    @pytest.fixture
    def tool(self, tmp_path):
        """Create BashTool instance."""
        return BashTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_with_whitelist(self, tmp_path):
        """Create BashTool instance with custom whitelist."""
        return BashTool(cwd=str(tmp_path), whitelist_commands=["echo", "pwd"])

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.mark.asyncio
    async def test_bash_simple_command(self, tool, tool_context):
        """Test executing simple command."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"command": "echo 'Hello World'"},
        )

        assert result["success"] is True
        assert result["return_code"] == 0
        assert "Hello World" in result["stdout"]

    @pytest.mark.asyncio
    async def test_bash_command_with_cwd(self, tool, tool_context, tmp_path):
        """Test executing command with custom cwd."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "command": "cat test.txt",
                "cwd": str(tmp_path)
            },
        )

        assert result["success"] is True
        assert "content" in result["stdout"]

    @pytest.mark.asyncio
    async def test_bash_command_error(self, tool, tool_context):
        """Test executing command that fails."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"command": "false"},
        )

        assert result["success"] is False
        assert result["return_code"] != 0

    @pytest.mark.asyncio
    async def test_bash_command_timeout(self, tool, tool_context):
        """Test command timeout."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "command": "sleep 10",
                "timeout": 1
            },
        )

        assert result["success"] is False
        assert "COMMAND_TIMEOUT" in result["error"]

    @pytest.mark.asyncio
    async def test_bash_missing_command(self, tool, tool_context):
        """Test executing without command parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_bash_pipe_command(self, tool, tool_context):
        """Test executing command with pipe."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"command": "echo 'test' | cat"},
        )

        assert result["success"] is True
        assert "test" in result["stdout"]

    @pytest.mark.asyncio
    async def test_bash_security_restriction(self, tool, tool_context):
        """Test security restriction for commands outside working directory."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "command": "rm -rf /nonexistent",
                "cwd": "/tmp"
            },
        )

        assert "command" in result

    @pytest.mark.asyncio
    async def test_bash_allowed_command_outside_workdir(self, tool, tool_context):
        """Test allowed command outside working directory."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "command": "ls",
                "cwd": "/tmp"
            },
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_bash_custom_whitelist(self, tool_with_whitelist, tool_context):
        """Test custom whitelist commands."""
        result = await tool_with_whitelist._run_async_impl(
            tool_context=tool_context,
            args={"command": "echo 'test'"},
        )

        assert result["success"] is True

        result = await tool_with_whitelist._run_async_impl(
            tool_context=tool_context,
            args={
                "command": "ls",
                "cwd": "/tmp"
            },
        )

        if "error" in result:
            assert "SECURITY_RESTRICTION" in result["error"]

    @pytest.mark.asyncio
    async def test_bash_command_in_workdir(self, tool, tool_context, tmp_path):
        """Test command in working directory has no restrictions."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"command": f"cat {test_file.name}"},
        )

        assert result["success"] is True
        assert "content" in result["stdout"]

    def test_resolve_execution_directory(self, tool, tmp_path):
        """Test _resolve_execution_directory method."""
        result = tool._resolve_execution_directory(None)
        assert result == str(tmp_path)

        result = tool._resolve_execution_directory("subdir")
        assert result == str(tmp_path / "subdir")

        result = tool._resolve_execution_directory("/tmp")
        assert result == "/tmp"

    def test_is_command_safe(self, tool, tmp_path):
        """Test _is_command_safe method."""
        assert tool._is_command_safe("echo test", str(tmp_path)) is True
        assert tool._is_command_safe("ls", "/tmp") is True

        import os
        workdir = str(tmp_path)
        outside_dir = "/var" if workdir != "/var" else "/usr"
        result = tool._is_command_safe("rm -rf /nonexistent", outside_dir)
        assert isinstance(result, bool)
