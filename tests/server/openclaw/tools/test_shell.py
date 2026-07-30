"""Unit tests for trpc_agent_sdk.server.openclaw.tools.shell module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.shell import ExecTool, _DEFAULT_DENY_PATTERNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# ExecTool.__init__
# ---------------------------------------------------------------------------


class TestExecToolInit:

    def test_default_deny_patterns(self):
        tool = ExecTool()
        assert tool._deny_patterns == _DEFAULT_DENY_PATTERNS

    def test_custom_deny_patterns(self):
        custom = [r"\bfoo\b"]
        tool = ExecTool(deny_patterns=custom)
        assert tool._deny_patterns == custom

    def test_empty_deny_patterns(self):
        tool = ExecTool(deny_patterns=[])
        assert tool._deny_patterns == []

    def test_default_timeout(self):
        tool = ExecTool()
        assert tool._timeout == 60

    def test_custom_timeout(self):
        tool = ExecTool(timeout=120)
        assert tool._timeout == 120

    def test_allow_patterns_default_empty(self):
        tool = ExecTool()
        assert tool._allow_patterns == []

    def test_allow_patterns_custom(self):
        tool = ExecTool(allow_patterns=[r"\bls\b"])
        assert tool._allow_patterns == [r"\bls\b"]

    def test_restrict_to_workspace_default_false(self):
        tool = ExecTool()
        assert tool._restrict_to_workspace is False

    def test_path_append(self):
        tool = ExecTool(path_append="/usr/local/bin")
        assert tool._path_append == "/usr/local/bin"

    def test_name_is_exec(self):
        tool = ExecTool()
        assert tool.name == "exec"


# ---------------------------------------------------------------------------
# ExecTool._get_declaration
# ---------------------------------------------------------------------------


class TestGetDeclaration:

    def test_declaration_name(self):
        tool = ExecTool()
        decl = tool._get_declaration()
        assert decl.name == "exec"

    def test_declaration_has_command_param(self):
        tool = ExecTool()
        decl = tool._get_declaration()
        assert "command" in decl.parameters.properties

    def test_declaration_required_fields(self):
        tool = ExecTool()
        decl = tool._get_declaration()
        assert "command" in decl.parameters.required


# ---------------------------------------------------------------------------
# ExecTool._guard_command
# ---------------------------------------------------------------------------


class TestGuardCommand:

    def test_rm_rf_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("rm -rf /", "/tmp") is not None

    def test_rm_r_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("rm -r /home", "/tmp") is not None

    def test_rm_f_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("rm -f file.txt", "/tmp") is not None

    def test_del_f_blocked(self):
        tool = ExecTool()
        result = tool._guard_command("del /f something", "/tmp")
        assert result is not None
        assert "blocked" in result

    def test_rmdir_s_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("rmdir /s mydir", "/tmp") is not None

    def test_format_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("format C:", "/tmp") is not None

    def test_format_after_semicolon_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("echo hello; format C:", "/tmp") is not None

    def test_mkfs_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("mkfs.ext4 /dev/sda1", "/tmp") is not None

    def test_diskpart_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("diskpart", "/tmp") is not None

    def test_dd_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("dd if=/dev/zero of=/dev/sda", "/tmp") is not None

    def test_write_to_disk_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("echo x > /dev/sda", "/tmp") is not None

    def test_shutdown_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("shutdown -h now", "/tmp") is not None

    def test_reboot_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("reboot", "/tmp") is not None

    def test_poweroff_blocked(self):
        tool = ExecTool()
        assert tool._guard_command("poweroff", "/tmp") is not None

    def test_fork_bomb_blocked(self):
        tool = ExecTool()
        assert tool._guard_command(":() { :|:& }; :", "/tmp") is not None

    def test_safe_command_allowed(self):
        tool = ExecTool()
        assert tool._guard_command("ls -la", "/tmp") is None

    def test_echo_allowed(self):
        tool = ExecTool()
        assert tool._guard_command("echo hello", "/tmp") is None

    def test_allow_patterns_blocks_non_matching(self):
        tool = ExecTool(deny_patterns=[], allow_patterns=[r"\bls\b"])
        result = tool._guard_command("cat file.txt", "/tmp")
        assert result is not None
        assert "not in allowlist" in result

    def test_allow_patterns_permits_matching(self):
        tool = ExecTool(deny_patterns=[], allow_patterns=[r"\bls\b"])
        assert tool._guard_command("ls -la", "/tmp") is None

    def test_deny_checked_before_allow(self):
        tool = ExecTool(
            deny_patterns=[r"\brm\b"],
            allow_patterns=[r"\brm\b"],
        )
        result = tool._guard_command("rm file", "/tmp")
        assert result is not None
        assert "dangerous pattern" in result

    def test_restrict_workspace_path_traversal_dotdotslash(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=True)
        result = tool._guard_command("cat ../secret.txt", str(tmp_path))
        assert result is not None
        assert "path traversal" in result

    def test_restrict_workspace_path_traversal_backslash(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=True)
        result = tool._guard_command("cat ..\\secret.txt", str(tmp_path))
        assert result is not None
        assert "path traversal" in result

    def test_restrict_workspace_absolute_path_outside(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=True)
        result = tool._guard_command("cat /etc/passwd", str(tmp_path))
        assert result is not None
        assert "path outside working dir" in result

    def test_restrict_workspace_absolute_path_inside(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=True)
        inside = str(tmp_path / "subdir" / "file.txt")
        result = tool._guard_command(f"cat {inside}", str(tmp_path))
        assert result is None

    def test_restrict_workspace_cwd_itself(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=True)
        result = tool._guard_command(f"ls {tmp_path}", str(tmp_path))
        assert result is None

    def test_restrict_workspace_disabled(self, tmp_path):
        tool = ExecTool(deny_patterns=[], restrict_to_workspace=False)
        assert tool._guard_command("cat /etc/passwd", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# ExecTool._extract_absolute_paths
# ---------------------------------------------------------------------------


class TestExtractAbsolutePaths:

    def test_posix_paths(self):
        paths = ExecTool._extract_absolute_paths("cat /etc/passwd /var/log/syslog")
        assert "/etc/passwd" in paths
        assert "/var/log/syslog" in paths

    def test_windows_paths(self):
        paths = ExecTool._extract_absolute_paths(r"type C:\Users\admin\file.txt")
        assert r"C:\Users\admin\file.txt" in paths

    def test_home_paths(self):
        paths = ExecTool._extract_absolute_paths("cat ~/Documents/file.txt")
        assert "~/Documents/file.txt" in paths

    def test_no_paths(self):
        paths = ExecTool._extract_absolute_paths("echo hello world")
        assert paths == []

    def test_mixed_paths(self):
        cmd = r"cat /etc/hosts C:\Windows\hosts ~/file.txt"
        paths = ExecTool._extract_absolute_paths(cmd)
        assert "/etc/hosts" in paths
        assert r"C:\Windows\hosts" in paths
        assert "~/file.txt" in paths


# ---------------------------------------------------------------------------
# ExecTool._run_async_impl
# ---------------------------------------------------------------------------


class TestRunAsyncImpl:

    async def test_successful_execution(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "echo hello"},
        )
        assert "hello" in result
        assert "Exit code: 0" in result

    async def test_stderr_output(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "echo error >&2"},
        )
        assert "STDERR:" in result
        assert "error" in result

    async def test_timeout(self):
        tool = ExecTool(deny_patterns=[], timeout=1)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "sleep 30", "timeout": 1},
        )
        assert "timed out" in result

    async def test_guarded_command_returns_error(self):
        tool = ExecTool()
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "rm -rf /"},
        )
        assert "blocked" in result

    async def test_working_dir_override(self, tmp_path):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "pwd", "working_dir": str(tmp_path)},
        )
        assert str(tmp_path) in result

    async def test_timeout_capped_at_max(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "echo ok", "timeout": 9999},
        )
        assert "ok" in result

    async def test_path_append(self):
        tool = ExecTool(deny_patterns=[], path_append="/nonexistent")
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "echo $PATH"},
        )
        assert "Exit code:" in result

    async def test_output_truncation(self):
        tool = ExecTool(deny_patterns=[])
        tool._MAX_OUTPUT = 100
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "python3 -c \"print('A' * 500)\""},
        )
        assert "truncated" in result

    async def test_nonzero_exit_code(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "exit 42"},
        )
        assert "Exit code: 42" in result

    async def test_exception_handling(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        with patch("asyncio.create_subprocess_shell", side_effect=OSError("fail")):
            result = await tool._run_async_impl(
                tool_context=ctx,
                args={"command": "echo hello"},
            )
        assert "Error executing command" in result

    async def test_empty_output(self):
        tool = ExecTool(deny_patterns=[])
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "true"},
        )
        assert "Exit code: 0" in result

    async def test_default_working_dir_from_init(self, tmp_path):
        tool = ExecTool(deny_patterns=[], working_dir=str(tmp_path))
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"command": "pwd"},
        )
        assert str(tmp_path) in result
