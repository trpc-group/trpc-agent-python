# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Workspace runtime backed by a Cube/E2B remote sandbox."""

from __future__ import annotations

import os
import posixpath
import re
import time
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from .._artifacts import load_artifact_helper
from .._artifacts import parse_artifact_ref
from .._base_workspace_runtime import BaseProgramRunner
from .._base_workspace_runtime import BaseWorkspaceFS
from .._base_workspace_runtime import BaseWorkspaceManager
from .._base_workspace_runtime import BaseWorkspaceRuntime
from .._base_workspace_runtime import RunEnvProvider
from .._constants import DEFAULT_TIMEOUT_SEC
from .._constants import DIR_OUT
from .._constants import DIR_RUNS
from .._constants import DIR_SKILLS
from .._constants import DIR_WORK
from .._constants import ENV_OUTPUT_DIR
from .._constants import ENV_RUN_DIR
from .._constants import ENV_SKILLS_DIR
from .._constants import ENV_WORK_DIR
from .._constants import WORKSPACE_ENV_DIR_KEY
from .._types import CodeFile
from .._types import ManifestOutput
from .._types import WorkspaceCapabilities
from .._types import WorkspaceInfo
from .._types import WorkspaceInputSpec
from .._types import WorkspaceOutputSpec
from .._types import WorkspacePutFileInfo
from .._types import WorkspaceRunProgramSpec
from .._types import WorkspaceRunResult
from .._types import WorkspaceStageOptions
from ..utils import normalize_globs
from ._code_executor import CubeCodeExecutor
from ._paths import join_remote
from ._paths import normalize_remote_relative
from ._paths import shell_quote
from ._sandbox import CubeSandboxClient
from ._types import CubeWorkspaceRuntimeConfig
from ._types import DEFAULT_EXECUTE_TIMEOUT

_RE_SAFE_ID = re.compile(r"[^a-zA-Z0-9_-]")


def _input_default_name(src: str) -> str:
    i = src.rfind("/")
    if 0 <= i < len(src) - 1:
        return src[i + 1:]
    return src


class CubeWorkspaceManager(BaseWorkspaceManager):
    """Creates per-execution workspaces under the configured ``remote_workspace`` root."""

    def __init__(self, client: CubeSandboxClient, remote_workspace: str, command_timeout: float):
        self._client = client
        self._root = posixpath.normpath(remote_workspace)
        self._timeout = command_timeout
        self._ws_paths: dict[str, WorkspaceInfo] = {}

    @override
    async def create_workspace(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> WorkspaceInfo:
        # Reuse the previously minted path for the same exec_id so callers
        # see a stable workspace location across calls. The cache is *not*
        # trusted as proof that the remote dir still exists: the sandbox is
        # remote and ephemeral, so any number of external events (operator
        # cleanup, snapshot rollback, sibling cleanup() on a shared
        # sandbox, host process restart re-attaching to a live sandbox)
        # can delete the directory while this process is unaware. To stay
        # in sync we unconditionally re-issue an idempotent ``mkdir -p``
        # for the four standard subdirs on every call. ``mkdir -p`` is a
        # no-op when the tree already exists, so the steady-state cost is
        # one round-trip; on miss the workspace heals transparently
        # instead of letting downstream put_files / collect_outputs /
        # stage_inputs fail deep inside with cryptic "No such file" errors.
        cached = self._ws_paths.get(exec_id)
        if cached is not None and cached.path:
            ws_path = cached.path
        else:
            safe = _RE_SAFE_ID.sub("_", exec_id) if exec_id else "anon"
            suffix = time.time_ns()
            ws_path = posixpath.join(self._root, f"ws_{safe}_{suffix}")

        cmd = ("set -e; "
               f"mkdir -p {shell_quote(ws_path)} "
               f"{shell_quote(posixpath.join(ws_path, DIR_WORK))} "
               f"{shell_quote(posixpath.join(ws_path, DIR_OUT))} "
               f"{shell_quote(posixpath.join(ws_path, DIR_SKILLS))} "
               f"{shell_quote(posixpath.join(ws_path, DIR_RUNS))}")
        result = await self._client.commands_run(cmd, timeout=self._timeout)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to create cube workspace: {result.stderr or result.stdout}")

        if cached is not None and cached.path == ws_path:
            logger.debug("Cube workspace reconciled: id=%s path=%s", exec_id, ws_path)
            return cached
        info = WorkspaceInfo(id=exec_id, path=ws_path)
        self._ws_paths[exec_id] = info
        logger.debug("Cube workspace created: id=%s path=%s", exec_id, ws_path)
        return info

    @override
    async def cleanup(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> None:
        info = self._ws_paths.get(exec_id)
        if not info or not info.path:
            # Drop any stale entry that lacks a usable path so retries don't
            # loop forever on a broken record.
            self._ws_paths.pop(exec_id, None)
            return
        cmd = f"rm -rf {shell_quote(info.path)}"
        result = await self._client.commands_run(cmd, timeout=self._timeout)
        if result.exit_code != 0:
            # Keep the cache entry intact so the caller can retry cleanup;
            # popping prematurely would orphan the remote workspace because
            # subsequent cleanup(exec_id) calls would hit the "unknown id"
            # no-op branch.
            raise RuntimeError(f"Failed to clean cube workspace: {result.stderr or result.stdout}")
        self._ws_paths.pop(exec_id, None)
        logger.debug("Cube workspace cleaned: id=%s path=%s", exec_id, info.path)


class CubeWorkspaceFS(BaseWorkspaceFS):
    """Workspace-scoped filesystem operations that delegate to the client."""

    def __init__(self, client: CubeSandboxClient, command_timeout: float):
        self._client = client
        self._timeout = command_timeout

    @override
    async def put_files(self,
                        ws: WorkspaceInfo,
                        files: List[WorkspacePutFileInfo],
                        ctx: Optional[InvocationContext] = None) -> None:
        for file in files:
            if not file.path:
                raise ValueError("empty file path")
            relative = normalize_remote_relative(file.path)
            remote = join_remote(ws.path, relative)
            parent = posixpath.dirname(remote)
            if parent and parent != ws.path:
                await self._mkdir(parent)
            await self._client.write_file_bytes(remote, file.content or b"")
        logger.debug("Cube put %d files into %s", len(files), ws.path)

    @override
    async def stage_directory(self,
                              ws: WorkspaceInfo,
                              src: str,
                              dst: str,
                              opt: WorkspaceStageOptions,
                              ctx: Optional[InvocationContext] = None) -> None:
        if not src:
            raise ValueError("stage_directory src is empty")
        local = Path(os.path.abspath(src))
        if not local.exists() or not local.is_dir():
            raise FileNotFoundError(f"stage_directory src not found: {src}")
        target = ws.path if not dst else join_remote(ws.path, normalize_remote_relative(dst, allow_current=True))
        await self._client.upload_path(local, target)
        if opt.read_only:
            # Surfacing chmod failures is critical: silently swallowing them
            # would leave the directory writable while callers believe the
            # read_only invariant was honoured.
            result = await self._client.commands_run(f"chmod -R a-w {shell_quote(target)}", timeout=self._timeout)
            if result.exit_code != 0:
                raise RuntimeError(f"failed to make {target} read-only: {result.stderr or result.stdout}")

    @override
    async def stage_inputs(self,
                           ws: WorkspaceInfo,
                           specs: List[WorkspaceInputSpec],
                           ctx: Optional[InvocationContext] = None) -> None:
        for spec in specs:
            if not spec.src:
                continue
            dst_rel = (spec.dst or "").strip()
            if not dst_rel:
                dst_rel = posixpath.join(DIR_WORK, "inputs", _input_default_name(spec.src))
            dst_rel = normalize_remote_relative(dst_rel)
            dst_abs = join_remote(ws.path, dst_rel)
            await self._mkdir(posixpath.dirname(dst_abs))

            if spec.src.startswith("artifact://"):
                if ctx is None:
                    raise ValueError("Context is required to load artifacts")
                ref = spec.src.removeprefix("artifact://")
                name, version = parse_artifact_ref(ref)
                content, _ = await load_artifact_helper(ctx, name, version)
                await self._client.write_file_bytes(dst_abs, content)
            elif spec.src.startswith("host://"):
                host_path = Path(spec.src.removeprefix("host://"))
                if not host_path.exists():
                    raise FileNotFoundError(f"host path not found: {host_path}")
                await self._client.upload_path(host_path, dst_abs)
            elif spec.src.startswith("workspace://"):
                rel = normalize_remote_relative(spec.src.removeprefix("workspace://"))
                src_abs = join_remote(ws.path, rel)
                await self._copy_remote(src_abs, dst_abs)
            elif spec.src.startswith("skill://"):
                rest = normalize_remote_relative(spec.src.removeprefix("skill://"))
                src_abs = join_remote(join_remote(ws.path, DIR_SKILLS), rest)
                await self._copy_remote(src_abs, dst_abs)
            else:
                raise ValueError(f"unsupported input scheme: {spec.src!r}")
        logger.debug("Cube staged %d inputs into %s", len(specs), ws.path)

    @override
    async def collect(self,
                      ws: WorkspaceInfo,
                      patterns: List[str],
                      ctx: Optional[InvocationContext] = None) -> List[CodeFile]:
        matches = await self._glob(ws.path, normalize_globs(patterns))
        files = await self._build_code_files(ws.path, matches, self._fetch_file)
        logger.debug("Cube collected %d files from %s", len(files), ws.path)
        return files

    @override
    async def collect_outputs(self,
                              ws: WorkspaceInfo,
                              spec: WorkspaceOutputSpec,
                              ctx: Optional[InvocationContext] = None) -> ManifestOutput:
        matches = await self._glob(ws.path, normalize_globs(spec.globs))
        manifest, _, _ = await self._build_manifest_output(ws.path, spec, matches, self._fetch_file, ctx)
        logger.debug("Cube collected %d outputs from %s", len(manifest.files), ws.path)
        return manifest

    async def _fetch_file(self, full_path: str, max_bytes: int) -> Tuple[bytes, int]:
        """Fetcher contract for :meth:`BaseWorkspaceFS._build_code_files` /
        :meth:`BaseWorkspaceFS._build_manifest_output`.

        Cube exposes no cheap ``stat`` RPC, so we read the full payload
        and slice locally; ``raw_size`` reflects the true on-disk size
        so the shared helpers can still report ``truncated`` /
        ``limits_hit`` accurately.
        """
        data = await self._client.read_file_bytes(full_path)
        return data[:max_bytes], len(data)

    async def _mkdir(self, remote_abs: str) -> None:
        if not remote_abs:
            return
        result = await self._client.commands_run(f"mkdir -p {shell_quote(remote_abs)}", timeout=self._timeout)
        if result.exit_code != 0:
            raise RuntimeError(f"mkdir -p failed: {result.stderr or result.stdout}")

    async def _copy_remote(self, src: str, dst: str) -> None:
        await self._mkdir(posixpath.dirname(dst))
        # Defensive rm before cp -a to avoid the long-standing POSIX
        # directory-footgun: when DST already exists as a directory,
        # ``cp -a SRC DST`` copies SRC *into* DST as DST/basename(SRC),
        # nesting stale data instead of replacing it. Removing DST first
        # makes the operation idempotent across repeated stage_inputs
        # calls targeting the same destination.
        #
        # Safety: ``dst`` is supplied exclusively by :meth:`stage_inputs`,
        # which routes the caller-provided ``spec.dst`` through
        # :func:`normalize_remote_relative` (rejects empty, absolute, and
        # ``..``-bearing relatives) and :func:`join_remote` (collapses
        # ``..`` after joining under ``ws.path``). ``shell_quote`` then
        # neutralises any shell metacharacters in the resulting absolute
        # path, and GNU ``rm``'s default ``--preserve-root`` is the
        # backstop. New callers of ``_copy_remote`` MUST funnel ``dst``
        # through the same validation chain.
        rm_result = await self._client.commands_run(
            f"rm -rf {shell_quote(dst)}",
            timeout=self._timeout,
        )
        if rm_result.exit_code != 0:
            raise RuntimeError(f"remote rm failed: {rm_result.stderr or rm_result.stdout}")
        result = await self._client.commands_run(
            f"cp -a {shell_quote(src)} {shell_quote(dst)}",
            timeout=self._timeout,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"remote cp failed: {result.stderr or result.stdout}")

    async def _glob(self, ws_path: str, patterns: List[str]) -> List[str]:
        if not patterns:
            return []
        # Patterns may contain spaces (e.g. "my dir/*.txt"). The naive shape
        # `for f in $p` first word-splits $p on IFS *and only then* globs each
        # token separately — turning "my dir/*.txt" into two patterns "my"
        # and "dir/*.txt", neither of which matches. Quoting `"$p"` would
        # suppress word-splitting but also disables globbing.
        #
        # Fix: pass patterns via a bash array (preserves spaces per element),
        # then temporarily set IFS= so the unquoted `$p` inside `matches=( $p )`
        # is *not* word-split, while bash still performs path expansion on it.
        # `compgen -G` is not used here because it does not honour `globstar`.
        array_literal = " ".join(shell_quote(p) for p in patterns)
        cmd = (f"cd {shell_quote(ws_path)} && "
               f"shopt -s globstar nullglob dotglob; "
               f"patterns=({array_literal}); "
               f"_saved_ifs=$IFS; IFS=; "
               f'for p in "${{patterns[@]}}"; do '
               f"matches=( $p ); "
               f'for f in "${{matches[@]}}"; do '
               f'[ -f "$f" ] && printf \'%s\\n\' "$(pwd)/$f"; '
               f"done; "
               f"done; "
               f"IFS=$_saved_ifs")
        result = await self._client.commands_run(cmd, timeout=self._timeout)
        if result.exit_code != 0:
            raise RuntimeError(f"glob failed: {result.stderr or result.stdout}")
        out: List[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                out.append(line)
        return out


class CubeProgramRunner(BaseProgramRunner):
    """Runs ``WorkspaceRunProgramSpec`` jobs inside the Cube sandbox.

    Follows the workspace-relative-cwd semantic shared by Container/Local
    runners (`WorkspaceRunProgramSpec.cwd` is rooted at ``ws.path``) and
    aligns with `LocalProgramRunner` in auto-creating the resolved cwd.
    """

    def __init__(
        self,
        client: CubeSandboxClient,
        command_timeout: float,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ):
        super().__init__(provider=provider, enable_provider_env=enable_provider_env)
        self._client = client
        self._timeout = command_timeout

    @override
    async def run_program(self,
                          ws: WorkspaceInfo,
                          spec: WorkspaceRunProgramSpec,
                          ctx: Optional[InvocationContext] = None) -> WorkspaceRunResult:
        spec = self._apply_provider_env(spec, ctx)
        cwd = ws.path if not spec.cwd else join_remote(ws.path, normalize_remote_relative(spec.cwd, allow_current=True))

        run_dir = join_remote(ws.path, posixpath.join(DIR_RUNS, f"run_{time.strftime('%Y%m%dT%H%M%S')}"))
        out_dir = join_remote(ws.path, DIR_OUT)
        skills_dir = join_remote(ws.path, DIR_SKILLS)
        work_dir = join_remote(ws.path, DIR_WORK)

        env: dict[str, str] = {
            WORKSPACE_ENV_DIR_KEY: ws.path,
            ENV_SKILLS_DIR: skills_dir,
            ENV_WORK_DIR: work_dir,
            ENV_OUTPUT_DIR: out_dir,
            ENV_RUN_DIR: run_dir,
        }
        env.update(spec.env or {})

        # Single shell pipeline: ensure run_dir + cwd exist, cd, exec command.
        parts = [
            "set -e",
            f"mkdir -p {shell_quote(run_dir)} {shell_quote(cwd)}",
            f"cd {shell_quote(cwd)}",
        ]
        argv = [shell_quote(spec.cmd)] + [shell_quote(arg) for arg in (spec.args or [])]
        parts.append(" ".join(argv))
        shell_cmd = "; ".join(parts)

        timeout = float(spec.timeout) if spec.timeout and spec.timeout > 0 else float(DEFAULT_TIMEOUT_SEC)
        stdin_bytes = spec.stdin.encode("utf-8") if spec.stdin else None

        start = time.time()
        result = await self._client.commands_run(
            shell_cmd,
            env=env,
            stdin=stdin_bytes,
            timeout=timeout,
        )
        return WorkspaceRunResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration=time.time() - start,
            timed_out=result.timed_out,
        )


class CubeWorkspaceRuntime(BaseWorkspaceRuntime):
    """Cube/E2B-backed workspace runtime.

    Depends only on the public :class:`CubeSandboxClient` primitive, not on
    :class:`CubeCodeExecutor`. Use :func:`create_cube_workspace_runtime`
    when you have an executor and want to share its sandbox; pass a client
    directly when integrating with a non-executor caller.
    """

    def __init__(
        self,
        client: CubeSandboxClient,
        *,
        remote_workspace: str,
        execute_timeout: float,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ):
        self._client = client
        self._fs = CubeWorkspaceFS(self._client, execute_timeout)
        self._manager = CubeWorkspaceManager(self._client, remote_workspace, execute_timeout)
        self._runner = CubeProgramRunner(
            self._client,
            execute_timeout,
            provider=provider,
            enable_provider_env=enable_provider_env,
        )

    @property
    def sandbox_id(self) -> str | None:
        """Current Cube sandbox id."""
        return self._client.sandbox_id

    async def recreate(self) -> None:
        """Force sandbox recreation when the client supports it."""
        await self._client.recreate()

    async def destroy(self) -> None:
        """Destroy the current Cube sandbox/client."""
        await self._client.destroy()

    @override
    def manager(self, ctx: Optional[InvocationContext] = None) -> CubeWorkspaceManager:
        return self._manager

    @override
    def fs(self, ctx: Optional[InvocationContext] = None) -> CubeWorkspaceFS:
        return self._fs

    @override
    def runner(self, ctx: Optional[InvocationContext] = None) -> CubeProgramRunner:
        return self._runner

    @override
    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        return WorkspaceCapabilities(
            isolation="cube",
            network_allowed=True,
            read_only_mount=False,
            streaming=False,
        )


def create_cube_workspace_runtime(
    executor: CubeCodeExecutor | None = None,
    sandbox_client: CubeSandboxClient | None = None,
    execute_timeout: float = DEFAULT_EXECUTE_TIMEOUT,
    workspace_cfg: Optional[CubeWorkspaceRuntimeConfig] = None,
    provider: Optional[RunEnvProvider] = None,
    enable_provider_env: bool = False,
) -> CubeWorkspaceRuntime:
    """Construct a :class:`CubeWorkspaceRuntime` sharing ``executor``'s sandbox.

    Convenience wrapper that:

    - reuses the live :class:`CubeSandboxClient` already opened by
      ``executor`` (no second remote handshake),
    - takes ``execute_timeout`` from ``executor.config`` (sandbox-wide
      command timeout — naturally shared with the runtime), and
    - takes workspace-only settings from ``workspace_cfg`` (defaulting
      to :class:`CubeWorkspaceRuntimeConfig` defaults when omitted).

    For lower-level integrations, construct :class:`CubeWorkspaceRuntime`
    directly with an explicit client + ``remote_workspace`` +
    ``execute_timeout``.
    Args:
        executor: CubeCodeExecutor instance, will deprecated, will be removed in the future
        sandbox_client: CubeSandboxClient instance, required
        execute_timeout: execute timeout, default to DEFAULT_EXECUTE_TIMEOUT
        workspace_cfg: workspace configuration, default to CubeWorkspaceRuntimeConfig()
        provider: provider, default to None
        enable_provider_env: enable provider environment, default to False
    Returns:
        CubeWorkspaceRuntime instance
    """
    if executor:
        sandbox_client = executor.sandbox_client
        execute_timeout = executor.config.execute_timeout
    if not sandbox_client:
        raise ValueError("sandbox_client is required")
    ws_cfg = workspace_cfg or CubeWorkspaceRuntimeConfig()
    return CubeWorkspaceRuntime(
        sandbox_client,
        remote_workspace=ws_cfg.remote_workspace,
        execute_timeout=execute_timeout,
        provider=provider,
        enable_provider_env=enable_provider_env,
    )
