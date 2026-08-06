# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for BashTool."""

from unittest.mock import Mock
from unittest.mock import patch

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

        assert tool._is_command_safe("blocked_cmd", str(tmp_path)) is True

        workdir = str(tmp_path)
        outside_dir = "/var" if workdir != "/var" else "/usr"
        assert tool._is_command_safe("blocked_cmd", outside_dir) is False

    @pytest.mark.parametrize(
        "command",
        [
            "blocked_cmd",
            "echo ok | blocked_cmd",
            "echo ok; blocked_cmd",
            "echo ok && blocked_cmd",
            "echo ok || blocked_cmd",
            "echo ok\nblocked_cmd",
            "echo ok & blocked_cmd",
            "echo $(blocked_cmd)",
            "echo `blocked_cmd`",
            r"echo escaped\>& blocked_cmd",
        ],
    )
    def test_is_command_safe_rejects_non_whitelisted_command_segments(self, tool_with_whitelist, command):
        """Every executable command segment must be allowlisted."""
        assert tool_with_whitelist._is_command_safe(command, tool_with_whitelist.cwd) is False

    @pytest.mark.parametrize(
        "command",
        [
            "echo ok",
            "echo ok | echo done",
            "echo ok; echo done",
            "echo ok && echo done",
            "echo ok || echo done",
            "echo ok\necho done",
            "echo ok & echo done",
        ],
    )
    def test_is_command_safe_allows_only_whitelisted_command_segments(self, tool_with_whitelist, command):
        """Compound commands remain valid when every command is allowlisted."""
        assert tool_with_whitelist._is_command_safe(command, tool_with_whitelist.cwd) is True

    @pytest.mark.parametrize(
        "command",
        [
            "echo 'literal; not a separator'",
            'echo "literal && not a separator"',
            r"echo escaped\;separator",
            "echo ok 2>&1",
            "echo ok &>output.log",
            "echo '$" + "\\" + "\n" + "(blocked_cmd)'",
        ],
    )
    def test_is_command_safe_preserves_quoted_escaped_and_redirected_arguments(self, tool_with_whitelist, command):
        """Shell syntax inside arguments and file-descriptor redirects is not a command boundary."""
        assert tool_with_whitelist._is_command_safe(command, tool_with_whitelist.cwd) is True

    @pytest.mark.parametrize(
        "command",
        [
            "cat <<EOF\nplain data\nEOF",
            "cat <<'EOF'\n$(blocked_cmd)\nEOF",
            "cat <<-EOF\n\ttab-indented data\n\tEOF",
            "cat <<EOF\nplain data\nEOF\necho done",
        ],
    )
    def test_is_command_safe_allows_heredoc_data(self, tmp_path, command):
        """Heredoc bodies are data rather than separate command segments."""
        tool = BashTool(cwd=str(tmp_path), whitelist_commands=["cat", "echo"])

        assert tool._is_command_safe(command, tool.cwd) is True

    @pytest.mark.parametrize(
        "command",
        [
            "cat <<EOF\nplain data",
            "cat <<EOF\n$(blocked_cmd)\nEOF",
            "cat <<EOF\nplain data\nEOF\nblocked_cmd",
        ],
    )
    def test_is_command_safe_rejects_unsafe_or_unterminated_heredocs(self, tmp_path, command):
        """Heredocs fail closed when parsing or expansion is unsafe."""
        tool = BashTool(cwd=str(tmp_path), whitelist_commands=["cat", "echo"])

        assert tool._is_command_safe(command, tool.cwd) is False

    @pytest.mark.parametrize(
        "command",
        [
            "echo 'unterminated",
            "echo <(blocked_cmd)",
            "echo >(blocked_cmd)",
            "echo $" + "\\" + "\n" + "(blocked_cmd)",
            'echo "$' + "\\" + "\n" + '(blocked_cmd)"',
            "cat <" + "\\" + "\n" + "(blocked_cmd)",
            "cat >" + "\\" + "\n" + "(blocked_cmd)",
        ],
    )
    def test_is_command_safe_fails_closed_for_unverifiable_syntax(self, tool_with_whitelist, command):
        """Unparseable syntax and process substitution are rejected."""
        assert tool_with_whitelist._is_command_safe(command, tool_with_whitelist.cwd) is False

    @pytest.mark.asyncio
    @patch("trpc_agent_sdk.tools.file_tools._bash_tool.asyncio.create_subprocess_shell")
    async def test_bash_custom_whitelist_blocks_before_subprocess(
        self,
        mock_create_subprocess_shell,
        tool_with_whitelist,
        tool_context,
    ):
        """A rejected command never reaches the subprocess boundary."""
        result = await tool_with_whitelist._run_async_impl(
            tool_context=tool_context,
            args={"command": "blocked_cmd"},
        )

        assert result["success"] is False
        assert "SECURITY_RESTRICTION" in result["error"]
        mock_create_subprocess_shell.assert_not_called()
