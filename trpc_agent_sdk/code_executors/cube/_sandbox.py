# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cube/E2B sandbox client.

Owns the :class:`AsyncSandbox` lifetime and exposes the few primitives
the SDK code executor and workspace runtime are built on top of:

- **Lifecycle** — :meth:`open_new`, :meth:`open_existing`, :meth:`close`,
  :meth:`destroy`, :meth:`assert_running`, :meth:`set_timeout`.
- **Command execution** — :meth:`commands_run` (always returns a
  structured :class:`CubeCommandResult`; non-zero exit codes never
  raise).
- **File primitives** — :meth:`upload_path` / :meth:`download_path`
  (auto-dispatch file vs directory; directories go through the tar
  protocol in :mod:`._transfer`), plus
  :meth:`read_file_bytes` / :meth:`write_file_bytes`.

Pure path/quote helpers live in :mod:`._paths`. The tar-based directory
transfer protocol lives in :mod:`._transfer`. The e2b vendor seam
(lazy import + ``user=`` constant) lives in :mod:`._e2b`. This module
is intentionally the only place that holds an ``AsyncSandbox`` reference
and therefore is the only place that needs to absorb e2b's quirks
(``CommandExitException`` / ``"STOPPED"`` /
``SandboxNotFoundException``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Mapping
from typing import Optional

from trpc_agent_sdk.log import logger

from ._e2b import _GUEST_USER
from ._e2b import _import_e2b
from ._paths import wrap_stdin_heredoc
from ._transfer import OnExisting
from ._transfer import download_directory_via_tar
from ._transfer import reserve_local_destination
from ._transfer import upload_directory_via_tar
from ._types import CubeCodeExecutorConfig

if TYPE_CHECKING:
    from e2b_code_interpreter import AsyncSandbox


@dataclass
class CubeCommandResult:
    """Structured result of a single command run inside the sandbox.

    Non-zero exit codes are returned, not raised. This intentionally
    absorbs the e2b SDK's :class:`CommandExitException` so callers always
    see a structured return value (matches the local/container
    code-executor behavior).
    """

    stdout: str
    stderr: str
    exit_code: int
    duration: float


class CubeSandboxClient:
    """Thin public wrapper around an :class:`AsyncSandbox` with SDK semantics.

    Holds the lifetime of one Cube/E2B remote sandbox and exposes the
    primitives :class:`CubeCodeExecutor` and :class:`CubeWorkspaceRuntime`
    are built on top of. External adapters (e.g. hermes' ``HarnessSandbox``)
    can also depend on this directly without pulling in the workspace
    runtime contract.

    Semantics:

    - ``close()`` is a no-op (drops the local handle only).
    - ``destroy()`` is the only place that calls ``kill()`` and tolerates
      the "already STOPPED" / :class:`SandboxNotFoundException`
      workarounds.
    - ``commands_run()`` always returns a structured result; non-zero
      exit codes never raise.
    - ``upload_path`` / ``download_path`` auto-dispatch file vs directory
      and preserve symlinks/perms via tar (see :mod:`._transfer`).

    Construct via :meth:`open_new` or :meth:`open_existing` rather than
    the constructor directly.
    """

    def __init__(self, sandbox: "AsyncSandbox", *, idle_timeout: int, execute_timeout: float):
        self._sbx: Optional["AsyncSandbox"] = sandbox
        self._idle_timeout = idle_timeout
        self._execute_timeout = execute_timeout

    @property
    def sandbox_id(self) -> str:
        sbx = self._require()
        return sbx.sandbox_id

    @classmethod
    async def open_new(cls, cfg: CubeCodeExecutorConfig) -> "CubeSandboxClient":
        """Create a brand-new remote sandbox."""
        e2b = _import_e2b()
        sbx = await e2b.AsyncSandbox.create(
            template=cfg.resolve_template(),
            api_url=cfg.resolve_api_url(),
            api_key=cfg.resolve_api_key(),
            timeout=cfg.idle_timeout,
        )
        return cls(sbx, idle_timeout=cfg.idle_timeout, execute_timeout=cfg.execute_timeout)

    @classmethod
    async def open_existing(cls, sandbox_id: str, cfg: CubeCodeExecutorConfig) -> "CubeSandboxClient":
        """Attach to an existing remote sandbox and assert it is RUNNING.

        Raises:
            SandboxNotFoundException: the sandbox is gone (caller decides
                whether to clear its locator and recreate).
            SandboxException: the sandbox is in a non-RUNNING state (e.g.
                PAUSED); caller should not silently overwrite locator
                state.
        """
        e2b = _import_e2b()
        sbx = await e2b.AsyncSandbox.connect(
            sandbox_id,
            api_url=cfg.resolve_api_url(),
            api_key=cfg.resolve_api_key(),
        )
        client = cls(sbx, idle_timeout=cfg.idle_timeout, execute_timeout=cfg.execute_timeout)
        await client.assert_running()
        return client

    def close(self) -> None:
        """Drop the local sandbox handle. Never kills the remote sandbox."""
        self._sbx = None

    async def destroy(self) -> None:
        """Explicitly kill the remote sandbox.

        Tolerates :class:`SandboxNotFoundException` (already gone) and
        :class:`SandboxException` whose message contains ``"STOPPED"``
        (Cube refuses kill on already-stopped instances). Other errors
        propagate.
        """
        sbx = self._sbx
        if sbx is None:
            return
        e2b = _import_e2b()
        try:
            await sbx.kill()
        except e2b.SandboxNotFoundException as exc:
            logger.info("Cube sandbox %s already gone during kill: %s", sbx.sandbox_id, exc)
        except e2b.SandboxException as exc:
            if "STOPPED" in str(exc):
                logger.info("Cube sandbox %s already stopped during kill: %s", sbx.sandbox_id, exc)
            else:
                raise
        finally:
            self._sbx = None

    async def assert_running(self) -> None:
        """Verify the sandbox is RUNNING; reject PAUSED and surface stale ids.

        - ``get_info`` raises :class:`SandboxNotFoundException` if
          killed/expired.
        - PAUSED state raises :class:`SandboxException` so callers do
          not silently discard operator-managed pause state.
        """
        sbx = self._require()
        e2b = _import_e2b()
        info = await sbx.get_info(request_timeout=self._execute_timeout)
        if info.state != e2b.SandboxState.RUNNING:
            raise e2b.SandboxException(f"Cube sandbox {sbx.sandbox_id} is in state {info.state.value!r}, "
                                       f"expected {e2b.SandboxState.RUNNING.value!r}.")

    async def set_timeout(self, seconds: int) -> None:
        """Best-effort idle-timeout renewal.

        ``seconds`` is integer because the underlying e2b ``set_timeout``
        takes integer seconds; previously a ``float`` would be silently
        truncated by ``int(...)`` (e.g. ``0.9`` → ``0``, which most
        vendor APIs interpret as "no timeout" / "expire immediately").
        """
        sbx = self._require()
        try:
            await sbx.set_timeout(seconds)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Cube sandbox %s set_timeout failed: %s", sbx.sandbox_id, exc)

    async def commands_run(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        stdin: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> CubeCommandResult:
        """Run a single shell command and return a structured result.

        Non-zero exit codes never raise. Stdin (when provided) is encoded
        as a bash heredoc because the e2b SDK's ``stdin`` flag is not a
        data channel.
        """
        sbx = self._require()
        e2b = _import_e2b()
        if stdin is not None:
            command = wrap_stdin_heredoc(command, stdin)
        kwargs: dict[str, Any] = {
            "envs": dict(env or {}),
            "user": _GUEST_USER,
            "timeout": float(timeout if timeout is not None else self._execute_timeout),
        }
        if cwd:
            kwargs["cwd"] = cwd

        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            result = await sbx.commands.run(command, **kwargs)
        except e2b.CommandExitException as exc:
            result = exc
        duration = loop.time() - start

        await self.set_timeout(self._idle_timeout)

        return CubeCommandResult(
            stdout=str(getattr(result, "stdout", "") or ""),
            stderr=str(getattr(result, "stderr", "") or ""),
            exit_code=int(getattr(result, "exit_code", 0) or 0),
            duration=float(duration),
        )

    async def upload_path(self, local: Path, remote_abs: str) -> None:
        """Upload a host file or directory to an absolute remote path.

        Directories go through the tar protocol so symlinks, permissions
        and special files are preserved in one round-trip. Single files
        and directories alike route through the client's own
        :meth:`write_file_bytes` / :meth:`commands_run`, so all e2b
        ``user=`` plumbing and ``CommandExitException`` absorption stays
        DRY.
        """
        if local.is_dir():
            await upload_directory_via_tar(self, local, remote_abs)
            return
        await self.write_file_bytes(remote_abs, local.read_bytes())

    async def download_path(
        self,
        remote_abs: str,
        local: Path,
        *,
        on_existing: OnExisting = "error",
    ) -> None:
        """Download a remote file or directory to a host path.

        Args:
            remote_abs: Absolute remote path to download.
            local: Host destination path.
            on_existing: Collision policy when ``local`` already exists.
                ``"error"`` (default) refuses to clobber; ``"replace"``
                removes the existing destination first; ``"merge"``
                overlays the tar payload onto an existing directory
                (siblings not in the payload are preserved). For
                file/symlink destinations ``"merge"`` behaves like
                ``"replace"`` because a regular file cannot be merged
                into. Missing destinations and empty directories are
                accepted regardless of this flag.
        """
        is_remote_dir = await self._is_remote_dir(remote_abs)

        reserve_local_destination(local, on_existing=on_existing)
        local.parent.mkdir(parents=True, exist_ok=True)
        if is_remote_dir:
            await download_directory_via_tar(self, remote_abs, local)
            return
        local.write_bytes(await self.read_file_bytes(remote_abs))

    async def read_file_bytes(self, remote_abs: str) -> bytes:
        """Read a remote file's raw bytes."""
        sbx = self._require()
        data = await sbx.files.read(remote_abs, format="bytes", user=_GUEST_USER)
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def write_file_bytes(self, remote_abs: str, data: bytes) -> None:
        """Write raw bytes to a remote file."""
        sbx = self._require()
        await sbx.files.write(remote_abs, data, user=_GUEST_USER)

    async def _is_remote_dir(self, remote_abs: str) -> bool:
        """Return whether ``remote_abs`` resolves to a directory inside the sandbox."""
        sbx = self._require()
        e2b = _import_e2b()
        info = await sbx.files.get_info(remote_abs, user=_GUEST_USER)
        return info.type == e2b.FileType.DIR

    def _require(self) -> "AsyncSandbox":
        if self._sbx is None:
            raise RuntimeError("CubeSandboxClient is closed.")
        return self._sbx
