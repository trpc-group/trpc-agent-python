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
transfer protocol lives in :mod:`._transfer`. This module is
intentionally the only place that holds an ``AsyncSandbox`` reference
and therefore is the only place that needs to absorb e2b's quirks
(``CommandExitException`` / ``"STOPPED"`` /
``SandboxNotFoundException``).

``e2b_code_interpreter`` is imported at module top-level. It is
distributed as the optional ``[cube]`` extra (``pip install
trpc-agent-py[cube]``); any code path that reaches this module is by
construction a Cube-backend caller and therefore must have the extra
installed. A missing extra surfaces as a normal :class:`ImportError`
at import time, which is the right place for the failure to land.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import TypeVar

import e2b_code_interpreter as e2b
from e2b_code_interpreter import AsyncSandbox

from trpc_agent_sdk.log import logger

from ._paths import wrap_stdin_heredoc
from ._transfer import OnExisting
from ._transfer import download_directory_via_tar
from ._transfer import reserve_local_destination
from ._transfer import upload_directory_via_tar
from ._types import CubeClientConfig

# The unix user we run sandbox commands and FS ops as. Standard cube/e2b
# templates ship with `root`; downstream callers do not need to override
# this and we deliberately do not expose a knob to keep the surface small.
_GUEST_USER = "root"
_T = TypeVar("_T")


def _is_stale_sandbox_error(exc: BaseException) -> bool:
    """Return whether ``exc`` means the remote sandbox disappeared."""
    if isinstance(exc, e2b.SandboxNotFoundException):
        return True
    message = str(exc).lower()
    return "code.unknown" in message and "requested resource does not exist" in message


@dataclass
class CubeCommandResult:
    """Structured result of a single command run inside the sandbox.

    Non-zero exit codes are returned, not raised. This intentionally
    absorbs the e2b SDK's :class:`CommandExitException` so callers always
    see a structured return value (matches the local/container
    code-executor behavior).

    The ``timed_out`` flag distinguishes a deadline-exceeded run from a
    plain non-zero exit: e2b raises :class:`e2b.TimeoutException` when
    the per-command ``timeout`` is hit, and ``commands_run`` catches it
    so callers never see the raw exception. When ``timed_out`` is ``True``
    the process has already been killed by e2b; ``exit_code`` is set to
    ``-1`` (mirroring the local/container executors' convention) and
    ``stderr`` carries a short, hand-written description rather than the
    e2b SDK's verbose original message.
    """

    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False


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
      exit codes never raise, and deadline-exceeded runs surface as
      ``CubeCommandResult(timed_out=True, exit_code=-1)`` rather than
      propagating e2b's :class:`TimeoutException`.
    - ``upload_path`` / ``download_path`` auto-dispatch file vs directory
      and preserve symlinks/perms via tar (see :mod:`._transfer`).

    Construct via :meth:`open_new` or :meth:`open_existing` rather than
    the constructor directly.
    """

    def __init__(self, sandbox: AsyncSandbox, cfg: CubeClientConfig):
        self._sbx: Optional[AsyncSandbox] = sandbox
        self._cfg = cfg
        self._recreate_cfg = replace(cfg, sandbox_id=None)
        self._idle_timeout = cfg.idle_timeout
        self._execute_timeout = cfg.execute_timeout
        self._recreate_lock = asyncio.Lock()

    @property
    def sandbox_id(self) -> str:
        return self._require().sandbox_id

    @classmethod
    async def open_new(cls, cfg: CubeClientConfig) -> "CubeSandboxClient":
        """Create a brand-new remote sandbox."""
        sbx = await e2b.AsyncSandbox.create(
            template=cfg.resolve_template(),
            api_url=cfg.resolve_api_url(),
            api_key=cfg.resolve_api_key(),
            timeout=cfg.idle_timeout,
        )
        return cls(sbx, cfg)

    @classmethod
    async def open_existing(
        cls,
        cfg: CubeClientConfig,
    ) -> "CubeSandboxClient":
        """Attach to an existing remote sandbox and assert it is RUNNING.

        Raises:
            SandboxNotFoundException: the sandbox is gone (caller decides
                whether to clear its locator and recreate).
            SandboxException: the sandbox is in a non-RUNNING state (e.g.
                PAUSED); caller should not silently overwrite locator
                state.
        """
        if not cfg.sandbox_id:
            raise ValueError("CubeSandboxClient.open_existing requires cfg.sandbox_id")
        sbx = await e2b.AsyncSandbox.connect(
            cfg.sandbox_id,
            api_url=cfg.resolve_api_url(),
            api_key=cfg.resolve_api_key(),
        )
        client = cls(sbx, cfg)
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

    async def recreate(self) -> None:
        """Explicitly replace the current sandbox with a fresh one."""
        async with self._recreate_lock:
            await self._recreate_locked()

    async def assert_running(self) -> None:
        """Verify the sandbox is RUNNING; reject PAUSED and surface stale ids.

        - ``get_info`` raises :class:`SandboxNotFoundException` if
          killed/expired.
        - PAUSED state raises :class:`SandboxException` so callers do
          not silently discard operator-managed pause state.
        """
        await self._with_recovery(self._assert_running_once)

    async def set_timeout(self, seconds: int) -> None:
        """Best-effort idle-timeout renewal.

        ``seconds`` is integer because the underlying e2b ``set_timeout``
        takes integer seconds; previously a ``float`` would be silently
        truncated by ``int(...)`` (e.g. ``0.9`` → ``0``, which most
        vendor APIs interpret as "no timeout" / "expire immediately").
        """
        await self._with_recovery(lambda: self._set_timeout_once(seconds))

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

        Non-zero exit codes never raise. Deadline-exceeded runs never
        raise either: the e2b SDK's :class:`e2b.TimeoutException` is
        caught here and turned into a :class:`CubeCommandResult` with
        ``timed_out=True`` and ``exit_code=-1``, mirroring the
        local/container executors so upstream callers see a single,
        unified shape for "command did not succeed". Stdin (when
        provided) is encoded as a bash heredoc because the e2b SDK's
        ``stdin`` flag is not a data channel.
        """
        return await self._with_recovery(lambda: self._commands_run_once(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            timeout=timeout,
        ))

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
        data = await self._with_recovery(
            lambda: self._require().files.read(remote_abs, format="bytes", user=_GUEST_USER))
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def write_file_bytes(self, remote_abs: str, data: bytes) -> None:
        """Write raw bytes to a remote file."""
        await self._with_recovery(lambda: self._require().files.write(remote_abs, data, user=_GUEST_USER))

    async def _is_remote_dir(self, remote_abs: str) -> bool:
        """Return whether ``remote_abs`` resolves to a directory inside the sandbox."""
        info = await self._with_recovery(lambda: self._require().files.get_info(remote_abs, user=_GUEST_USER))
        return info.type == e2b.FileType.DIR

    async def _assert_running_once(self) -> None:
        sbx = self._require()
        info = await sbx.get_info(request_timeout=self._execute_timeout)
        if info.state != e2b.SandboxState.RUNNING:
            raise e2b.SandboxException(f"Cube sandbox {sbx.sandbox_id} is in state {info.state.value!r}, "
                                       f"expected {e2b.SandboxState.RUNNING.value!r}.")

    async def _set_timeout_once(self, seconds: int) -> None:
        sbx = self._require()
        try:
            await sbx.set_timeout(seconds)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Cube sandbox %s set_timeout failed: %s", sbx.sandbox_id, exc)

    async def _commands_run_once(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        stdin: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> CubeCommandResult:
        sbx = self._require()
        if stdin is not None:
            command = wrap_stdin_heredoc(command, stdin)
        timeout_sec = float(timeout if timeout is not None else self._execute_timeout)
        kwargs: dict[str, Any] = {
            "envs": dict(env or {}),
            "user": _GUEST_USER,
            "timeout": timeout_sec,
        }
        if cwd:
            kwargs["cwd"] = cwd

        loop = asyncio.get_running_loop()
        start = loop.time()
        timed_out = False
        try:
            result = await sbx.commands.run(command, **kwargs)
        except e2b.CommandExitException as exc:
            result = exc
        except BaseException as exc:
            # Timeouts surface here as one of several types depending on
            # which transport layer fires first:
            #   - e2b.TimeoutException (vendor SDK layer)
            #   - httpcore.ReadTimeout / httpcore.TimeoutException
            #     (transport layer — can race ahead of the e2b mapping on
            #     slow Cube deployments)
            # The httpcore path is only reachable via the transitive
            # dependency, so we match by type-name instead of importing
            # httpcore just to subclass-check. We still re-raise anything
            # that is not timeout-flavoured so real errors stay visible.
            name = type(exc).__name__
            if "Timeout" not in name:
                raise
            result = None
            timed_out = True
        duration = loop.time() - start

        await self.set_timeout(self._idle_timeout)

        if timed_out:
            return CubeCommandResult(
                stdout="",
                stderr=f"Command timed out after {timeout_sec:g}s",
                exit_code=-1,
                duration=float(duration),
                timed_out=True,
            )
        return CubeCommandResult(
            stdout=str(getattr(result, "stdout", "") or ""),
            stderr=str(getattr(result, "stderr", "") or ""),
            exit_code=int(getattr(result, "exit_code", 0) or 0),
            duration=float(duration),
        )

    async def _with_recovery(self, op: Callable[[], Awaitable[_T]]) -> _T:
        sandbox = self._sbx
        try:
            return await op()
        except Exception as exc:
            if not self._cfg.auto_recover or not _is_stale_sandbox_error(exc):
                raise
            logger.info("Cube sandbox expired; recreating sandbox client: %s", exc)
            async with self._recreate_lock:
                if self._sbx is sandbox:
                    await self._recreate_locked()
            return await op()

    async def _recreate_locked(self) -> None:
        if self._sbx is not None:
            await self.destroy()
        fresh = await type(self).open_new(self._recreate_cfg)
        self._sbx = fresh._require()
        fresh.close()
        logger.info("Cube sandbox client using sandbox: %s", self.sandbox_id)

    def _require(self) -> AsyncSandbox:
        if self._sbx is None:
            raise RuntimeError("CubeSandboxClient is closed.")
        return self._sbx


async def create_cube_sandbox_client(cfg: CubeClientConfig) -> CubeSandboxClient:
    """Create or attach a Cube sandbox client from config."""
    if cfg.sandbox_id:
        return await CubeSandboxClient.open_existing(cfg)
    return await CubeSandboxClient.open_new(cfg)
