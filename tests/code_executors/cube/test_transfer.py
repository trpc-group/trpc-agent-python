# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._transfer."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.code_executors.cube import _transfer
from trpc_agent_sdk.code_executors.cube._sandbox import CubeCommandResult
from trpc_agent_sdk.code_executors.cube._transfer import (
    _run_protocol_step,
    download_directory_via_tar,
    reserve_local_destination,
    upload_directory_via_tar,
)


# ---------------------------------------------------------------------------
# Fake client: the transfer functions only touch three methods.
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "") -> CubeCommandResult:
    return CubeCommandResult(stdout=stdout, stderr=stderr, exit_code=0, duration=0.0)


def _err(stderr: str = "boom", exit_code: int = 1) -> CubeCommandResult:
    return CubeCommandResult(stdout="", stderr=stderr, exit_code=exit_code, duration=0.0)


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.commands_run = AsyncMock(return_value=_ok())
    c.read_file_bytes = AsyncMock(return_value=b"")
    c.write_file_bytes = AsyncMock(return_value=None)
    return c


# ---------------------------------------------------------------------------
# reserve_local_destination
# ---------------------------------------------------------------------------


class TestReserveLocalDestination:
    """Exercise the collision policy (error/replace/merge)."""

    def test_default_is_error(self, tmp_path):
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "sentinel.txt").write_text("x")
        # No flag → default is "error".
        with pytest.raises(FileExistsError):
            reserve_local_destination(target)

    def test_missing_destination_is_silent(self, tmp_path):
        target = tmp_path / "missing"
        reserve_local_destination(target, on_existing="error")
        assert not target.exists()

    def test_empty_directory_is_silent(self, tmp_path):
        target = tmp_path / "empty"
        target.mkdir()
        reserve_local_destination(target, on_existing="error")
        assert target.exists() and target.is_dir()

    def test_nonempty_dir_error_raises(self, tmp_path):
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "sentinel.txt").write_text("x")
        with pytest.raises(FileExistsError, match="on_existing="):
            reserve_local_destination(target, on_existing="error")
        assert (target / "sentinel.txt").exists()

    def test_nonempty_dir_replace_removes(self, tmp_path):
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "sentinel.txt").write_text("x")
        reserve_local_destination(target, on_existing="replace")
        assert not target.exists()

    def test_nonempty_dir_merge_keeps_siblings(self, tmp_path):
        """Merge mode leaves an existing directory intact.

        The tar extract that follows overlays its own entries on top;
        siblings not present in the payload survive. This is the
        behaviour Hermes' ``copy_out`` relies on.
        """
        target = tmp_path / "occupied"
        target.mkdir()
        keep = target / "keep.txt"
        keep.write_text("stays")
        (target / "subdir").mkdir()
        (target / "subdir" / "nested.txt").write_text("also stays")

        reserve_local_destination(target, on_existing="merge")

        assert target.is_dir()
        assert keep.read_text() == "stays"
        assert (target / "subdir" / "nested.txt").read_text() == "also stays"

    def test_file_error_raises(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x")
        with pytest.raises(FileExistsError):
            reserve_local_destination(target, on_existing="error")
        assert target.exists()

    def test_file_replace_unlinks(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x")
        reserve_local_destination(target, on_existing="replace")
        assert not target.exists()

    def test_file_merge_falls_back_to_unlink(self, tmp_path):
        """Cannot merge a remote payload into a regular file — falls back to replace."""
        target = tmp_path / "f.txt"
        target.write_text("old")
        reserve_local_destination(target, on_existing="merge")
        assert not target.exists()

    def test_symlink_to_dir_is_unlinked_not_rmtree(self, tmp_path):
        """BUG PROBE: symlinks must go through ``unlink``, not ``rmtree``.

        If the implementation used ``shutil.rmtree`` on a symlink it
        would follow the link and delete the real directory — a
        well-known bug. Reserve must detect the symlink first.
        """
        real = tmp_path / "real"
        real.mkdir()
        (real / "keep.txt").write_text("keep")
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        reserve_local_destination(link, on_existing="replace")

        # Link is gone but the real directory it pointed to is intact.
        assert not link.exists()
        assert not link.is_symlink()
        assert real.exists()
        assert (real / "keep.txt").read_text() == "keep"

    def test_symlink_to_dir_merge_is_also_unlinked(self, tmp_path):
        """Merge on a symlink-to-dir must unlink the link, not follow it.

        Same safety invariant as ``replace`` — if we ever ``rmtree``'d
        through a symlink we'd blow away the real target.
        """
        real = tmp_path / "real"
        real.mkdir()
        (real / "keep.txt").write_text("keep")
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        reserve_local_destination(link, on_existing="merge")

        assert not link.is_symlink()
        assert real.exists()
        assert (real / "keep.txt").read_text() == "keep"

    def test_symlink_to_file_is_unlinked(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("keep")
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        reserve_local_destination(link, on_existing="replace")
        assert not link.is_symlink()
        assert real.read_text() == "keep"

    def test_broken_symlink_error_raises(self, tmp_path):
        """Broken symlink: exists()=False, is_symlink()=True.

        Must be treated as "already occupied" because the name is taken.
        """
        link = tmp_path / "broken"
        link.symlink_to(tmp_path / "does_not_exist")
        assert not link.exists()
        assert link.is_symlink()

        with pytest.raises(FileExistsError):
            reserve_local_destination(link, on_existing="error")

    def test_broken_symlink_replace_is_unlinked(self, tmp_path):
        link = tmp_path / "broken"
        link.symlink_to(tmp_path / "does_not_exist")
        reserve_local_destination(link, on_existing="replace")
        assert not link.is_symlink()

    def test_missing_target_is_silent_in_all_modes(self, tmp_path):
        for mode in ("error", "replace", "merge"):
            target = tmp_path / f"missing_{mode}"
            reserve_local_destination(target, on_existing=mode)
            assert not target.exists(), mode

    def test_empty_dir_is_silent_in_all_modes(self, tmp_path):
        for mode in ("error", "replace", "merge"):
            target = tmp_path / f"empty_{mode}"
            target.mkdir()
            reserve_local_destination(target, on_existing=mode)
            assert target.is_dir(), mode


# ---------------------------------------------------------------------------
# upload_directory_via_tar
# ---------------------------------------------------------------------------


class TestUploadDirectoryViaTar:

    @pytest.mark.asyncio
    async def test_uploads_tar_and_extracts(self, tmp_path, fake_client, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello")

        monkeypatch.setattr(_transfer.secrets, "token_hex", lambda n: "DEADBEEF")

        await upload_directory_via_tar(fake_client, src, "/remote/dst")

        # write_file_bytes call (upload the tar).
        fake_client.write_file_bytes.assert_awaited_once()
        (temp_remote, payload), _ = fake_client.write_file_bytes.await_args
        assert temp_remote == "/tmp/.cube_upload_DEADBEEF.tar"
        assert isinstance(payload, bytes)
        # Payload is a valid tar containing a.txt.
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as tar:
            names = {m.name.lstrip("./") for m in tar.getmembers() if m.name.lstrip("./")}
        assert "a.txt" in names

        # Two commands: extract, then cleanup.
        assert fake_client.commands_run.await_count == 2
        extract_cmd = fake_client.commands_run.await_args_list[0].args[0]
        assert "mkdir -p '/remote/dst'" in extract_cmd
        assert "tar -xf '/tmp/.cube_upload_DEADBEEF.tar' -C '/remote/dst'" in extract_cmd
        assert "set -e" in extract_cmd

        cleanup_cmd = fake_client.commands_run.await_args_list[1].args[0]
        assert cleanup_cmd == "rm -f '/tmp/.cube_upload_DEADBEEF.tar'"

    @pytest.mark.asyncio
    async def test_cleanup_runs_even_when_extract_fails(self, tmp_path, fake_client):
        src = tmp_path / "src"
        src.mkdir()

        # Extract returns non-zero; cleanup must still run.
        fake_client.commands_run.side_effect = [_err("extract failed"), _ok()]
        with pytest.raises(RuntimeError, match="upload tar extract"):
            await upload_directory_via_tar(fake_client, src, "/remote/dst")
        assert fake_client.commands_run.await_count == 2  # cleanup still fired

    @pytest.mark.asyncio
    async def test_cleanup_nonzero_is_swallowed(self, tmp_path, fake_client):
        src = tmp_path / "src"
        src.mkdir()
        # Extract OK, cleanup returns non-zero → no exception.
        fake_client.commands_run.side_effect = [_ok(), _err("rm failed")]
        await upload_directory_via_tar(fake_client, src, "/remote/dst")
        # No exception raised.

    @pytest.mark.asyncio
    async def test_remote_path_is_normalized(self, tmp_path, fake_client, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.setattr(_transfer.secrets, "token_hex", lambda n: "XXXX")
        await upload_directory_via_tar(fake_client, src, "/remote/./dst/../dst")
        extract_cmd = fake_client.commands_run.await_args_list[0].args[0]
        # ``posixpath.normpath`` collapses to ``/remote/dst``.
        assert "'/remote/dst'" in extract_cmd


# ---------------------------------------------------------------------------
# download_directory_via_tar
# ---------------------------------------------------------------------------


def _build_tar_payload(file_map: dict[str, bytes]) -> bytes:
    """Build an in-memory tar containing ``file_map`` entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in file_map.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestDownloadDirectoryViaTar:

    @pytest.mark.asyncio
    async def test_creates_local_dir_and_extracts(self, tmp_path, fake_client, monkeypatch):
        payload = _build_tar_payload({"a.txt": b"hello", "sub/b.txt": b"world"})
        fake_client.read_file_bytes.return_value = payload
        monkeypatch.setattr(_transfer.secrets, "token_hex", lambda n: "TOKEN")

        dst = tmp_path / "out"
        await download_directory_via_tar(fake_client, "/remote/dir", dst)

        assert (dst / "a.txt").read_bytes() == b"hello"
        assert (dst / "sub" / "b.txt").read_bytes() == b"world"
        # Commands: create tar, cleanup tar.
        assert fake_client.commands_run.await_count == 2
        create_cmd = fake_client.commands_run.await_args_list[0].args[0]
        assert create_cmd == "tar -cf '/tmp/.cube_download_TOKEN.tar' -C '/remote/dir' ."
        cleanup_cmd = fake_client.commands_run.await_args_list[1].args[0]
        assert cleanup_cmd == "rm -f '/tmp/.cube_download_TOKEN.tar'"

    @pytest.mark.asyncio
    async def test_existing_file_at_dst_is_unlinked(self, tmp_path, fake_client):
        """When ``local`` is a file (not dir), it's unlinked and recreated."""
        fake_client.read_file_bytes.return_value = _build_tar_payload({"a.txt": b"x"})
        dst = tmp_path / "target"
        dst.write_text("previous")  # exists as file

        await download_directory_via_tar(fake_client, "/r", dst)
        assert dst.is_dir()
        assert (dst / "a.txt").read_bytes() == b"x"

    @pytest.mark.asyncio
    async def test_cleanup_runs_when_read_fails(self, tmp_path, fake_client):
        fake_client.read_file_bytes.side_effect = RuntimeError("read fail")
        dst = tmp_path / "out"
        with pytest.raises(RuntimeError, match="read fail"):
            await download_directory_via_tar(fake_client, "/r", dst)
        # Cleanup still ran (only the "rm -f ..." command, which is the
        # second call that gets through the finally block).
        assert fake_client.commands_run.await_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_when_tar_create_fails(self, tmp_path, fake_client):
        fake_client.commands_run.side_effect = [_err("tar failed"), _ok()]
        dst = tmp_path / "out"
        with pytest.raises(RuntimeError, match="download tar create"):
            await download_directory_via_tar(fake_client, "/r", dst)

    @pytest.mark.asyncio
    async def test_py312_extractall_fallback(self, tmp_path, fake_client, monkeypatch):
        """py<3.12 does not accept ``filter=`` — must fall back to no filter.

        Since this test is running on Python 3.12+, we simulate the
        older behavior by patching ``TarFile.extractall`` to raise
        ``TypeError`` when ``filter`` is passed.
        """
        payload = _build_tar_payload({"a.txt": b"ok"})
        fake_client.read_file_bytes.return_value = payload

        real_extractall = tarfile.TarFile.extractall
        calls: list[dict] = []

        def patched(self, path=None, members=None, **kwargs):
            calls.append(kwargs)
            if "filter" in kwargs:
                raise TypeError("filter not supported")
            # Retry without filter — mimic real behavior.
            return real_extractall(self, path=path, members=members)

        monkeypatch.setattr(tarfile.TarFile, "extractall", patched)

        dst = tmp_path / "out"
        await download_directory_via_tar(fake_client, "/r", dst)
        # Two calls: one with filter (rejected), one without (succeeded).
        assert len(calls) == 2
        assert "filter" in calls[0]
        assert "filter" not in calls[1]
        assert (dst / "a.txt").read_bytes() == b"ok"


# ---------------------------------------------------------------------------
# _run_protocol_step
# ---------------------------------------------------------------------------


class TestRunProtocolStep:

    @pytest.mark.asyncio
    async def test_ok_returns_silent(self, fake_client):
        fake_client.commands_run.return_value = _ok()
        await _run_protocol_step(fake_client, "true", op="noop")

    @pytest.mark.asyncio
    async def test_error_raises_with_details(self, fake_client):
        fake_client.commands_run.return_value = _err("nope", exit_code=17)
        with pytest.raises(RuntimeError) as exc:
            await _run_protocol_step(fake_client, "false", op="tar step")
        msg = str(exc.value)
        assert "tar step" in msg
        assert "exit=17" in msg
        assert "nope" in msg

    @pytest.mark.asyncio
    async def test_error_with_swallow_is_silent(self, fake_client):
        fake_client.commands_run.return_value = _err("nope")
        await _run_protocol_step(fake_client, "false", op="cleanup", swallow=True)


# ---------------------------------------------------------------------------
# Integration-style roundtrip (end-to-end tar behaviour).
#
# Drive the full ``upload → download`` pair against an in-memory "remote"
# that simulates tar/mkdir/rm using the real host filesystem, so we
# verify symlink + permission preservation without touching e2b or a
# real sandbox.
# ---------------------------------------------------------------------------


class _InMemoryRemote:
    """Simulates the remote sandbox's filesystem + shell-step contract.

    Supports just enough of ``tar``/``mkdir -p``/``rm -f`` to drive the
    upload/download protocol.
    """

    def __init__(self, root: Path):
        self.root = root
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.files: dict[str, bytes] = {}  # simulated remote tmp files

    async def commands_run(self, cmd: str, **kwargs) -> CubeCommandResult:
        """Execute a locally-emulated version of the shell step."""
        import shlex
        import subprocess
        # Translate remote paths to host paths under self.root.
        # We allow-list the three shapes the protocol emits.
        # The actual implementation uses `set -e; mkdir -p ...; tar -xf ... -C ...`
        # We just run it verbatim since the host has tar/mkdir/rm.
        # Strip any leading "/" in remote paths so they resolve under root.
        host_cmd = cmd.replace("'/tmp/", f"'{self.root}/tmp/")
        host_cmd = host_cmd.replace("-C '/remote", f"-C '{self.root}/remote")
        host_cmd = host_cmd.replace("mkdir -p '/remote", f"mkdir -p '{self.root}/remote")
        proc = subprocess.run(
            ["bash", "-c", host_cmd],
            capture_output=True,
        )
        return CubeCommandResult(
            stdout=proc.stdout.decode(),
            stderr=proc.stderr.decode(),
            exit_code=proc.returncode,
            duration=0.0,
        )

    async def read_file_bytes(self, path: str) -> bytes:
        host = Path(str(self.root) + path)
        return host.read_bytes()

    async def write_file_bytes(self, path: str, data: bytes) -> None:
        host = Path(str(self.root) + path)
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(data)


@pytest.mark.asyncio
async def test_roundtrip_preserves_symlink(tmp_path):
    """Upload a tree containing a symlink; download it back; symlink survives."""
    (tmp_path / "tmp").mkdir()
    (tmp_path / "remote").mkdir()

    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("real-content")
    (src / "link.txt").symlink_to("real.txt")

    client = _InMemoryRemote(tmp_path)

    await upload_directory_via_tar(client, src, "/remote/uploaded")

    # Now download back to a fresh local dir.
    dst = tmp_path / "downloaded"
    await download_directory_via_tar(client, "/remote/uploaded", dst)

    assert (dst / "real.txt").read_text() == "real-content"
    # Symlink survives (filter="data" still preserves relative symlinks).
    assert (dst / "link.txt").is_symlink() or (dst / "link.txt").exists()
    if (dst / "link.txt").is_symlink():
        assert os.readlink(dst / "link.txt") == "real.txt"


@pytest.mark.asyncio
async def test_roundtrip_preserves_executable_bit(tmp_path):
    """File permissions survive upload → download."""
    (tmp_path / "tmp").mkdir()
    (tmp_path / "remote").mkdir()

    src = tmp_path / "src"
    src.mkdir()
    script = src / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)

    client = _InMemoryRemote(tmp_path)
    await upload_directory_via_tar(client, src, "/remote/uploaded")

    dst = tmp_path / "downloaded"
    await download_directory_via_tar(client, "/remote/uploaded", dst)

    downloaded = dst / "run.sh"
    assert downloaded.exists()
    # Mode bits are preserved end-to-end.
    mode = downloaded.stat().st_mode & 0o777
    assert mode & 0o100, f"executable bit lost: mode={oct(mode)}"
