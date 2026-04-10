# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""WorkspaceInfo runtime for local code execution.

This module provides the WorkspaceRuntime class which allows local code execution.
It provides methods for staging directories and inputs into the workspace.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.utils import async_execute_command

from .._artifacts import load_artifact_helper
from .._artifacts import parse_artifact_ref
from .._artifacts import save_artifact_helper
from .._base_workspace_runtime import BaseWorkspaceManager
from .._base_workspace_runtime import BaseWorkspaceFS
from .._base_workspace_runtime import BaseProgramRunner
from .._base_workspace_runtime import BaseWorkspaceRuntime
from .._base_workspace_runtime import RunEnvProvider

from .._constants import DEFAULT_FILE_MODE
from .._constants import DEFAULT_MAX_FILES
from .._constants import DEFAULT_MAX_TOTAL_BYTES
from .._constants import DEFAULT_TIMEOUT_SEC
from .._constants import DIR_OUT
from .._constants import DIR_RUNS
from .._constants import DIR_SKILLS
from .._constants import DIR_WORK
from .._constants import ENV_OUTPUT_DIR
from .._constants import ENV_RUN_DIR
from .._constants import ENV_SKILLS_DIR
from .._constants import ENV_WORK_DIR
from .._constants import MAX_READ_SIZE_BYTES
from .._constants import WORKSPACE_ENV_DIR_KEY
from .._types import CodeFile
from .._types import WorkspaceInfo
from .._types import WorkspacePutFileInfo
from .._types import WorkspaceInputSpec
from .._types import WorkspaceRunProgramSpec
from .._types import WorkspaceRunResult
from .._types import WorkspaceCapabilities
from .._types import WorkspaceStageOptions
from .._types import ManifestFileRef
from .._types import ManifestOutput
from .._types import WorkspaceOutputSpec
from .._program_session import BaseProgramSession
from ..utils import ensure_layout
from ..utils import load_metadata
from ..utils import save_metadata
from ..utils import InputRecordMeta
from ..utils import OutputRecordMeta
from ..utils import normalize_globs
from ..utils import collect_files_with_glob
from ..utils import detect_content_type
from ..utils import make_symlink
from ..utils import copy_path
from ..utils import path_join

if sys.platform != "win32":
    import pty

from ._local_program_session import LocalProgramSession


class LocalWorkspaceManager(BaseWorkspaceManager):
    """Local workspace manager for executing commands in skill workspaces."""

    def __init__(self,
                 work_root: str,
                 auto_inputs: bool = True,
                 inputs_host_base: str = "",
                 fs: BaseWorkspaceFS = None):
        if not work_root:
            work_root = tempfile.gettempdir()
        self.work_root = work_root
        self.auto_inputs = auto_inputs
        self.inputs_host_base = inputs_host_base
        self.fs = fs
        self.ws_paths: dict[str, WorkspaceInfo] = {}

    @override
    async def create_workspace(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> WorkspaceInfo:
        """Create a new workspace.

        Args:
            ctx: Context for the operation
            exec_id: Execution ID

        Returns:
            The workspace information.
        """
        if exec_id in self.ws_paths:
            return self.ws_paths[exec_id]
        # Sanitize exec_id to be filesystem friendly
        safe = re.sub(r'[^a-zA-Z0-9_-]', '_', exec_id)

        # Make workspace path unique to avoid collisions
        suffix = time.time_ns()
        ws_path = Path(self.work_root) / f"ws_{safe}_{suffix}"
        ws_path.mkdir(parents=True, exist_ok=True)
        ws_path.chmod(0o777)

        # Ensure standard layout and metadata
        ensure_layout(ws_path)

        ws = WorkspaceInfo(id=exec_id, path=ws_path.as_posix())

        # Auto-map inputs if configured
        if self.auto_inputs and self.inputs_host_base:
            specs = [
                WorkspaceInputSpec(src=f"host://{self.inputs_host_base}", dst=str(Path("work") / "inputs"), mode="link")
            ]
            await self.fs.stage_inputs(ws, specs, ctx)

        self.ws_paths[exec_id] = ws
        return ws

    @override
    async def cleanup(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> None:
        """Clean up a workspace.

        Args:
            ctx: Context for the operation
            exec_id: Execution ID
        """
        ws = self.ws_paths.get(exec_id)
        if not ws or not ws.path:
            return

        path = Path(ws.path)
        if path.exists():
            shutil.rmtree(path)
        self.ws_paths.pop(exec_id, None)


class LocalWorkspaceFS(BaseWorkspaceFS):
    """Local workspace file system for executing commands in skill workspaces."""

    def __init__(self, read_only_staged_skill: bool = False):
        self.read_only_staged_skill = read_only_staged_skill

    @override
    async def put_files(
        self,
        ws: WorkspaceInfo,
        files: List[WorkspacePutFileInfo],
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Write file blobs under the workspace root.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            files: Files to write
        """
        for file in files:
            self._write_file_safe(ws.path, file)

    @override
    async def stage_directory(
        self,
        ws: WorkspaceInfo,
        src: str,
        dst: str,
        opt: WorkspaceStageOptions,
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Stage a host directory into the workspace.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            src: Source directory path
            to: Destination path relative to workspace
            opt: Staging options
        """
        self._put_directory(ws, src, dst)

        # Make tree read-only if requested
        ro = opt.read_only or self.read_only_staged_skill
        if ro:
            if dst:
                dst = Path(ws.path) / Path(dst)
            else:
                dst = Path(ws.path)
            self._make_tree_read_only(dst)

    def _put_directory(
        self,
        ws: WorkspaceInfo,
        src: str,
        dst: str,
    ) -> None:
        """
        Copy an entire directory from host into workspace.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            host_path: Source directory path on host
            to: Destination path relative to workspace
        """
        src = os.path.abspath(src)
        dst = path_join(ws.path, dst)
        self._copy_directory(src, dst)

    def _copy_directory(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Copy directory recursively.

        Args:
            src: Source directory path
            dst: Destination directory path
        """
        src_path = Path(src)
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)

        for item in src_path.rglob("*"):
            rel_path = item.relative_to(src)
            target = dst_path / rel_path
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)

    def _make_tree_read_only(
        self,
        dst: Path,
    ) -> None:
        """Remove write bits from entire tree.

        Args:
            dst: Destination directory path
        """
        for path in dst.rglob("*"):
            if path.is_file():
                current_mode = path.stat().st_mode
                new_mode = current_mode & ~0o222  # Clear write bits
                path.chmod(new_mode)

    @override
    async def collect(self,
                      ws: WorkspaceInfo,
                      patterns: List[str],
                      ctx: Optional[InvocationContext] = None) -> List[CodeFile]:
        """Collect files from the workspace.

        Find output files by glob patterns relative to workspace root.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            patterns: Glob patterns to match

        Returns:
            List of matching file references
        """
        out = []
        root = Path(ws.path)
        patterns = normalize_globs(patterns)

        # Canonicalize root
        try:
            real_root = root.resolve()
        except Exception:  # pylint: disable=broad-except
            real_root = root

        seen = set()

        for pattern in patterns:
            matches = collect_files_with_glob(ws.path, pattern)
            for match_path in matches:
                m_abs = Path("/" + match_path.lstrip("/"))
                # Ensure it is within root
                try:
                    m_abs.relative_to(root)
                except ValueError:
                    continue

                # Collapse symlinks to canonical path and deduplicate
                try:
                    real_path = m_abs.resolve()
                except Exception:  # pylint: disable=broad-except
                    real_path = m_abs

                # Re-check containment against canonical root
                try:
                    name = str(real_path.relative_to(real_root))
                except ValueError:
                    continue

                if name in seen:
                    continue

                seen.add(name)
                content, mime_type = self._read_limited(real_path)

                out.append(CodeFile(
                    name=name,
                    content=content,
                    mime_type=mime_type,
                ))

        return out

    def _read_limited(self, path: Path) -> tuple[str, str]:
        """Read file with size limit.

        Args:
            path: Path to the file

        Returns:
            The content and MIME type of the file.
        """
        try:
            content = path.read_bytes()[:MAX_READ_SIZE_BYTES]
            mime_type = detect_content_type(path, content)
            return content.decode('utf-8', errors='ignore'), mime_type
        except Exception:  # pylint: disable=broad-except
            return "", "application/octet-stream"

    @override
    async def stage_inputs(
        self,
        ws: WorkspaceInfo,
        specs: List[WorkspaceInputSpec],
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        """
        Map external inputs into the workspace.

        Args:
            ws: Target workspace
            specs: Input specifications
        """
        ensure_layout(ws.path)
        md = load_metadata(ws.path)
        for spec in specs:
            mode = (spec.mode or "copy").lower().strip()
            dst = spec.dst
            if not dst or not dst.strip():
                base = self._input_default_name(spec.src)
                dst = Path(DIR_WORK) / "inputs" / base
            else:
                dst = Path(dst)

            resolved = ""
            ver = None

            if spec.src.startswith("artifact://"):
                # Handle artifact inputs
                name = spec.src[len("artifact://"):]
                resolved, ver = parse_artifact_ref(name)
                content, ver = await load_artifact_helper(ctx, resolved, ver)
                self._write_file_safe(
                    ws.path, WorkspacePutFileInfo(
                        path=dst.as_posix(),
                        content=content,
                        mode=DEFAULT_FILE_MODE,
                    ))
            elif spec.src.startswith("host://"):
                # Handle host inputs
                host_path = spec.src[len("host://"):]
                resolved = host_path
                if mode == "link":
                    make_symlink(ws.path, dst.as_posix(), host_path)
                else:
                    self._put_directory(ws, host_path, dst.parent.as_posix())
            elif spec.src.startswith("workspace://"):
                # Handle workspace inputs
                rel = spec.src[len("workspace://"):]
                src = path_join(ws.path, rel)
                resolved = rel

                if mode == "link":
                    make_symlink(ws.path, dst.as_posix(), src)
                else:
                    copy_path(src, path_join(ws.path, dst.as_posix()))
            elif spec.src.startswith("skill://"):
                # Handle skill inputs
                rest = spec.src[len("skill://"):]
                src_base = Path(ws.path) / DIR_SKILLS
                src = path_join(src_base.as_posix(), rest)
                resolved = src

                if mode == "link":
                    make_symlink(ws.path, dst.as_posix(), src)
                else:
                    copy_path(src, path_join(ws.path, dst.as_posix()))
            else:
                raise ValueError(f"unsupported input: {spec.src}")

            # Record input
            md.inputs.append(
                InputRecordMeta(
                    src=spec.src,
                    dst=dst.as_posix(),
                    resolved=resolved,
                    version=ver,
                    mode=mode,
                    timestamp=datetime.now(),
                ))

        save_metadata(Path(ws.path), md)

    def _input_default_name(
        self,
        src: str,
    ) -> str:
        """Generate default input name from path."""
        # Strip scheme and keep tail element as default name
        i = src.rfind("/")
        if i >= 0 and i + 1 < len(src):
            return src[i + 1:]
        return src

    def _write_file_safe(
        self,
        root: str,
        file: WorkspacePutFileInfo,
    ) -> None:
        """Safely write a file within workspace boundaries."""
        if not file.path:
            raise ValueError("empty file path")

        dst = Path(path_join(root, file.path))

        # Ensure inside root
        try:
            dst.relative_to(Path(root))
        except ValueError:
            raise ValueError(f"path escapes workspace: {file.path}")

        dst.parent.mkdir(parents=True, exist_ok=True)

        mode = file.mode or DEFAULT_FILE_MODE
        dst.write_bytes(file.content or b"")
        dst.chmod(mode)

    @override
    async def collect_outputs(self,
                              ws: WorkspaceInfo,
                              spec: WorkspaceOutputSpec,
                              ctx: Optional[InvocationContext] = None) -> ManifestOutput:
        """Collect outputs from the workspace."""
        """
        Implement declarative collector with limits.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            spec: Output collection specification

        Returns:
            Output manifest with collected files
        """
        ensure_layout(ws.path)

        max_files = spec.max_files or DEFAULT_MAX_FILES
        max_file_bytes = spec.max_file_bytes or MAX_READ_SIZE_BYTES
        max_total_bytes = spec.max_total_bytes or DEFAULT_MAX_TOTAL_BYTES

        left_total = max_total_bytes
        globs = normalize_globs(spec.globs)
        out = ManifestOutput()

        saved_names = []
        saved_vers = []
        count = 0

        for pattern in globs:
            matches = collect_files_with_glob(ws.path, pattern)

            for match_path in matches:
                if count >= max_files:
                    out.limits_hit = True
                    break

                m_abs = Path("/" + match_path.lstrip("/"))
                # Ensure it is within workspace
                try:
                    name = str(m_abs.relative_to(ws.path))
                except ValueError:
                    continue

                # Respect both per-file and total byte limits
                limit = min(max_file_bytes, left_total)

                content, mime_type = self._read_limited_with_cap(m_abs, limit)

                # Mark limits hit when a file reached per-file cap
                if len(content) >= max_file_bytes:
                    out.limits_hit = True

                left_total -= len(content)
                count += 1

                file_ref = ManifestFileRef(
                    name=name,
                    mime_type=mime_type,
                )

                if spec.inline:
                    file_ref.content = content

                if spec.save:
                    save_name = name
                    if spec.name_template:
                        save_name = spec.name_template + name
                    if not ctx:
                        raise ValueError("Context is required to save artifacts")
                    ver = await save_artifact_helper(ctx, save_name, content, mime_type)
                    # Placeholder for artifact saving
                    file_ref.saved_as = save_name
                    file_ref.version = ver
                    saved_names.append(save_name)
                    saved_vers.append(ver)

                out.files.append(file_ref)

                if left_total <= 0:
                    out.limits_hit = True
                    break

        # Record output
        md = load_metadata(ws.path)
        md.outputs.append(
            OutputRecordMeta(
                globs=spec.globs,
                saved_as=saved_names,
                versions=saved_vers,
                limits_hit=out.limits_hit,
                timestamp=datetime.now(),
            ))
        save_metadata(ws.path, md)

        return out

    def _read_limited_with_cap(
        self,
        path: Path,
        cap_bytes: int,
    ) -> tuple[str, str]:
        """Read file with specific capacity limit."""
        if cap_bytes <= 0:
            return "", "application/octet-stream"

        if cap_bytes > MAX_READ_SIZE_BYTES:
            cap_bytes = MAX_READ_SIZE_BYTES

        try:
            content = path.read_bytes()[:cap_bytes]
            mime_type = detect_content_type(path, content)
            return content.decode('utf-8', errors='ignore'), mime_type
        except Exception:  # pylint: disable=broad-except
            return "", "application/octet-stream"


class LocalProgramRunner(BaseProgramRunner):
    """Local program runner for executing commands in skill workspaces."""

    def __init__(
        self,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ):
        super().__init__(provider=provider, enable_provider_env=enable_provider_env)

    def _build_program_env(self, ws: WorkspaceInfo, spec: WorkspaceRunProgramSpec) -> dict[str, str]:
        env = os.environ.copy()
        user_env = dict(spec.env or {})
        wr_path = Path(ws.path)
        ensure_layout(wr_path)
        run_dir = wr_path / DIR_RUNS / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S.%f')}"
        run_dir.mkdir(parents=True, exist_ok=True)

        base_env = {
            WORKSPACE_ENV_DIR_KEY: ws.path,
            ENV_SKILLS_DIR: str(Path(ws.path) / DIR_SKILLS),
            ENV_WORK_DIR: str(Path(ws.path) / DIR_WORK),
            ENV_OUTPUT_DIR: str(Path(ws.path) / DIR_OUT),
            ENV_RUN_DIR: str(run_dir),
        }
        for key, value in base_env.items():
            if key not in user_env:
                env[key] = value
        if user_env:
            env.update(user_env)
        return env

    @override
    async def run_program(self,
                          ws: WorkspaceInfo,
                          spec: WorkspaceRunProgramSpec,
                          ctx: Optional[InvocationContext] = None) -> WorkspaceRunResult:
        """Run a program in the workspace."""
        """
        Run a command inside the workspace.

        Args:
            ctx: Context for the operation
            ws: Target workspace
            spec: Program execution specification

        Returns:
            Execution result
        """
        spec = self._apply_provider_env(spec, ctx)
        # Resolve cwd under workspace
        cwd = Path(path_join(ws.path, spec.cwd))
        cwd.mkdir(parents=True, exist_ok=True)

        timeout = spec.timeout or float(DEFAULT_TIMEOUT_SEC)
        env = self._build_program_env(ws, spec)

        # Prepare command
        cmd_args = [spec.cmd] + (spec.args or [])

        stdin_data = spec.stdin.encode('utf-8') if spec.stdin else None
        start_time = time.time()
        result = await async_execute_command(work_dir=cwd,
                                             cmd_args=cmd_args,
                                             input=stdin_data,
                                             env=env,
                                             timeout=timeout)
        return WorkspaceRunResult(stdout=result.stdout,
                                  stderr=result.stderr,
                                  exit_code=result.exit_code,
                                  duration=time.time() - start_time,
                                  timed_out=result.is_timeout)

    async def start_program(
        self,
        ctx: Optional[InvocationContext],
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
    ) -> BaseProgramSession:
        """Start an interactive program session in workspace."""
        if spec.tty and sys.platform == "win32":
            raise ValueError("interactive tty is not supported on windows")

        spec = self._apply_provider_env(spec, ctx)
        cwd = Path(path_join(ws.path, spec.cwd))
        cwd.mkdir(parents=True, exist_ok=True)
        env = self._build_program_env(ws, spec)
        timeout = spec.timeout or float(DEFAULT_TIMEOUT_SEC)

        cmd_args = [spec.cmd] + (spec.args or [])
        if spec.tty:
            master_fd, slave_fd = pty.openpty()
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd_args,
                    cwd=str(cwd),
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    preexec_fn=os.setsid,
                )
            except Exception:
                os.close(master_fd)
                os.close(slave_fd)
                raise
            finally:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            session = LocalProgramSession(process, master_fd=master_fd)
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            session = LocalProgramSession(process)
        if timeout > 0:
            asyncio.create_task(session.enforce_timeout(float(timeout)))
        if spec.stdin:
            await session.write(spec.stdin, newline=False)
        return session


class LocalWorkspaceRuntime(BaseWorkspaceRuntime):
    """Local workspace for executing commands in skill workspaces."""

    def __init__(self,
                 work_root: str = '',
                 read_only_staged_skill: bool = False,
                 auto_inputs: bool = True,
                 inputs_host_base: str = "",
                 provider: Optional[RunEnvProvider] = None,
                 enable_provider_env: bool = False):
        self._fs = LocalWorkspaceFS(read_only_staged_skill)
        self._runner = LocalProgramRunner(provider=provider, enable_provider_env=enable_provider_env)
        self._manager = LocalWorkspaceManager(work_root, auto_inputs, inputs_host_base, self._fs)

    @override
    def manager(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceManager:
        """Get the workspace manager."""
        return self._manager

    @override
    def fs(self, ctx: Optional[InvocationContext] = None) -> BaseWorkspaceFS:
        """Get the workspace file system."""
        return self._fs

    @override
    def runner(self, ctx: Optional[InvocationContext] = None) -> BaseProgramRunner:
        """Get the program runner."""
        return self._runner

    @override
    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        """Get the workspace capabilities."""
        return WorkspaceCapabilities(
            isolation="local",
            network_allowed=True,
            read_only_mount=True,
            streaming=True,
        )


def create_local_workspace_runtime(work_root: str = '',
                                   read_only_staged_skill: bool = False,
                                   auto_inputs: bool = True,
                                   inputs_host_base: str = "",
                                   provider: Optional[RunEnvProvider] = None,
                                   enable_provider_env: bool = False) -> LocalWorkspaceRuntime:
    """Create a new local workspace runtime."""
    return LocalWorkspaceRuntime(work_root, read_only_staged_skill, auto_inputs, inputs_host_base, provider,
                                 enable_provider_env)
