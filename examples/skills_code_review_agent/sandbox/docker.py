"""Docker-backed review sandbox."""

import base64
import hashlib
import io
import json
import os
import shlex
import socket as pysocket
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import BaseWorkspaceFS
from trpc_agent_sdk.code_executors import BaseWorkspaceManager
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import ContainerConfig
from trpc_agent_sdk.code_executors import ContainerClient
from trpc_agent_sdk.code_executors import ContainerWorkspaceRuntime
from trpc_agent_sdk.code_executors import ContainerWorkspaceFS
from trpc_agent_sdk.code_executors import ContainerWorkspaceManager
from trpc_agent_sdk.code_executors import DEFAULT_INPUTS_CONTAINER
from trpc_agent_sdk.code_executors import DEFAULT_SKILLS_CONTAINER
from trpc_agent_sdk.code_executors import WorkspaceCapabilities
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceStageOptions
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.code_executors.container import CommandArgs
from trpc_agent_sdk.context import InvocationContext

from docker.errors import ImageNotFound
from docker.utils.socket import consume_socket_output
from docker.utils.socket import demux_adaptor
from docker.utils.socket import frames_iter
from trpc_agent_sdk.utils import CommandExecResult

from security import redact_text

DEFAULT_DOCKER_IMAGE = "skills-code-review-agent:latest"
IMAGE_POLICY_LABEL = "skills-code-review-agent.security-profile"
DEFAULT_OUTPUT_LIMIT_BYTES = 1024 * 1024
SDK_INLINE_OUTPUT_BYTES = 15 * 1024

_BOUNDED_RUN_SCRIPT = r"""
limit=$1
duration=$2
shift 2
capture='
import pathlib
import sys
n = int(sys.argv[3])
marker = b"\n[output truncated by sandbox policy]"
with pathlib.Path(sys.argv[1]).open("rb", buffering=0) as source:
    data = source.read(n + 1)
truncated = len(data) > n
data = data[:n]
if truncated:
    data = data[:max(0, n - len(marker))] + marker
pathlib.Path(sys.argv[2]).write_bytes(data)
'
output_dir=$(mktemp -d)
trap 'rm -rf "$output_dir"' EXIT
mkfifo "$output_dir/stdout.pipe" "$output_dir/stderr.pipe"
python3 -c "$capture" \
  "$output_dir/stdout.pipe" "$output_dir/stdout" "$limit" &
stdout_reader=$!
python3 -c "$capture" \
  "$output_dir/stderr.pipe" "$output_dir/stderr" "$limit" &
stderr_reader=$!
timeout --signal=TERM --kill-after=1s "$duration" "$@" \
  >"$output_dir/stdout.pipe" 2>"$output_dir/stderr.pipe"
status=$?
wait "$stdout_reader" "$stderr_reader"
python3 -c 'import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())' \
  "$output_dir/stdout"
python3 -c 'import pathlib,sys;sys.stderr.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())' \
  "$output_dir/stderr"
exit "$status"
""".strip()


class _HardenedContainerClient(ContainerClient):
    """Create the SDK container with review-specific resource restrictions."""

    def _expected_image_policy(self) -> str:
        dockerfile = Path(self.docker_path or "") / "Dockerfile"
        return hashlib.sha256(dockerfile.read_bytes()).hexdigest()

    def _build_docker_image(self) -> None:
        """Build the trusted context and bind its exact hash into image metadata."""
        if not self.docker_path:
            raise ValueError("Docker path is not set")
        self._client.images.build(
            path=self.docker_path,
            tag=self.image,
            rm=True,
            buildargs={"REVIEW_IMAGE_POLICY_HASH": self._expected_image_policy()},
        )

    def _ensure_review_image(self) -> None:
        try:
            image = self._client.images.get(self.image)
        except ImageNotFound:
            self._build_docker_image()
            return
        labels = image.attrs.get("Config", {}).get("Labels") or {}
        if labels.get(IMAGE_POLICY_LABEL) != self._expected_image_policy():
            self._build_docker_image()

    def _init_container(self) -> None:
        if not self._client:
            raise RuntimeError("Docker client is not initialized")
        if self.docker_path:
            self._ensure_review_image()

        binds = self.host_config.get("Binds", [])
        current_uid = getattr(os, "getuid", lambda: 65532)()
        current_gid = getattr(os, "getgid", lambda: 65532)()
        if current_uid == 0:
            current_uid, current_gid = 65532, 65532
        self._container = self._client.containers.run(
            image=self.image,
            command=["tail", "-f", "/dev/null"],
            detach=True,
            tty=True,
            stdin_open=False,
            working_dir="/tmp",
            network_mode="none",
            auto_remove=True,
            volumes=binds,
            user=f"{current_uid}:{current_gid}",
            environment={
                "HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "core.hooksPath",
                "GIT_CONFIG_VALUE_1": "/dev/null",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
            },
            read_only=True,
            tmpfs={
                "/tmp": (
                    "rw,noexec,nosuid,nodev,mode=1777,"
                    f"size={self.host_config['tmpfs_size_bytes']}"
                )
            },
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            mem_limit=self.host_config["memory_limit_bytes"],
            nano_cpus=self.host_config["nano_cpus"],
            pids_limit=self.host_config["pids_limit"],
            init=True,
        )
        self._verify_python_installation()

    def _exec_run_with_stdin(
        self,
        cmd: list[str],
        environment: dict[str, str],
        stdin: str,
    ) -> CommandExecResult:
        """Use the SDK stdin protocol with the current Docker container id."""
        response = self.container.client.api.exec_create(
            self.container.id,
            cmd=cmd,
            stdout=True,
            stderr=True,
            stdin=True,
            tty=False,
            environment=environment,
        )
        exec_id = response["Id"]
        socket = self.container.client.api.exec_start(
            exec_id,
            detach=False,
            tty=False,
            stream=False,
            socket=True,
            demux=False,
        )
        try:
            data = stdin.encode("utf-8")
            if data:
                try:
                    socket.sendall(data)
                except Exception:
                    socket._sock.sendall(data)
            try:
                socket.shutdown(pysocket.SHUT_WR)
            except Exception:
                raw_socket = getattr(socket, "_sock", None)
                if raw_socket is not None:
                    raw_socket.shutdown(pysocket.SHUT_WR)
                else:
                    close_write = getattr(socket, "close_write", None)
                    if callable(close_write):
                        close_write()
            frames = frames_iter(socket, tty=False)
            output = consume_socket_output(
                (demux_adaptor(*frame) for frame in frames),
                demux=True,
            )
            stdout = output[0].decode("utf-8") if output and output[0] else ""
            stderr = output[1].decode("utf-8") if output and output[1] else ""
        finally:
            socket.close()
        inspected = self.container.client.api.exec_inspect(exec_id)
        return CommandExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=int(inspected.get("ExitCode", -1)),
            is_timeout=False,
        )

    def close(self) -> None:
        """Stop the per-review container now instead of waiting for process exit."""
        if self._container is None:
            return
        self._cleanup_container()
        self._container = None


class _BoundedProgramRunner(BaseProgramRunner):
    """Kill timed-out programs, cap output, and redact it before model access."""

    def __init__(self, delegate: BaseProgramRunner, max_output_bytes: int) -> None:
        super().__init__()
        self.delegate = delegate
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def _bounded_text(value: str, limit: int, truncated: bool) -> str:
        marker = "\n[output truncated by sandbox policy]" if truncated else ""
        marker_bytes = marker.encode("utf-8")
        available = max(0, limit - len(marker_bytes))
        bounded = redact_text(value).encode("utf-8")[:available]
        # A byte slice may end inside a multibyte character; dropping that partial
        # code point keeps the returned payload valid UTF-8 and within the byte cap.
        text = bounded.decode("utf-8", errors="ignore")
        return f"{text}{marker}"

    async def run_program(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceRunResult:
        timeout_seconds = float(spec.timeout) if spec.timeout > 0 else 30.0
        # Skill tool results have a 16 KiB inline ceiling. Returning more through
        # Docker exec is wasted and can stall Docker Desktop at its 64 KiB socket
        # boundary, so reserve a small envelope and split the useful budget evenly.
        stream_limit = max(
            1,
            min(self.max_output_bytes, SDK_INLINE_OUTPUT_BYTES) // 2,
        )
        wrapped = WorkspaceRunProgramSpec(
            cmd="bash",
            args=[
                "-c",
                _BOUNDED_RUN_SCRIPT,
                "code-review-sandbox",
                str(stream_limit),
                f"{timeout_seconds}s",
                spec.cmd,
                *spec.args,
            ],
            env=spec.env,
            cwd=spec.cwd,
            stdin=spec.stdin,
            timeout=timeout_seconds + 2.0,
            limits=spec.limits,
        )
        result = await self.delegate.run_program(ws, wrapped, ctx)
        marker = "output truncated by sandbox policy"
        stdout_truncated = (
            marker in result.stdout
            or len(result.stdout.encode("utf-8")) > stream_limit
        )
        stderr_truncated = (
            marker in result.stderr
            or len(result.stderr.encode("utf-8")) > stream_limit
        )
        return result.model_copy(
            update={
                "stdout": self._bounded_text(
                    result.stdout,
                    stream_limit,
                    stdout_truncated,
                ),
                "stderr": self._bounded_text(
                    result.stderr,
                    stream_limit,
                    stderr_truncated,
                ),
                "timed_out": result.timed_out or result.exit_code in {124, 137},
            }
        )


class _TmpfsWorkspaceFS(ContainerWorkspaceFS):
    """Stage SDK-owned files into tmpfs without Docker's archive endpoint."""

    async def _extract_tar(self, archive: io.BytesIO, destination: str) -> None:
        encoded = base64.b64encode(archive.getvalue()).decode("ascii")
        if len(encoded) > 1024 * 1024:
            raise RuntimeError("tmpfs staging archive exceeds 1 MiB")
        script = (
            "import base64,io,sys,tarfile;"
            "data=base64.b64decode(sys.stdin.buffer.read());"
            "archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:');"
            "archive.extractall(sys.argv[1],filter='data')"
        )
        result = await self.container.exec_run(
            cmd=["python3", "-c", script, destination],
            command_args=CommandArgs(stdin=encoded, timeout=15),
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to stage tmpfs archive: {result.stderr}")

    async def put_files(
        self,
        ws: WorkspaceInfo,
        files: list[WorkspacePutFileInfo],
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        del ctx
        if files:
            await self._extract_tar(self._create_tar_from_files(files), ws.path)

    async def stage_directory(
        self,
        ws: WorkspaceInfo,
        src: str,
        dst: str,
        opt: WorkspaceStageOptions,
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        del ctx
        source = Path(src).resolve()
        skills_root = Path(self.config.skills_host_base).resolve()
        try:
            relative = source.relative_to(skills_root)
        except ValueError:
            await self._put_directory(ws, str(source), dst)
            return
        container_source = Path(self.config.skills_container_base) / relative
        destination = Path(ws.path) / dst if dst else Path(ws.path)
        command = (
            f"mkdir -p {shlex.quote(str(destination))} && "
            f"cp -R {shlex.quote(str(container_source) + '/.')} "
            f"{shlex.quote(str(destination))}"
        )
        if opt.read_only:
            command += f" && chmod -R a-w {shlex.quote(str(destination))}"
        result = await self.container.exec_run(
            cmd=["bash", "-lc", command],
            command_args=self.config.command_args,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to stage Skill directory: {result.stderr}")

    async def _put_bytes_tar(
        self,
        data: bytes,
        dest: str,
        mode: int = 0o644,
    ) -> None:
        base = Path(dest).name
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo(name=base)
            info.size = len(data)
            info.mode = mode
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
        parent = Path(dest).parent.as_posix()
        result = await self.container.exec_run(
            cmd=["mkdir", "-p", parent],
            command_args=self.config.command_args,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to stage tmpfs directory: {result.stderr}")
        await self._extract_tar(archive, parent)

    async def _put_directory(
        self,
        ws: WorkspaceInfo,
        src: str,
        dst: str,
    ) -> None:
        source = Path(src).resolve()
        destination = str(Path(ws.path) / dst) if dst else ws.path
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.add(source, arcname=".")
        result = await self.container.exec_run(
            cmd=["mkdir", "-p", destination],
            command_args=self.config.command_args,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to stage tmpfs directory: {result.stderr}")
        await self._extract_tar(archive, destination)

    def _copy_file_out(
        self,
        full_path: str,
        *,
        max_bytes: int = 1024 * 1024,
    ) -> tuple[bytes, int, str]:
        script = (
            "import base64,json,pathlib,sys;"
            "path=pathlib.Path(sys.argv[1]);limit=int(sys.argv[2]);"
            "size=path.stat().st_size;"
            "data=path.open('rb').read(limit);"
            "print(json.dumps({'size':size,'data':base64.b64encode(data).decode()}))"
        )
        exit_code, output = self.container.container.exec_run(
            ["python3", "-c", script, full_path, str(max_bytes)],
            demux=True,
        )
        stdout, stderr = output
        if exit_code != 0:
            message = stderr.decode("utf-8", errors="replace") if stderr else ""
            raise RuntimeError(f"Failed to copy tmpfs file: {message}")
        payload = json.loads((stdout or b"{}").decode("utf-8"))
        data = base64.b64decode(payload["data"])
        return data, int(payload["size"]), self._detect_mime_type(data)


class _HardenedContainerWorkspaceRuntime(ContainerWorkspaceRuntime):
    """Use the SDK runtime with a tmpfs-compatible file staging adapter."""

    def __init__(self, client: ContainerClient, host_config: dict[str, object]) -> None:
        super().__init__(client, host_config=host_config, auto_inputs=True)
        config = self._manager.config
        self._fs = _TmpfsWorkspaceFS(client, config)
        self._manager = ContainerWorkspaceManager(client, config, self._fs)

    async def close(self) -> None:
        self.container.close()


class _InputRemappingManager(BaseWorkspaceManager):
    """Restore the standard input link after Skill staging replaces it."""

    def __init__(self, runtime: BaseWorkspaceRuntime) -> None:
        self.runtime = runtime

    async def create_workspace(
        self,
        exec_id: str,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceInfo:
        workspace = await self.runtime.manager(ctx).create_workspace(exec_id, ctx)
        input_path = str(Path(workspace.path) / "work" / "inputs")
        # Skill staging replaces this link, so restore the read-only mounted input.
        command = (
            f"rm -rf {shlex.quote(input_path)} && "
            f"ln -s {shlex.quote(DEFAULT_INPUTS_CONTAINER)} "
            f"{shlex.quote(input_path)}"
        )
        result = await self.runtime.runner(ctx).run_program(
            workspace,
            WorkspaceRunProgramSpec(
                cmd="bash",
                args=["-lc", command],
                cwd=".",
                timeout=5,
            ),
            ctx,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to restore sandbox inputs: {result.stderr}")
        return workspace

    async def cleanup(
        self,
        exec_id: str,
        ctx: Optional[InvocationContext] = None,
    ) -> None:
        await self.runtime.manager(ctx).cleanup(exec_id, ctx)


class _InputRemappingRuntime(BaseWorkspaceRuntime):
    """Delegate a runtime while keeping ``work/inputs`` mapped read-only."""

    def __init__(
        self,
        runtime: BaseWorkspaceRuntime,
        max_output_bytes: int,
    ) -> None:
        self.runtime = runtime
        self._manager = _InputRemappingManager(runtime)
        self._max_output_bytes = max_output_bytes

    def manager(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseWorkspaceManager:
        del ctx
        return self._manager

    def fs(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseWorkspaceFS:
        return self.runtime.fs(ctx)

    def runner(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseProgramRunner:
        return _BoundedProgramRunner(
            self.runtime.runner(ctx),
            self._max_output_bytes,
        )

    def describe(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceCapabilities:
        return self.runtime.describe(ctx)

    async def close(self) -> None:
        close = getattr(self.runtime, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


@dataclass(frozen=True)
class DockerSandbox:
    """Build an isolated, network-disabled Docker workspace runtime."""

    image: str = DEFAULT_DOCKER_IMAGE
    docker_context: Path = Path(__file__).resolve().parent
    memory_limit_bytes: int = 512 * 1024 * 1024
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 256
    tmpfs_size_bytes: int = 256 * 1024 * 1024
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES

    def create_runtime(
        self,
        repository_path: Path,
        skills_path: Path,
    ) -> BaseWorkspaceRuntime:
        """Mount the target and Skills read-only and create the runtime."""
        repository_path = repository_path.resolve()
        skills_path = skills_path.resolve()
        if not repository_path.is_dir():
            raise ValueError(f"Repository path is not a directory: {repository_path}")
        if not skills_path.is_dir():
            raise ValueError(f"Skills path is not a directory: {skills_path}")
        for name, value in (
            ("memory limit", self.memory_limit_bytes),
            ("CPU limit", self.nano_cpus),
            ("PID limit", self.pids_limit),
            ("tmpfs limit", self.tmpfs_size_bytes),
            ("output limit", self.output_limit_bytes),
        ):
            if value <= 0:
                raise ValueError(f"Docker {name} must be positive")

        # Both reviewed code and Skill definitions are immutable inside the container.
        binds = [
            f"{repository_path}:{DEFAULT_INPUTS_CONTAINER}:ro",
            f"{skills_path}:{DEFAULT_SKILLS_CONTAINER}:ro",
        ]
        container_config = ContainerConfig(
            image=self.image,
            docker_path=str(self.docker_context),
        )
        host_config = {
            "Binds": binds,
            "memory_limit_bytes": self.memory_limit_bytes,
            "nano_cpus": self.nano_cpus,
            "pids_limit": self.pids_limit,
            "tmpfs_size_bytes": self.tmpfs_size_bytes,
        }
        client = _HardenedContainerClient(
            ContainerConfig(
                image=container_config.image,
                docker_path=container_config.docker_path,
                host_config=host_config,
            )
        )
        return _InputRemappingRuntime(
            _HardenedContainerWorkspaceRuntime(client, host_config),
            self.output_limit_bytes,
        )
