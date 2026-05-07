# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._sandbox.

Every test in this file uses the ``fake_e2b`` fixture in conftest.py,
which monkeypatches the ``e2b`` symbol bound in
``trpc_agent_sdk.code_executors.cube._sandbox`` (and ``_code_executor``)
so the real ``e2b-code-interpreter`` SDK is never invoked, even though
it is now a hard import dependency of the cube subpackage.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.code_executors.cube import _sandbox
from trpc_agent_sdk.code_executors.cube._sandbox import (
    CubeCommandResult,
    CubeSandboxClient,
)
from trpc_agent_sdk.code_executors.cube._types import CubeCodeExecutorConfig


def _cfg(**overrides) -> CubeCodeExecutorConfig:
    base = dict(
        template="tmpl",
        api_url="https://api",
        api_key="sekret",
        idle_timeout=123,
        execute_timeout=45.0,
    )
    base.update(overrides)
    return CubeCodeExecutorConfig(**base)


# ---------------------------------------------------------------------------
# Construction & sandbox_id
# ---------------------------------------------------------------------------


class TestConstruction:

    def test_stores_timeouts_and_sandbox_id(self, fake_async_sandbox):
        client = CubeSandboxClient(
            fake_async_sandbox, idle_timeout=600, execute_timeout=30
        )
        assert client.sandbox_id == "sbx-1"

    def test_sandbox_id_after_close_raises(self, fake_async_sandbox):
        client = CubeSandboxClient(
            fake_async_sandbox, idle_timeout=600, execute_timeout=30
        )
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = client.sandbox_id


# ---------------------------------------------------------------------------
# open_new
# ---------------------------------------------------------------------------


class TestOpenNew:

    @pytest.mark.asyncio
    async def test_creates_with_resolved_credentials(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        cfg = _cfg()
        client = await CubeSandboxClient.open_new(cfg)

        fake_e2b.AsyncSandbox.create.assert_awaited_once_with(
            template="tmpl",
            api_url="https://api",
            api_key="sekret",
            timeout=123,
        )
        assert client.sandbox_id == "sbx-1"
        kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
        assert isinstance(kwargs["timeout"], int)

    @pytest.mark.asyncio
    async def test_missing_template_raises(self, fake_e2b, monkeypatch):
        monkeypatch.delenv("CUBE_TEMPLATE_ID", raising=False)
        fake_e2b.AsyncSandbox.create = AsyncMock()
        cfg = _cfg(template=None)
        with pytest.raises(ValueError, match="CUBE_TEMPLATE_ID"):
            await CubeSandboxClient.open_new(cfg)


# ---------------------------------------------------------------------------
# open_existing
# ---------------------------------------------------------------------------


class TestOpenExisting:

    @pytest.mark.asyncio
    async def test_connects_and_asserts_running(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.connect = AsyncMock(return_value=fake_async_sandbox)
        cfg = _cfg()
        client = await CubeSandboxClient.open_existing("sbx-42", cfg)

        fake_e2b.AsyncSandbox.connect.assert_awaited_once_with(
            "sbx-42", api_url="https://api", api_key="sekret"
        )
        fake_async_sandbox.get_info.assert_awaited_once()
        assert client is not None

    @pytest.mark.asyncio
    async def test_paused_state_raises(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.connect = AsyncMock(return_value=fake_async_sandbox)
        fake_async_sandbox.get_info.return_value = SimpleNamespace(
            state=fake_e2b.SandboxState.PAUSED
        )
        cfg = _cfg()
        with pytest.raises(fake_e2b.SandboxException, match="paused"):
            await CubeSandboxClient.open_existing("sbx-42", cfg)

    @pytest.mark.asyncio
    async def test_missing_sandbox_propagates(self, fake_e2b):
        async def raise_not_found(*a, **k):
            raise fake_e2b.SandboxNotFoundException("gone")

        fake_e2b.AsyncSandbox.connect = raise_not_found
        with pytest.raises(fake_e2b.SandboxNotFoundException):
            await CubeSandboxClient.open_existing("sbx-42", _cfg())


# ---------------------------------------------------------------------------
# close / destroy
# ---------------------------------------------------------------------------


class TestClose:

    def test_close_drops_handle_without_calling_kill(self, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        client.close()
        fake_async_sandbox.kill.assert_not_called()


class TestDestroy:

    @pytest.mark.asyncio
    async def test_happy_path(self, fake_e2b, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.destroy()
        fake_async_sandbox.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_closed_is_noop(self, fake_e2b, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        client.close()
        await client.destroy()  # must not raise and must not touch e2b
        fake_async_sandbox.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_not_found(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.kill = AsyncMock(
            side_effect=fake_e2b.SandboxNotFoundException("gone")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.destroy()

    @pytest.mark.asyncio
    async def test_swallows_stopped_sandbox_exception(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.kill = AsyncMock(
            side_effect=fake_e2b.SandboxException("instance is STOPPED")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.destroy()

    @pytest.mark.asyncio
    async def test_reraises_other_sandbox_exception(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.kill = AsyncMock(
            side_effect=fake_e2b.SandboxException("auth failed")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        with pytest.raises(fake_e2b.SandboxException, match="auth failed"):
            await client.destroy()

    @pytest.mark.asyncio
    async def test_handle_cleared_even_on_error(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.kill = AsyncMock(
            side_effect=fake_e2b.SandboxException("auth failed")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        with pytest.raises(fake_e2b.SandboxException):
            await client.destroy()
        # After the failure, the handle is gone (finally block).
        with pytest.raises(RuntimeError, match="closed"):
            _ = client.sandbox_id


# ---------------------------------------------------------------------------
# assert_running / set_timeout
# ---------------------------------------------------------------------------


class TestAssertRunning:

    @pytest.mark.asyncio
    async def test_running_is_silent(self, fake_e2b, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.assert_running()

    @pytest.mark.asyncio
    async def test_paused_raises_sandbox_exception(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.get_info.return_value = SimpleNamespace(
            state=fake_e2b.SandboxState.PAUSED
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        with pytest.raises(fake_e2b.SandboxException, match="paused"):
            await client.assert_running()

    @pytest.mark.asyncio
    async def test_not_found_propagates(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.get_info.side_effect = fake_e2b.SandboxNotFoundException(
            "gone"
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        with pytest.raises(fake_e2b.SandboxNotFoundException):
            await client.assert_running()

    @pytest.mark.asyncio
    async def test_called_after_close_raises(self, fake_e2b, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            await client.assert_running()


class TestSetTimeout:

    @pytest.mark.asyncio
    async def test_happy_path(self, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.set_timeout(300)
        fake_async_sandbox.set_timeout.assert_awaited_once_with(300)

    @pytest.mark.asyncio
    async def test_exceptions_are_swallowed(self, fake_async_sandbox):
        fake_async_sandbox.set_timeout.side_effect = RuntimeError("nope")
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.set_timeout(10)  # must not raise


# ---------------------------------------------------------------------------
# commands_run
# ---------------------------------------------------------------------------


def _cmd_return(stdout: str = "", stderr: str = "", exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=exit_code)


class TestCommandsRun:

    @pytest.mark.asyncio
    async def test_basic_success(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.commands.run = AsyncMock(
            return_value=_cmd_return(stdout="ok", stderr="", exit_code=0)
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)

        result = await client.commands_run("echo hi")

        assert isinstance(result, CubeCommandResult)
        assert result.stdout == "ok"
        assert result.stderr == ""
        assert result.exit_code == 0
        fake_async_sandbox.commands.run.assert_awaited_once()
        args, kwargs = fake_async_sandbox.commands.run.await_args
        assert args == ("echo hi",)
        assert kwargs["envs"] == {}
        assert kwargs["user"] == "root"
        assert kwargs["timeout"] == 30.0  # execute_timeout default
        assert "cwd" not in kwargs  # not provided when falsy

    @pytest.mark.asyncio
    async def test_with_env_and_cwd(self, fake_async_sandbox):
        fake_async_sandbox.commands.run = AsyncMock(
            return_value=_cmd_return(stdout="")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.commands_run("cmd", env={"K": "V"}, cwd="/ws")
        kwargs = fake_async_sandbox.commands.run.await_args.kwargs
        assert kwargs["envs"] == {"K": "V"}
        assert kwargs["cwd"] == "/ws"

    @pytest.mark.asyncio
    async def test_env_none_becomes_empty_dict(self, fake_async_sandbox):
        fake_async_sandbox.commands.run = AsyncMock(
            return_value=_cmd_return(stdout="")
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.commands_run("cmd", env=None)
        assert fake_async_sandbox.commands.run.await_args.kwargs["envs"] == {}

    @pytest.mark.asyncio
    async def test_stdin_is_heredoc_wrapped(self, fake_async_sandbox):
        fake_async_sandbox.commands.run = AsyncMock(return_value=_cmd_return(""))
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.commands_run("python3", stdin=b"print('hi')")

        invoked_cmd = fake_async_sandbox.commands.run.await_args.args[0]
        assert invoked_cmd.startswith("python3 << 'TRPC_STDIN_EOF_")
        assert "print('hi')" in invoked_cmd

    @pytest.mark.asyncio
    async def test_timeout_override(self, fake_async_sandbox):
        fake_async_sandbox.commands.run = AsyncMock(return_value=_cmd_return(""))
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.commands_run("cmd", timeout=5)
        assert fake_async_sandbox.commands.run.await_args.kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_command_exit_exception_absorbed(self, fake_e2b, fake_async_sandbox):
        exc = fake_e2b.CommandExitException(stdout="out", stderr="err", exit_code=7)
        fake_async_sandbox.commands.run = AsyncMock(side_effect=exc)
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        result = await client.commands_run("bad")
        assert result.exit_code == 7
        assert result.stdout == "out"
        assert result.stderr == "err"

    @pytest.mark.asyncio
    async def test_none_fields_coerced(self, fake_async_sandbox):
        # Vendor sometimes returns None for optional fields.
        fake_async_sandbox.commands.run = AsyncMock(
            return_value=SimpleNamespace(stdout=None, stderr=None, exit_code=None)
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        result = await client.commands_run("cmd")
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_set_timeout_called_after_command(self, fake_async_sandbox):
        """After each command the idle timer is renewed. Regression check."""
        fake_async_sandbox.commands.run = AsyncMock(return_value=_cmd_return(""))
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=77, execute_timeout=30)
        await client.commands_run("cmd")
        fake_async_sandbox.set_timeout.assert_awaited_once_with(77)

    @pytest.mark.asyncio
    async def test_set_timeout_called_even_on_command_exit(self, fake_e2b, fake_async_sandbox):
        """Idle renewal must fire even when CommandExitException absorbed.

        BUG PROBE: if the renewal lived inside a success branch it would
        silently skip on failures; over time the sandbox would idle out
        mid-session. It must run unconditionally.
        """
        fake_async_sandbox.commands.run = AsyncMock(
            side_effect=fake_e2b.CommandExitException(exit_code=1)
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=77, execute_timeout=30)
        await client.commands_run("cmd")
        fake_async_sandbox.set_timeout.assert_awaited_once_with(77)

    @pytest.mark.asyncio
    async def test_duration_is_measured(self, fake_async_sandbox, monkeypatch):
        clock = [1000.0]

        def fake_time():
            v = clock[0]
            clock[0] += 2.5
            return v

        loop = asyncio.get_event_loop()
        monkeypatch.setattr(loop, "time", fake_time)
        fake_async_sandbox.commands.run = AsyncMock(return_value=_cmd_return(""))
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        result = await client.commands_run("cmd")
        # start=1000, end=1002.5 → duration=2.5.
        assert abs(result.duration - 2.5) < 0.01

    @pytest.mark.asyncio
    async def test_closed_client_raises(self, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            await client.commands_run("cmd")


# ---------------------------------------------------------------------------
# upload_path / download_path
# ---------------------------------------------------------------------------


class TestUploadPath:

    @pytest.mark.asyncio
    async def test_uploads_file_via_write(self, tmp_path, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        local = tmp_path / "f.txt"
        local.write_bytes(b"hello")
        await client.upload_path(local, "/remote/f.txt")
        fake_async_sandbox.files.write.assert_awaited_once_with(
            "/remote/f.txt", b"hello", user="root"
        )

    @pytest.mark.asyncio
    async def test_uploads_directory_via_tar(self, tmp_path, fake_async_sandbox, monkeypatch):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        local = tmp_path / "dir"
        local.mkdir()

        called = AsyncMock()
        monkeypatch.setattr(_sandbox, "upload_directory_via_tar", called)
        await client.upload_path(local, "/remote/dir")
        called.assert_awaited_once_with(client, local, "/remote/dir")


class TestDownloadPath:

    @pytest.mark.asyncio
    async def test_downloads_file(self, tmp_path, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.FILE
        )
        fake_async_sandbox.files.read = AsyncMock(return_value=b"payload")

        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out.txt"
        await client.download_path("/remote/f.txt", dst)
        assert dst.read_bytes() == b"payload"

    @pytest.mark.asyncio
    async def test_downloads_directory(self, tmp_path, fake_e2b, fake_async_sandbox, monkeypatch):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.DIR
        )
        called = AsyncMock()
        monkeypatch.setattr(_sandbox, "download_directory_via_tar", called)

        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out"
        await client.download_path("/remote/dir", dst)
        called.assert_awaited_once_with(client, "/remote/dir", dst)

    @pytest.mark.asyncio
    async def test_default_refuses_to_clobber_existing_file(self, tmp_path, fake_e2b, fake_async_sandbox):
        """Default ``on_existing='error'`` raises on a pre-existing destination file."""
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.FILE
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out.txt"
        dst.write_text("preexisting")
        with pytest.raises(FileExistsError):
            await client.download_path("/r/f", dst)

    @pytest.mark.asyncio
    async def test_on_existing_replace_overwrites_file(self, tmp_path, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.FILE
        )
        fake_async_sandbox.files.read = AsyncMock(return_value=b"new")
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out.txt"
        dst.write_bytes(b"old")
        await client.download_path("/r/f", dst, on_existing="replace")
        assert dst.read_bytes() == b"new"

    @pytest.mark.asyncio
    async def test_on_existing_merge_preserves_siblings(
        self, tmp_path, fake_e2b, fake_async_sandbox, monkeypatch
    ):
        """``on_existing="merge"`` must overlay onto an existing dir.

        Sibling entries that are not part of the downloaded payload must
        survive. This is the behaviour Hermes' ``copy_out`` relies on
        for repeated downloads into the same host workspace.
        """
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.DIR
        )
        # Stub the tar-transfer out; we only care that reserve_local_destination
        # left the directory in place when merge mode was requested.
        called = AsyncMock()
        monkeypatch.setattr(_sandbox, "download_directory_via_tar", called)

        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out"
        dst.mkdir()
        sibling = dst / "pre_existing.txt"
        sibling.write_text("still here")

        await client.download_path("/remote/dir", dst, on_existing="merge")

        # Existing sibling untouched; tar downloader was invoked to overlay.
        assert sibling.read_text() == "still here"
        called.assert_awaited_once_with(client, "/remote/dir", dst)

    @pytest.mark.asyncio
    async def test_on_existing_error_raises_on_nonempty_dir(
        self, tmp_path, fake_e2b, fake_async_sandbox
    ):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.DIR
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        dst = tmp_path / "out"
        dst.mkdir()
        (dst / "sentinel.txt").write_text("x")
        with pytest.raises(FileExistsError):
            await client.download_path("/remote/dir", dst, on_existing="error")


# ---------------------------------------------------------------------------
# read_file_bytes / write_file_bytes
# ---------------------------------------------------------------------------


class TestReadWriteBytes:

    @pytest.mark.asyncio
    async def test_read_file_bytes_passes_user_and_format(self, fake_async_sandbox):
        fake_async_sandbox.files.read = AsyncMock(return_value=b"data")
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        result = await client.read_file_bytes("/remote")
        assert result == b"data"
        fake_async_sandbox.files.read.assert_awaited_once_with(
            "/remote", format="bytes", user="root"
        )

    @pytest.mark.asyncio
    async def test_read_none_becomes_empty(self, fake_async_sandbox):
        fake_async_sandbox.files.read = AsyncMock(return_value=None)
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        assert await client.read_file_bytes("/r") == b""

    @pytest.mark.asyncio
    async def test_read_non_bytes_coerced(self, fake_async_sandbox):
        fake_async_sandbox.files.read = AsyncMock(return_value=bytearray(b"x"))
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        assert await client.read_file_bytes("/r") == b"x"

    @pytest.mark.asyncio
    async def test_write_passes_user(self, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        await client.write_file_bytes("/r/f", b"data")
        fake_async_sandbox.files.write.assert_awaited_once_with(
            "/r/f", b"data", user="root"
        )

    @pytest.mark.asyncio
    async def test_closed_raises(self, fake_async_sandbox):
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            await client.read_file_bytes("/r")
        with pytest.raises(RuntimeError, match="closed"):
            await client.write_file_bytes("/r", b"")


# ---------------------------------------------------------------------------
# _is_remote_dir
# ---------------------------------------------------------------------------


class TestIsRemoteDir:

    @pytest.mark.asyncio
    async def test_dir(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.DIR
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        assert await client._is_remote_dir("/r") is True

    @pytest.mark.asyncio
    async def test_file(self, fake_e2b, fake_async_sandbox):
        fake_async_sandbox.files.get_info.return_value = SimpleNamespace(
            type=fake_e2b.FileType.FILE
        )
        client = CubeSandboxClient(fake_async_sandbox, idle_timeout=60, execute_timeout=30)
        assert await client._is_remote_dir("/r") is False
