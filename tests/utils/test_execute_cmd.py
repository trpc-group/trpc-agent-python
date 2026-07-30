# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.utils._execute_cmd.

Covers:
- CommandExecResult namedtuple
- async_execute_command: success, failure, timeout, input, env, exception branches
"""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from trpc_agent_sdk.utils import CommandExecResult
from trpc_agent_sdk.utils import async_execute_command
from trpc_agent_sdk.utils import _execute_cmd as execute_cmd_module


async def _assert_process_stopped(pid: int, failure_message: str) -> None:
    for _ in range(20):
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        if state.returncode != 0 or state.stdout.strip().startswith("Z"):
            return
        await asyncio.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    pytest.fail(failure_message)


class TestCommandExecResult:
    """Test suite for CommandExecResult namedtuple."""

    def test_fields(self):
        r = CommandExecResult(stdout="out", stderr="err", exit_code=0, is_timeout=False)
        assert r.stdout == "out"
        assert r.stderr == "err"
        assert r.exit_code == 0
        assert r.is_timeout is False

    def test_is_namedtuple(self):
        r = CommandExecResult("", "", 0, False)
        assert r._fields == ("stdout", "stderr", "exit_code", "is_timeout")

    def test_equality(self):
        r1 = CommandExecResult("a", "b", 1, True)
        r2 = CommandExecResult("a", "b", 1, True)
        assert r1 == r2

    def test_index_access(self):
        r = CommandExecResult("out", "err", 42, True)
        assert r[0] == "out"
        assert r[1] == "err"
        assert r[2] == 42
        assert r[3] is True


class TestAsyncExecuteCommand:
    """Test suite for async_execute_command function."""

    async def test_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["echo", "hello world"])
            assert isinstance(result, CommandExecResult)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert "hello" in result.stdout
            assert result.stderr == ""

    async def test_success_with_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["echo", "test"], timeout=5.0)
            assert result.exit_code == 0
            assert result.is_timeout is False
            assert "test" in result.stdout

    async def test_failure_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["false"])
            assert result.exit_code != 0
            assert result.is_timeout is False
            assert "command failed" in result.stderr

    async def test_timeout_triggers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["sleep", "10"], timeout=0.1)
            assert result.is_timeout is True
            assert result.exit_code == -1
            assert "timed out" in result.stderr.lower() or "timeout" in result.stderr.lower()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
    async def test_timeout_terminates_descendants_and_reaps_leader(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        parent_code = ("import subprocess,sys,time\n"
                       f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                       f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                       "time.sleep(60)\n")

        result = await async_execute_command(tmp_path, [sys.executable, "-c", parent_code], timeout=0.2)

        assert result.is_timeout is True
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        await _assert_process_stopped(child_pid, "descendant process survived timeout cleanup")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
    async def test_cancellation_terminates_descendants_and_reaps_leader(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        parent_code = ("import subprocess,sys,time\n"
                       f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                       f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                       "time.sleep(60)\n")
        task = asyncio.create_task(async_execute_command(tmp_path, [sys.executable, "-c", parent_code]))

        for _ in range(40):
            if pid_file.exists():
                break
            await asyncio.sleep(0.05)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            pytest.fail("child process did not start before cancellation")

        child_pid = int(pid_file.read_text(encoding="utf-8"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_process_stopped(child_pid, "descendant process survived cancellation cleanup")

    async def test_empty_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["true"])
            assert result.exit_code == 0
            assert result.stdout == ""
            assert result.stderr == ""

    async def test_multiline_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["sh", "-c", "echo -e 'line1\nline2\nline3'"])
            assert result.exit_code == 0
            assert "line1" in result.stdout
            assert "line2" in result.stdout

    async def test_with_stdin_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["cat"], input=b"hello from stdin")
            assert result.exit_code == 0
            assert "hello from stdin" in result.stdout

    async def test_with_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(
                Path(tmpdir),
                ["sh", "-c", "echo $MY_TEST_VAR"],
                env={
                    "MY_TEST_VAR": "test_value",
                    "PATH": "/usr/bin:/bin"
                },
            )
            assert result.exit_code == 0
            assert "test_value" in result.stdout

    async def test_nonexistent_command_exception(self):
        """Trigger the generic except Exception branch (lines 78-79)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["__nonexistent_cmd_xyz__"])
            assert result.exit_code == -1
            assert result.is_timeout is False
            assert "command execution error" in result.stderr

    async def test_invalid_workdir_exception(self):
        """Trigger exception with a non-existent working directory."""
        result = await async_execute_command(Path("/nonexistent/directory/xyz"), ["echo", "test"])
        assert result.exit_code == -1
        assert result.is_timeout is False
        assert "command execution error" in result.stderr

    async def test_failure_with_stderr_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["sh", "-c", "echo err_msg >&2; exit 1"])
            assert result.exit_code != 0
            assert "err_msg" in result.stderr

    async def test_with_timeout_and_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await async_execute_command(Path(tmpdir), ["cat"], input=b"data", timeout=5.0)
            assert result.exit_code == 0
            assert "data" in result.stdout

    async def test_started_process_is_reaped_when_communication_fails(self, monkeypatch, tmp_path):

        class FinishedProcess:
            pid = 12345
            returncode = 0

            async def communicate(self, input=None):
                del input
                raise RuntimeError("communication failed")

        process = FinishedProcess()

        async def create_process(*args, **kwargs):
            del args, kwargs
            return process

        def missing_process_group(pid, sig):
            del pid, sig
            raise ProcessLookupError

        monkeypatch.setattr(execute_cmd_module.asyncio, "create_subprocess_exec", create_process)
        monkeypatch.setattr(execute_cmd_module.os, "killpg", missing_process_group)

        result = await async_execute_command(tmp_path, ["echo", "hello"])

        assert result.exit_code == -1
        assert "communication failed" in result.stderr

    async def test_process_cleanup_falls_back_and_escalates_when_group_signals_fail(
        self,
        monkeypatch,
        tmp_path,
    ):

        class RunningProcess:
            pid = 12345
            returncode = None

            def __init__(self):
                self.terminated = False
                self.killed = False
                self.wait_calls = 0

            async def communicate(self, input=None):
                del input
                raise RuntimeError("communication failed")

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            async def wait(self):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise asyncio.TimeoutError
                self.returncode = -signal.SIGKILL
                return self.returncode

        process = RunningProcess()

        async def create_process(*args, **kwargs):
            del args, kwargs
            return process

        def unavailable_process_group(pid, sig):
            del pid, sig
            raise OSError("process groups unavailable")

        monkeypatch.setattr(execute_cmd_module.asyncio, "create_subprocess_exec", create_process)
        monkeypatch.setattr(execute_cmd_module.os, "killpg", unavailable_process_group)

        result = await async_execute_command(tmp_path, ["echo", "hello"])

        assert result.exit_code == -1
        assert process.terminated is True
        assert process.killed is True
        assert process.wait_calls == 2
