# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Tencent is pleased to support the open source community by making
trpc-agent-go available.

Copyright (C) 2025 Tencent.  All rights reserved.

trpc-agent-go is licensed under the Apache License Version 2.0.

Container workspace runtime implementation for Docker-based code execution.
"""

import io
import json
import os
import tarfile
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from .._artifacts import load_artifact_helper
from .._artifacts import parse_artifact_ref
from .._artifacts import save_artifact_helper
from .._base_workspace_runtime import BaseProgramRunner
from .._base_workspace_runtime import BaseWorkspaceFS
from .._base_workspace_runtime import BaseWorkspaceManager
from .._base_workspace_runtime import BaseWorkspaceRuntime
from .._base_workspace_runtime import RunEnvProvider
from .._constants import DEFAULT_INPUTS_CONTAINER
from .._constants import DEFAULT_MAX_FILES
from .._constants import DEFAULT_MAX_TOTAL_BYTES
from .._constants import DEFAULT_RUN_CONTAINER_BASE
from .._constants import DEFAULT_SKILLS_CONTAINER
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
from .._constants import META_FILE_NAME
from .._constants import WORKSPACE_ENV_DIR_KEY
from .._types import CodeFile
from .._types import ManifestFileRef
from .._types import ManifestOutput
from .._types import WorkspaceCapabilities
from .._types import WorkspaceInfo
from .._types import WorkspaceInputSpec
from .._types import WorkspaceOutputSpec
from .._types import WorkspacePutFileInfo
from .._types import WorkspaceRunProgramSpec
from .._types import WorkspaceRunResult
from .._types import WorkspaceStageOptions
from ..utils import InputRecordMeta
from ..utils import WorkspaceMetadata
from ..utils import get_rel_path
from ..utils import normalize_globs
from ._container_cli import CommandArgs
from ._container_cli import ContainerClient
from ._container_cli import ContainerConfig


@dataclass
class RuntimeConfig:
    """
    Configuration for container runtime.
    """
    skills_host_base: str = ""
    skills_container_base: str = DEFAULT_SKILLS_CONTAINER
    run_container_base: str = DEFAULT_RUN_CONTAINER_BASE
    inputs_host_base: str = ""
    inputs_container_base: str = DEFAULT_INPUTS_CONTAINER
    auto_map_inputs: bool = True
    command_args: CommandArgs = field(default_factory=CommandArgs)


def _shell_quote(s: str) -> str:
    if not s:
        return "''"
    return "'" + s.replace("'", "'\\''") + "'"


class ContainerWorkspaceManager(BaseWorkspaceManager):
    """
    Docker container-based workspace manager implementation.
    """

    def __init__(self, container: ContainerClient, config: RuntimeConfig, fs: BaseWorkspaceFS):
        """
        Initialize container workspace manager.

        Args:
            client: Docker client instance
            container: Docker container to use
            config: Runtime configuration
        """
        self.container = container
        self.config = config
        self.fs = fs
        self.ws_paths: dict[str, WorkspaceInfo] = {}

    @override
    async def create_workspace(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> WorkspaceInfo:
        """
        Create a new workspace inside the container.

        Args:
            exec_id: Unique execution identifier
            ctx: Optional[InvocationContext]

        Returns:
            Created workspace instance

        Raises:
            RuntimeError: If container is not ready or creation fails
        """
        if exec_id in self.ws_paths:
            return self.ws_paths[exec_id]
        safe_id = self._sanitize(exec_id)
        suffix = time.time_ns()

        ws_path = str(Path(self.config.run_container_base) / f"ws_{safe_id}_{suffix}")

        # Create standard directory layout
        cmd_str = ("set -e; "
                   f"mkdir -p {_shell_quote(ws_path)} "
                   f"{_shell_quote(str(Path(ws_path) / DIR_SKILLS))} "
                   f"{_shell_quote(str(Path(ws_path) / DIR_WORK))} "
                   f"{_shell_quote(str(Path(ws_path) / DIR_RUNS))} "
                   f"{_shell_quote(str(Path(ws_path) / DIR_OUT))}; "
                   f"[ -f {_shell_quote(str(Path(ws_path) / META_FILE_NAME))} ] || "
                   f"echo '{{}}' > {_shell_quote(str(Path(ws_path) / META_FILE_NAME))}")
        cmd = ["/bin/bash", "-lc", cmd_str]

        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to create workspace: {result.stderr}")

        ws = WorkspaceInfo(id=exec_id, path=ws_path)

        # Auto-map inputs if configured
        if self.config.auto_map_inputs and self.config.inputs_host_base:
            logger.info("Auto-mapping inputs for workspace %s", exec_id)
            specs = [
                WorkspaceInputSpec(src=f"host://{self.config.inputs_host_base}",
                                   dst=str(Path("work") / "inputs"),
                                   mode="link")
            ]
            await self.fs.stage_inputs(ws, specs, ctx)

        self.ws_paths[exec_id] = ws
        return ws

    @override
    async def cleanup(self, exec_id: str, ctx: Optional[InvocationContext] = None) -> None:
        """
        Remove workspace directory from container.

        Args:
            exec_id: Execution ID
            ctx: Optional[InvocationContext]
        """
        ws = self.ws_paths.get(exec_id)
        if not ws or not ws.path:
            return

        cmd = ["/bin/bash", "-lc", f"rm -rf '{ws.path}'"]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to clean up workspace: {result.stderr}")
        logger.info("Cleaned up workspace: %s", ws.path)
        self.ws_paths.pop(exec_id, None)

    @staticmethod
    def _sanitize(s: str) -> str:
        """
        Sanitize string for use in file paths.

        Args:
            s: String to sanitize

        Returns:
            Sanitized string
        """
        result = []
        for c in s:
            if c.isalnum() or c in ['-', '_']:
                result.append(c)
            else:
                result.append('_')
        return ''.join(result)


class ContainerWorkspaceFS(BaseWorkspaceFS):
    """
    Docker container-based workspace filesystem implementation.
    """

    def __init__(self, container: ContainerClient, config: RuntimeConfig):
        """
        Initialize container workspace filesystem.

        Args:
            client: Docker client instance
            container: Docker container to use
            config: Runtime configuration
        """
        self.container = container
        self.config = config

    @override
    async def put_files(self,
                        ws: WorkspaceInfo,
                        files: List[WorkspacePutFileInfo],
                        ctx: Optional[InvocationContext] = None) -> None:
        """
        Write files into workspace via tar archive.

        Args:
            ws: Target workspace
            files: List of files to write
            ctx: Optional[InvocationContext]

        Raises:
            RuntimeError: If file writing fails
        """
        if not files:
            return

        tar_stream = self._create_tar_from_files(files)
        success = self.container.client.api.put_archive(self.container.container.id, ws.path, tar_stream)

        if not success:
            raise RuntimeError("Failed to put files into container")

        logger.info("Put %s files into workspace %s", len(files), ws.path)

    @override
    async def stage_directory(self,
                              ws: WorkspaceInfo,
                              src: str,
                              dst: str,
                              opt: WorkspaceStageOptions,
                              ctx: Optional[InvocationContext] = None) -> None:
        """
        Stage a directory into workspace.

        Args:
            ws: Target workspace
            src: Source directory path
            dst: Destination path in workspace
            opt: Staging options
            ctx: Optional[InvocationContext]

        Raises:
            RuntimeError: If staging fails
        """
        src_abs_path = os.path.abspath(src)
        container_dst = str(Path(ws.path) / dst) if dst else ws.path
        # Fast path: within skills mount
        if opt.allow_mount and self.config.skills_host_base:
            rel_path = get_rel_path(self.config.skills_host_base, src_abs_path)
            if rel_path:
                container_src = str(Path(self.config.skills_container_base) / rel_path)
                cmd_str = f"mkdir -p '{container_dst}' && cp -a '{container_src}/.' '{container_dst}'"
                if opt.read_only:
                    cmd_str += f" && chmod -R a-w '{container_dst}'"

                cmd = ["/bin/bash", "-lc", cmd_str]
                result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
                if result.exit_code != 0:
                    raise RuntimeError(f"Failed to stage directory: {result.stderr}")
                return

        # Fallback: tar copy
        await self._put_directory(ws, src_abs_path, dst)

        if opt.read_only:
            cmd = ["/bin/bash", "-lc", f"chmod -R a-w '{container_dst}'"]
            result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
            if result.exit_code != 0:
                raise RuntimeError(f"Failed to chmod directory: {result.stderr}")

    @override
    async def collect(self,
                      ws: WorkspaceInfo,
                      patterns: List[str],
                      ctx: Optional[InvocationContext] = None) -> List[CodeFile]:
        """
        Collect files matching patterns from workspace.

        Args:
            ws: Source workspace
            patterns: Glob patterns to match
            ctx: Optional[InvocationContext]

        Returns:
            List of collected files

        Raises:
            RuntimeError: If collection fails
        """
        patterns = self._normalize_globs(patterns)

        # Build bash command to find files
        pattern_str = " ".join([f"'{p}'" for p in patterns])
        cmd_str = (f"cd '{ws.path}' && shopt -s globstar nullglob dotglob; "
                   f"for p in {pattern_str}; do for f in $p; do "
                   f"if [ -f \"$f\" ]; then "
                   f"(readlink -f \"$f\" 2>/dev/null || realpath \"$f\" 2>/dev/null || echo \"$(pwd)/$f\"); "
                   f"fi; done; done")

        cmd = ["/bin/bash", "-lc", cmd_str]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to collect files: {result.stderr}")
        stdout = result.stdout
        files = []
        seen = set()

        for line in stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue

            rel_path = line.removeprefix(f"{ws.path}/")
            if rel_path in seen:
                continue
            seen.add(rel_path)

            data, size_bytes, mime = self._copy_file_out(line)
            files.append(
                CodeFile(
                    name=rel_path,
                    content=data.decode('utf-8', errors='replace'),
                    mime_type=mime,
                    size_bytes=size_bytes,
                    truncated=size_bytes > len(data),
                ))

        logger.info("Collected %s files from workspace", len(files))
        return files

    @override
    async def stage_inputs(self,
                           ws: WorkspaceInfo,
                           specs: List[WorkspaceInputSpec],
                           ctx: Optional[InvocationContext] = None) -> None:
        """
        Stage inputs into workspace according to specifications.

        Args:
            ws: Target workspace
            specs: Input staging specifications
            ctx: Optional[InvocationContext]

        Raises:
            RuntimeError: If staging fails
        """
        md = await self._load_workspace_metadata(ws)
        for spec in specs:
            mode = (spec.mode or "").lower().strip() or "copy"
            dst_rel = (spec.dst or "").strip() or str(Path(DIR_WORK) / "inputs" / self._input_base(spec.src))
            dst_abs = str(Path(ws.path) / dst_rel)

            resolved = ""
            version: Optional[int] = None

            if spec.src.startswith("artifact://"):
                if not ctx:
                    raise ValueError("Context is required to load artifacts")
                name = spec.src.removeprefix("artifact://")
                artifact_name, requested_ver = parse_artifact_ref(name)
                use_ver = requested_ver
                if use_ver is None and spec.pin:
                    use_ver = self._pinned_artifact_version(md, artifact_name, dst_rel)
                content, actual_ver = await load_artifact_helper(ctx, artifact_name, use_ver)
                await self._put_bytes_tar(content, dst_abs)
                resolved = artifact_name
                version = use_ver if use_ver is not None else actual_ver
            elif spec.src.startswith("host://"):
                host_path = spec.src.removeprefix("host://")
                await self._stage_host_input(ws, host_path, dst_abs, mode, dst_rel)
                resolved = host_path
            elif spec.src.startswith("workspace://"):
                rel = spec.src.removeprefix("workspace://")
                src = str(Path(ws.path) / rel)
                await self._stage_workspace_input(src, dst_abs, mode)
                resolved = rel
            elif spec.src.startswith("skill://"):
                rest = spec.src.removeprefix("skill://")
                src = str(Path(ws.path) / DIR_SKILLS / rest)
                await self._stage_workspace_input(src, dst_abs, mode)
                resolved = src
            else:
                raise RuntimeError(f"Unsupported input: {spec.src}")

            md.inputs.append(
                InputRecordMeta(
                    src=spec.src,
                    dst=dst_rel,
                    resolved=resolved,
                    version=version,
                    mode=mode,
                    timestamp=datetime.now(),
                ))

        await self._save_workspace_metadata(ws, md)

        logger.info("Staged %s inputs into workspace", len(specs))

    @override
    async def collect_outputs(self,
                              ws: WorkspaceInfo,
                              spec: WorkspaceOutputSpec,
                              ctx: Optional[InvocationContext] = None) -> ManifestOutput:
        """
        Collect outputs from workspace according to specification.

        Args:
            ws: Source workspace
            spec: Output collection specification
            ctx: Optional[InvocationContext]

        Returns:
            Output manifest with collected files

        Raises:
            RuntimeError: If collection fails
        """
        globs = self._normalize_globs(spec.globs)
        pattern_str = " ".join([f"'{g}'" for g in globs])

        cmd_str = (f"cd '{ws.path}' && shopt -s globstar nullglob dotglob; "
                   f"for p in {pattern_str}; do for f in $p; do "
                   f"if [ -f \"$f\" ]; then echo \"$(pwd)/$f\"; fi; done; done")

        cmd = ["/bin/bash", "-lc", cmd_str]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code:
            raise RuntimeError(f"Failed to collect outputs: {result.stderr}")
        stdout = result.stdout

        max_files = spec.max_files or DEFAULT_MAX_FILES
        max_file_bytes = spec.max_file_bytes or MAX_READ_SIZE_BYTES
        max_total = spec.max_total_bytes or DEFAULT_MAX_TOTAL_BYTES

        manifest = ManifestOutput()
        total_bytes = 0
        count = 0
        saved_names: list[str] = []
        saved_versions: list[int] = []
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue

            if count >= max_files or total_bytes >= max_total:
                manifest.limits_hit = True
                break

            data, raw_size, mime = self._copy_file_out(line)

            if len(data) > max_file_bytes:
                data = data[:max_file_bytes]
                manifest.limits_hit = True

            if total_bytes + len(data) > max_total:
                remain = max_total - total_bytes
                if remain <= 0:
                    manifest.limits_hit = True
                    break
                data = data[:remain]
                manifest.limits_hit = True

            total_bytes += len(data)
            rel_path = line.removeprefix(f"{ws.path}/")
            truncated = raw_size > len(data)
            if truncated and spec.save:
                raise RuntimeError(f"cannot save truncated output file: {rel_path}")

            file_ref = ManifestFileRef(name=rel_path, mime_type=mime)
            if spec.inline:
                file_ref.content = data.decode('utf-8', errors='replace')
            if spec.save:
                save_name = rel_path
                if spec.name_template:
                    save_name = spec.name_template + rel_path
                if not ctx:
                    raise ValueError("Context is required to save artifacts")
                version = await save_artifact_helper(ctx, save_name, data, mime)
                file_ref.saved_as = save_name
                file_ref.version = version
                saved_names.append(save_name)
                saved_versions.append(version)

            manifest.files.append(file_ref)
            count += 1

        logger.info("Collected %s output files", len(manifest.files))
        return manifest

    async def _put_directory(self, ws: WorkspaceInfo, src: str, dst: str) -> None:
        """Copy directory to container using tar."""
        if not src or not str(src).strip():
            raise ValueError("source path is empty")
        abs_src = os.path.abspath(src)
        container_dst = str(Path(ws.path) / dst) if dst else ws.path
        if self.config.skills_host_base:
            rel_path = get_rel_path(self.config.skills_host_base, abs_src)
            if rel_path:
                container_src = str(Path(self.config.skills_container_base) / rel_path)
                # Create destination directory
                cmd = ["/bin/bash", "-lc", f"mkdir -p '{container_dst}' && cp -a '{container_src}/.' '{container_dst}'"]
                result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
                if result.exit_code == 0:
                    return None
                logger.debug("Failed to stage directory via mount copy, fallback to tar: %s", result.stderr)

        cmd = ["/bin/bash", "-lc", f"[ -e '{container_dst}' ] || mkdir -p '{container_dst}'"]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code:
            raise RuntimeError(f"Failed to stage directory: {result.stderr}")
        # Create tar archive
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tar.add(abs_src, arcname='.')

        tar_stream.seek(0)
        success = self.container.client.api.put_archive(self.container.container.id, container_dst, tar_stream)

        if not success:
            raise RuntimeError(f"Failed to copy directory {src} to {container_dst}")

    async def _put_bytes_tar(self, data: bytes, dest: str, mode: int = 0o644) -> None:
        """Copy bytes to container using tar."""
        # Create a tar with single file named as dest's base
        base = Path(dest).name
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            tarinfo = tarfile.TarInfo(name=base)
            tarinfo.size = len(data)
            tarinfo.mode = mode
            tarinfo.mtime = int(time.time())
            tar.addfile(tarinfo, io.BytesIO(data))
        # Reset buffer position to beginning
        tar_buffer.seek(0)
        # Ensure parent directory exists. Parent can be a symlink (for example
        # work/inputs in container mode when auto_inputs is enabled), so avoid
        # running plain `mkdir -p <symlink>` which may return "File exists".
        parent = Path(dest).parent
        cmd = ["/bin/bash", "-lc", f"[ -e '{parent.as_posix()}' ] || mkdir -p '{parent.as_posix()}'"]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code:
            raise RuntimeError(f"Failed to stage directory: {result.stderr}")
        success = self.container.client.api.put_archive(self.container.container.id, parent.as_posix(), tar_buffer)
        if not success:
            raise RuntimeError(f"Failed to copy bytes to {dest}")

    async def _stage_host_input(self, ws: WorkspaceInfo, host: str, dst: str, mode: str, dst_rel: str) -> None:
        """Stage input from host path."""
        if self.config.inputs_host_base:
            rel_path = get_rel_path(self.config.inputs_host_base, host)
            if rel_path:
                container_src = str(Path(self.config.inputs_container_base) / rel_path)

                if mode == "link":
                    cmd_str = (f"parent='{Path(dst).parent}'; "
                               f"[ -e \"$parent\" ] || mkdir -p \"$parent\"; "
                               f"ln -sfn '{container_src}' '{dst}'")
                else:
                    cmd_str = (f"parent='{Path(dst).parent}'; "
                               f"[ -e \"$parent\" ] || mkdir -p \"$parent\"; "
                               f"cp -a '{container_src}' '{dst}'")

                cmd = ["/bin/bash", "-lc", cmd_str]
                result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
                if result.exit_code != 0:
                    raise RuntimeError(f"Failed to stage host input: {result.stderr}")
                return
        # Fallback to tar copy
        await self._put_directory(ws, host, str(Path(dst_rel).parent))

    async def _stage_workspace_input(self, src: str, dst: str, mode: str) -> None:
        """Stage input from workspace path."""
        parent = Path(dst).parent
        if mode == "link":
            cmd_str = (f"[ -e '{parent}' ] || mkdir -p '{parent}'; "
                       f"ln -sfn '{src}' '{dst}'")
        else:
            cmd_str = (f"[ -e '{parent}' ] || mkdir -p '{parent}'; "
                       f"cp -a '{src}' '{dst}'")

        cmd = ["/bin/bash", "-lc", cmd_str]
        # await _exec_cmd(self.container, cmd, self.config.command_args)
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code:
            raise RuntimeError(f"Failed to stage input: {result.stderr}")

    def _copy_file_out(self, full_path: str) -> Tuple[bytes, int, str]:
        """
        Copy file out of container.

        Args:
            full_path: Full path to file in container

        Returns:
            Tuple of (file_data, size_bytes, mime_type)

        Raises:
            RuntimeError: If copy fails
        """
        try:
            stream, _ = self.container.client.api.get_archive(self.container.container.id, full_path)
            tar_stream = io.BytesIO(b''.join(stream))

            with tarfile.open(fileobj=tar_stream, mode='r') as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        f = tar.extractfile(member)
                        data = f.read(MAX_READ_SIZE_BYTES)
                        mime = self._detect_mime_type(data)
                        return data, member.size, mime

            raise RuntimeError(f"No file found in archive: {full_path}")

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to copy file out: %s", ex)
            raise RuntimeError(f"Failed to copy file {full_path}: {ex}")

    @staticmethod
    def _create_tar_from_files(files: List[WorkspacePutFileInfo]) -> io.BytesIO:
        """Create tar archive from file list."""
        tar_stream = io.BytesIO()

        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            for f in files:
                tarinfo = tarfile.TarInfo(name=f.path)
                tarinfo.size = len(f.content)
                tarinfo.mode = f.mode
                tarinfo.mtime = time.time()
                tar.addfile(tarinfo, io.BytesIO(f.content))

        tar_stream.seek(0)
        return tar_stream

    @staticmethod
    def _normalize_globs(patterns: List[str]) -> List[str]:
        """Normalize glob patterns."""
        return normalize_globs(patterns)

    @staticmethod
    def _input_base(src: str) -> str:
        """Extract base name from input path."""
        s = (src or "").strip()
        if s.startswith("artifact://"):
            ref = s.removeprefix("artifact://")
            try:
                name, _ = parse_artifact_ref(ref)
                base = Path(name.strip()).name
                if base and base not in (".", "..", "/"):
                    return base
            except Exception:  # pylint: disable=broad-except
                pass
        return Path(s).name

    @staticmethod
    def _pinned_artifact_version(md: Any, artifact_name: str, dst: str) -> Optional[int]:
        for record in reversed(md.inputs or []):
            if (record.dst or "") != dst:
                continue
            if record.version is None:
                continue
            if (record.resolved or "") == artifact_name:
                return record.version
            src = record.src or ""
            if not src.startswith("artifact://"):
                continue
            try:
                name, _ = parse_artifact_ref(src.removeprefix("artifact://"))
            except Exception:  # pylint: disable=broad-except
                continue
            if name == artifact_name:
                return record.version
        return None

    async def _load_workspace_metadata(self, ws: WorkspaceInfo):
        now = datetime.now()
        cmd = ["/bin/bash", "-lc", f"cat {_shell_quote(str(Path(ws.path) / META_FILE_NAME))}"]
        result = await self.container.exec_run(cmd=cmd, command_args=self.config.command_args)
        if result.exit_code != 0 or not result.stdout.strip():
            return WorkspaceMetadata(version=1, created_at=now, updated_at=now, last_access=now, skills={})
        try:
            data = json.loads(result.stdout)
            md = WorkspaceMetadata(**data)
        except Exception as ex:  # pylint: disable=broad-except
            raise RuntimeError(f"Failed to parse workspace metadata: {ex}") from ex
        if not md.version:
            md.version = 1
        if md.created_at is None:
            md.created_at = now
        md.last_access = now
        if md.skills is None:
            md.skills = {}
        return md

    async def _save_workspace_metadata(self, ws: WorkspaceInfo, md: Any) -> None:
        now = datetime.now()
        if not md.version:
            md.version = 1
        if md.created_at is None:
            md.created_at = now
        md.updated_at = now
        md.last_access = now
        if md.skills is None:
            md.skills = {}
        payload = json.dumps(md.model_dump(exclude_none=True, by_alias=True, mode="json"), ensure_ascii=False, indent=2)
        await self._put_bytes_tar(payload.encode("utf-8"), str(Path(ws.path) / META_FILE_NAME), mode=0o600)

    @staticmethod
    def _detect_mime_type(data: bytes) -> str:
        """Detect MIME type from file data."""
        # Simple detection based on content
        if data.startswith(b'\x89PNG'):
            return 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        elif data.startswith(b'%PDF'):
            return 'application/pdf'
        elif data.startswith(b'{') or data.startswith(b'['):
            return 'application/json'
        else:
            return 'text/plain'


class ContainerProgramRunner(BaseProgramRunner):
    """
    Docker container-based program runner implementation.
    """

    def __init__(
        self,
        container: ContainerClient,
        config: RuntimeConfig,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ):
        """
        Initialize container program runner.

        Args:
            client: Docker client instance
            container: Docker container to use
            config: Runtime configuration
        """
        super().__init__(provider=provider, enable_provider_env=enable_provider_env)
        self.container = container
        self.config = config

    @override
    async def run_program(self,
                          ws: WorkspaceInfo,
                          spec: WorkspaceRunProgramSpec,
                          ctx: Optional[InvocationContext] = None) -> WorkspaceRunResult:
        """
        Execute a program in the workspace.

        Args:
            ws: WorkspaceInfo to run in
            spec: Program execution specification
            ctx: Optional[InvocationContext]
        Returns:
            Execution result

        Raises:
            RuntimeError: If execution fails
        """
        spec = self._apply_provider_env(spec, ctx)
        cwd = f"{ws.path}/{spec.cwd}" if spec.cwd else ws.path

        # Prepare directories
        skills_dir = f"{ws.path}/{DIR_SKILLS}"
        work_dir = f"{ws.path}/{DIR_WORK}"
        out_dir = f"{ws.path}/{DIR_OUT}"
        run_dir = f"{ws.path}/{DIR_RUNS}/run_{time.strftime('%Y%m%dT%H%M%S')}"

        # Build environment
        base_env = {
            WORKSPACE_ENV_DIR_KEY: ws.path,
            ENV_SKILLS_DIR: skills_dir,
            ENV_WORK_DIR: work_dir,
            ENV_OUTPUT_DIR: out_dir,
            ENV_RUN_DIR: run_dir,
        }

        env_parts = []
        user_env = dict(spec.env or {})
        for k, v in base_env.items():
            if k not in user_env:
                env_parts.append(f"{k}={_shell_quote(v)}")

        for k, v in user_env.items():
            env_parts.append(f"{k}={_shell_quote(v)}")

        env_str = " ".join(env_parts)

        # Build command line
        cmd_parts = [
            f"mkdir -p {_shell_quote(run_dir)} {_shell_quote(out_dir)}", f"&& cd {_shell_quote(cwd)}",
            "&& env" if env_str else "", env_str,
            _shell_quote(spec.cmd)
        ]

        for arg in spec.args:
            cmd_parts.append(_shell_quote(arg))

        cmd_str = " ".join(filter(None, cmd_parts))
        cmd = ["/bin/bash", "-lc", cmd_str]

        start_time = time.time()
        if spec.timeout and spec.timeout > 0:
            timeout = spec.timeout
        elif self.config.command_args.timeout and self.config.command_args.timeout > 0:
            timeout = self.config.command_args.timeout
        else:
            timeout = float(DEFAULT_TIMEOUT_SEC)
        command_args = CommandArgs(
            environment=None,
            timeout=timeout,
            stdin=spec.stdin or None,
        )
        result = await self.container.exec_run(cmd=cmd, command_args=command_args)
        return WorkspaceRunResult(stdout=result.stdout,
                                  stderr=result.stderr,
                                  exit_code=result.exit_code,
                                  duration=time.time() - start_time,
                                  timed_out=result.is_timeout)


class ContainerWorkspaceRuntime(BaseWorkspaceRuntime):
    """
    Docker container-based execution engine.
    """

    def __init__(
        self,
        container: ContainerClient,
        host_config: Optional[Dict] = None,
        auto_inputs: bool = True,
        provider: Optional[RunEnvProvider] = None,
        enable_provider_env: bool = False,
    ):
        """
        Initialize container engine.

        Args:
            client: Docker client instance
            container: Docker container to use
            host_config: Host configuration with binds
            auto_inputs: Whether to auto-map inputs
        """
        self.container = container

        # Build runtime configuration
        config = RuntimeConfig(auto_map_inputs=auto_inputs)

        if host_config and 'Binds' in host_config:
            config.skills_host_base = self._find_bind_source(host_config['Binds'], DEFAULT_SKILLS_CONTAINER)
            config.inputs_host_base = self._find_bind_source(host_config['Binds'], DEFAULT_INPUTS_CONTAINER)

        self._fs = ContainerWorkspaceFS(self.container, config)
        self._manager = ContainerWorkspaceManager(self.container, config, self._fs)
        self._runner = ContainerProgramRunner(
            self.container,
            config,
            provider=provider,
            enable_provider_env=enable_provider_env,
        )

    @override
    def manager(self, ctx: Optional[InvocationContext] = None) -> ContainerWorkspaceManager:
        """Get workspace manager instance."""
        return self._manager

    @override
    def fs(self, ctx: Optional[InvocationContext] = None) -> ContainerWorkspaceFS:
        """Get workspace filesystem instance."""
        return self._fs

    @override
    def runner(self, ctx: Optional[InvocationContext] = None) -> ContainerProgramRunner:
        """Get program runner instance."""
        return self._runner

    @staticmethod
    def _find_bind_source(binds: List[str], dest: str) -> str:
        """
        Find host path for bind mount destination.

        Args:
            binds: List of bind mount specifications
            dest: Destination path in container

        Returns:
            Host source path or empty string
        """
        for bind in binds:
            parts = bind.split(':')
            if len(parts) < 2:
                continue

            # Handle format: source:dest[:mode], parse from right.
            bind_dest = parts[-2]
            if bind_dest == dest:
                source = ':'.join(parts[:-2]) if len(parts) > 2 else parts[0]
                if Path(source).is_dir():
                    return source

        return ""

    @override
    def describe(self, ctx: Optional[InvocationContext] = None) -> WorkspaceCapabilities:
        """Get the workspace capabilities."""
        return WorkspaceCapabilities(
            isolation="container",
            network_allowed=True,
            read_only_mount=True,
            streaming=True,
        )


def create_container_workspace_runtime(
    container_config: Optional[ContainerConfig] = None,
    host_config: Optional[Dict] = None,
    auto_inputs: bool = True,
    provider: Optional[RunEnvProvider] = None,
    enable_provider_env: bool = False,
) -> ContainerWorkspaceRuntime:
    """Create a new container workspace runtime.
    Args:
        container_config: Container configuration
        host_config: Host configuration
        auto_inputs: Whether to auto-map inputs
    Returns:
        ContainerWorkspaceRuntime instance
    """
    if container_config:
        cfg = ContainerConfig(base_url=container_config.base_url,
                              image=container_config.image,
                              docker_path=container_config.docker_path,
                              host_config=host_config)
        container = ContainerClient(config=cfg)
    else:
        container = ContainerClient(config=ContainerConfig(host_config=host_config))
    return ContainerWorkspaceRuntime(
        container=container,
        host_config=host_config,
        auto_inputs=auto_inputs,
        provider=provider,
        enable_provider_env=enable_provider_env,
    )
