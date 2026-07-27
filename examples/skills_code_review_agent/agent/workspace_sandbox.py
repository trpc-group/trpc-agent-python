"""Sandbox adapter backed by the SDK ``BaseWorkspaceRuntime`` contract.

The deterministic review pipeline is synchronous, while SDK workspace
runtimes expose async lifecycle, filesystem and program APIs.  This adapter
owns one event loop and one workspace for the lifetime of a review, stages the
bundled skill once, and presents the same ``run(SandboxRequest)`` surface as
the local development fallback in :mod:`.sandbox`.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from trpc_agent_sdk.code_executors import (
    BaseWorkspaceRuntime,
    WorkspaceCapabilities,
    WorkspaceOutputSpec,
    WorkspacePutFileInfo,
    WorkspaceRunProgramSpec,
    WorkspaceStageOptions,
)

from .filtering import ReviewExecutionFilter
from .models import FilterDecision, SandboxRequest, SandboxRun, utc_now_iso
from .redaction import redact_text
from .sandbox import SAFE_ENV_KEYS, SandboxRunner

DEFAULT_REVIEW_IMAGE = os.getenv("CODE_REVIEW_IMAGE", "python:3.12-slim")
REDACTION_LOOKAHEAD_BYTES = 8192
CAPTURE_SHUTDOWN_GRACE_SECONDS = 1.0
CAPTURE_PROTOCOL_PREFIX = "TRPC_REVIEW_CAPTURE_V1:"

# ``BaseProgramRunner.run_program`` returns fully materialized stdout/stderr;
# neither the common runtime contract nor Cube exposes an incremental session
# API.  Run the requested program behind this small, dependency-free wrapper
# so the SDK backend only ever receives a bounded protocol envelope.  The
# wrapper keeps a fixed-size buffer per stream and kills the process group as
# soon as the redaction-aware collection budget is exceeded.
CAPTURE_PROGRAM_SOURCE = r"""
import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time

capture_limit = max(int(sys.argv[1]), 0)
timeout_seconds = max(float(sys.argv[2]), 0.0)
protocol_prefix = sys.argv[3]
command = sys.argv[4:]
buffers = {"stdout": bytearray(), "stderr": bytearray()}
buffer_lock = threading.Lock()
limit_hit = threading.Event()
output_truncated = False


def emit(*, exit_code=None, timed_out=False, wrapper_error=""):
    payload = {
        "stdout": base64.b64encode(bytes(buffers["stdout"])).decode("ascii"),
        "stderr": base64.b64encode(bytes(buffers["stderr"])).decode("ascii"),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_truncated": output_truncated,
        "wrapper_error": wrapper_error,
    }
    sys.stdout.write(protocol_prefix + json.dumps(payload, separators=(",", ":")))
    sys.stdout.flush()


try:
    process = subprocess.Popen(
        command,
        # The wrapper itself never consumes stdin.  Forward the bounded
        # runtime request's one-shot input to the child unchanged so benign
        # commands such as JSON parsers keep their normal SDK semantics.
        stdin=sys.stdin.buffer,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
except BaseException as error:
    emit(wrapper_error=f"{type(error).__name__}: {error}")
    raise SystemExit(0)


def drain(stream, stream_name):
    global output_truncated
    read_chunk = getattr(stream, "read1", stream.read)
    while True:
        chunk = read_chunk(4096)
        if not chunk:
            return
        with buffer_lock:
            remaining = max(capture_limit - len(buffers[stream_name]), 0)
            buffers[stream_name].extend(chunk[:remaining])
            if len(buffers[stream_name]) >= capture_limit:
                output_truncated = True
                limit_hit.set()


threads = [
    threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
    threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
]
for thread in threads:
    thread.start()


def kill_process_group():
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
timed_out = False
while process.poll() is None:
    if limit_hit.wait(0.01):
        kill_process_group()
        break
    if deadline is not None and time.monotonic() >= deadline:
        timed_out = True
        kill_process_group()
        break

try:
    exit_code = process.wait(timeout=0.5)
except subprocess.TimeoutExpired:
    kill_process_group()
    try:
        exit_code = process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        emit(timed_out=timed_out, wrapper_error="process did not stop after SIGKILL")
        raise SystemExit(0)

for thread in threads:
    thread.join(timeout=0.5)
emit(exit_code=exit_code, timed_out=timed_out)
"""

RuntimeFactory = Callable[
    [str, float],
    BaseWorkspaceRuntime | Awaitable[BaseWorkspaceRuntime],
]


class NetworkIsolationError(RuntimeError):
    """Raised when a production runtime cannot prove network isolation."""


class NetworkIsolatedWorkspaceRuntime(BaseWorkspaceRuntime):
    """Delegate to a runtime created with backend-enforced egress denial.

    The SDK's current Container and Cube capability descriptions always say
    that networking is available, even when Docker ``network_mode=none`` or
    E2B ``allow_internet_access=False`` was used at sandbox creation.  This
    facade records the stronger construction invariant for downstream runtime
    validation without changing the SDK globally.
    """

    def __init__(self, delegate: BaseWorkspaceRuntime) -> None:
        self._delegate = delegate

    def manager(self, ctx=None):
        return self._delegate.manager(ctx)

    def fs(self, ctx=None):
        return self._delegate.fs(ctx)

    def runner(self, ctx=None):
        return self._delegate.runner(ctx)

    def describe(self, ctx=None) -> WorkspaceCapabilities:
        capabilities = self._delegate.describe(ctx)
        return capabilities.model_copy(update={"network_allowed": False}, deep=True)

    async def destroy(self) -> None:
        destroy = getattr(self._delegate, "destroy", None)
        if callable(destroy):
            destroyed = destroy()
            if inspect.isawaitable(destroyed):
                await destroyed
            return

        # ContainerWorkspaceRuntime has no public lifecycle method today. Its
        # ContainerClient owns the long-lived Docker container and otherwise
        # releases it only through an atexit hook. Expose that release through
        # this facade so model-driven agents can close their runtime per task.
        container_client = getattr(self._delegate, "container", None)
        cleanup = getattr(container_client, "_cleanup_container", None)
        if callable(cleanup):
            destroyed = cleanup()
            if inspect.isawaitable(destroyed):
                await destroyed

    async def recreate(self) -> None:
        # CubeSandboxClient.recreate() currently creates a sandbox without
        # carrying forward E2B's network policy. Refuse that lifecycle path
        # until the SDK can recreate with an explicit deny-egress policy.
        raise NetworkIsolationError(
            "network-isolated runtime recreation is disabled because the SDK "
            "cannot preserve its egress policy"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def assert_runtime_network_isolated(
    runtime: BaseWorkspaceRuntime,
    runtime_name: str,
) -> None:
    """Fail closed unless a production runtime declares networking disabled."""
    try:
        capabilities = runtime.describe()
    except Exception as ex:
        raise NetworkIsolationError(
            f"{runtime_name} runtime cannot prove that outbound networking is disabled"
        ) from ex
    if getattr(capabilities, "network_allowed", None) is not False:
        raise NetworkIsolationError(
            f"{runtime_name} runtime permits outbound networking; "
            "a backend-enforced deny-egress policy is required"
        )


async def create_network_isolated_cube_runtime(
    timeout_seconds: float,
) -> BaseWorkspaceRuntime:
    """Create Cube with E2B's sandbox-level internet access disabled.

    tRPC-Agent's current Cube client factory does not expose E2B's network
    creation options. Use the same public ``AsyncSandbox`` and
    ``CubeSandboxClient`` types directly so denial is applied atomically while
    the remote sandbox is created. Older E2B clients reject the keyword and
    therefore fail closed before a sandbox can be opened.
    """
    from e2b_code_interpreter import AsyncSandbox

    from trpc_agent_sdk.code_executors.cube import (
        CubeClientConfig,
        CubeSandboxClient,
        create_cube_workspace_runtime,
    )

    timeout_seconds = max(float(timeout_seconds), 1.0)
    config = CubeClientConfig(execute_timeout=timeout_seconds, auto_recover=False)
    try:
        sandbox = await AsyncSandbox.create(
            template=config.resolve_template(),
            api_url=config.resolve_api_url(),
            api_key=config.resolve_api_key(),
            timeout=config.idle_timeout,
            allow_internet_access=False,
        )
    except TypeError as ex:
        raise NetworkIsolationError(
            "the installed Cube/E2B client cannot enforce deny-egress at sandbox creation"
        ) from ex

    try:
        client = CubeSandboxClient(sandbox, config)
    except Exception:
        await sandbox.kill()
        raise
    try:
        runtime = create_cube_workspace_runtime(
            sandbox_client=client,
            execute_timeout=timeout_seconds,
        )
        return NetworkIsolatedWorkspaceRuntime(runtime)
    except Exception:
        await client.destroy()
        raise


class OutputCaptureError(RuntimeError):
    """The bounded child-process output protocol failed closed."""


@dataclass(frozen=True)
class CapturedProgramResult:
    """Decoded, size-checked result emitted by ``CAPTURE_PROGRAM_SOURCE``."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    output_truncated: bool
    wrapper_error: str


async def create_sdk_workspace_runtime(
    runtime: str, timeout_seconds: float
) -> BaseWorkspaceRuntime:
    """Create a production workspace runtime without importing optional Cube eagerly."""
    timeout_seconds = max(float(timeout_seconds), 1.0)
    if runtime == "container":
        from trpc_agent_sdk.code_executors import (
            ContainerConfig,
            create_container_workspace_runtime,
        )

        workspace_runtime = create_container_workspace_runtime(
            container_config=ContainerConfig(image=DEFAULT_REVIEW_IMAGE),
            host_config={"network_mode": "none"},
            auto_inputs=False,
        )
        return NetworkIsolatedWorkspaceRuntime(workspace_runtime)

    if runtime == "cube":
        return await create_network_isolated_cube_runtime(timeout_seconds)

    raise ValueError(f"unsupported SDK workspace runtime: {runtime!r}")


class WorkspaceSandboxRunner:
    """Execute all sandbox requests for one review in one SDK workspace.

    Use as a context manager.  Initialization errors are retained and turned
    into structured ``SandboxRun`` failures, or delegated to the existing
    local runner when ``allow_local_fallback`` is enabled.
    """

    def __init__(
        self,
        *,
        runtime: str,
        skill_dir: Path,
        execution_filter: ReviewExecutionFilter,
        exec_id: str,
        timeout_seconds: float,
        allow_local_fallback: bool = False,
        workspace_runtime: BaseWorkspaceRuntime | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        if runtime not in {"container", "cube"}:
            raise ValueError("WorkspaceSandboxRunner supports only container or cube")
        self.runtime = runtime
        self.skill_dir = skill_dir
        self.execution_filter = execution_filter
        self.exec_id = exec_id
        self.timeout_seconds = timeout_seconds
        self.allow_local_fallback = allow_local_fallback
        self._runtime = workspace_runtime
        self._owns_runtime = workspace_runtime is None
        self._runtime_factory = runtime_factory or create_sdk_workspace_runtime
        self._workspace: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._init_error: Exception | None = None
        self._cleanup_attempted = False
        self.cleanup_failure: SandboxRun | None = None
        self._fallback = (
            SandboxRunner(
                runtime="local",
                skill_dir=skill_dir,
                execution_filter=execution_filter,
            )
            if allow_local_fallback
            else None
        )

    def __enter__(self) -> WorkspaceSandboxRunner:  # noqa: PYI034 - Python 3.10 compatibility
        if self._loop is not None:
            raise RuntimeError("workspace sandbox is already open")
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._initialize())
        except Exception as ex:  # noqa: BLE001 - initialization is reported as SandboxRun
            self._init_error = ex
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False

    async def _initialize(self) -> None:
        if self._runtime is None:
            created = self._runtime_factory(self.runtime, self.timeout_seconds)
            self._runtime = await created if inspect.isawaitable(created) else created
        assert_runtime_network_isolated(self._runtime, self.runtime)
        manager = self._runtime.manager()
        self._workspace = await manager.create_workspace(self.exec_id)
        await self._runtime.fs().stage_directory(
            self._workspace,
            str(self.skill_dir),
            "skills/code-review",
            WorkspaceStageOptions(read_only=True, allow_mount=False, mode="copy"),
        )

    def run(self, request: SandboxRequest) -> SandboxRun:
        """Filter, stage, execute and collect one request in the review workspace."""
        decision = self.execution_filter.evaluate_request(request)
        if decision.allowed:
            paths = [(request.cwd or ".", True)]
            paths.extend((path, False) for path in request.input_files)
            paths.extend((path, False) for path in request.output_files)
            for path, allow_current in paths:
                path_decision = self._workspace_path_decision(
                    path, allow_current=allow_current
                )
                if not path_decision.allowed:
                    path_decision.command = request.display_command
                    decision = path_decision
                    break
        if not decision.allowed:
            return SandboxRun(
                name=request.name,
                runtime=self.runtime,
                command=request.display_command,
                status="filtered",
                filter_decision=decision,
                error_type="FilterIntercept",
            )

        if self._init_error is not None:
            if self._fallback is not None:
                fallback_run = self._fallback.run(request)
                fallback_run.runtime = "local-fallback"
                return fallback_run
            return self._failure_from_exception(request, decision, self._init_error, 0)
        if self._loop is None or self._workspace is None or self._runtime is None:
            return self._failure_from_exception(
                request,
                decision,
                RuntimeError("workspace sandbox must be opened with a context manager"),
                0,
            )

        started = time.monotonic()
        started_at = utc_now_iso()
        try:
            return self._loop.run_until_complete(
                self._run_async(request, decision, started, started_at)
            )
        except Exception as ex:  # noqa: BLE001 - backend errors are reported as SandboxRun
            return self._failure_from_exception(
                request,
                decision,
                ex,
                int((time.monotonic() - started) * 1000),
                started_at=started_at,
            )

    async def _run_async(
        self,
        request: SandboxRequest,
        decision: FilterDecision,
        started: float,
        started_at: str,
    ) -> SandboxRun:
        assert self._runtime is not None
        assert self._workspace is not None
        fs = self._runtime.fs()
        if request.input_files:
            await fs.put_files(
                self._workspace,
                [
                    WorkspacePutFileInfo(
                        path=path,
                        content=content.encode("utf-8"),
                        mode=0o600,
                    )
                    for path, content in request.input_files.items()
                ],
            )

        command = self._resolve_command(request.command)
        if not command:
            raise ValueError("sandbox command is empty")
        final_limit = max(int(request.max_output_bytes), 0)
        # Keep enough bounded lookahead to redact a credential that starts at
        # the visible boundary.  Infinite producers are still terminated at
        # this small hard cap rather than being accumulated by the SDK.
        collection_limit = final_limit + REDACTION_LOOKAHEAD_BYTES
        backend_result = await self._runtime.runner().run_program(
            self._workspace,
            WorkspaceRunProgramSpec(
                cmd="python",
                args=[
                    "-c",
                    CAPTURE_PROGRAM_SOURCE,
                    str(collection_limit),
                    str(request.timeout_seconds),
                    CAPTURE_PROTOCOL_PREFIX,
                    *command,
                ],
                env=self._safe_env(request.env),
                cwd=request.cwd,
                # The child enforces the requested deadline.  This small
                # backend grace window exists only to emit the bounded result
                # envelope after killing the child process group.
                timeout=request.timeout_seconds + CAPTURE_SHUTDOWN_GRACE_SECONDS,
            ),
        )
        result = self._decode_captured_result(backend_result, collection_limit)
        if result.wrapper_error:
            raise OutputCaptureError(result.wrapper_error)

        artifacts: dict[str, str] = {}
        artifact_truncated = False
        if request.output_files:
            # Read a small bounded suffix beyond the externally visible cap so
            # credentials crossing the truncation boundary can still be fully
            # recognized and redacted before the final output is sliced.
            artifact_collection_limit = max(final_limit, 1) + REDACTION_LOOKAHEAD_BYTES
            manifest = await fs.collect_outputs(
                self._workspace,
                WorkspaceOutputSpec(
                    globs=request.output_files,
                    max_files=max(len(request.output_files), 1),
                    max_file_bytes=artifact_collection_limit,
                    max_total_bytes=artifact_collection_limit
                    * max(len(request.output_files), 1),
                    inline=True,
                    save=False,
                ),
            )
            artifact_truncated = manifest.limits_hit
            for file_ref in manifest.files:
                content, truncated = self._truncate(
                    file_ref.content, request.max_output_bytes
                )
                artifacts[file_ref.name] = content
                artifact_truncated = artifact_truncated or truncated

        stdout, stdout_truncated = self._truncate(
            result.stdout, request.max_output_bytes
        )
        stderr, stderr_truncated = self._truncate(
            result.stderr, request.max_output_bytes
        )
        stream_limit_hit = (
            result.output_truncated or stdout_truncated or stderr_truncated
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if result.timed_out:
            status = "timed_out"
            error_type = "TimeoutExpired"
        elif stream_limit_hit:
            status = "failed"
            error_type = "OutputLimitExceeded"
        elif result.exit_code == 0:
            status = "succeeded"
            error_type = ""
        else:
            status = "failed"
            error_type = "SandboxProcessError"
        return SandboxRun(
            name=request.name,
            runtime=self.runtime,
            command=request.display_command,
            status=status,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            output_truncated=stream_limit_hit or artifact_truncated,
            artifacts=artifacts,
            error_type=error_type,
            filter_decision=decision,
            started_at=started_at,
            finished_at=utc_now_iso(),
        )

    @staticmethod
    def _decode_captured_result(
        backend_result: Any, collection_limit: int
    ) -> CapturedProgramResult:
        """Validate and decode the bounded wrapper's protocol envelope."""
        if backend_result.timed_out:
            raise OutputCaptureError(
                "output wrapper exceeded its backend shutdown deadline"
            )
        if backend_result.exit_code != 0:
            raise OutputCaptureError(
                "output wrapper exited before returning a complete result"
            )
        if backend_result.stderr:
            raise OutputCaptureError(
                "output wrapper wrote unexpected diagnostic output"
            )

        envelope = backend_result.stdout or ""
        if not envelope.startswith(CAPTURE_PROTOCOL_PREFIX):
            raise OutputCaptureError(
                "output wrapper returned an invalid protocol envelope"
            )
        try:
            payload = json.loads(envelope.removeprefix(CAPTURE_PROTOCOL_PREFIX))
            if not isinstance(payload, dict):
                raise TypeError("capture payload must be an object")

            def decode_stream(name: str) -> str:
                encoded = payload.get(name)
                if not isinstance(encoded, str):
                    raise TypeError(f"capture field {name!r} must be a string")
                raw = base64.b64decode(encoded.encode("ascii"), validate=True)
                if len(raw) > collection_limit:
                    raise ValueError(f"capture field {name!r} exceeded its byte limit")
                return raw.decode("utf-8", errors="replace")

            exit_code = payload.get("exit_code")
            if exit_code is not None and (
                isinstance(exit_code, bool) or not isinstance(exit_code, int)
            ):
                raise TypeError("capture exit_code must be an integer or null")
            timed_out = payload.get("timed_out")
            output_truncated = payload.get("output_truncated")
            wrapper_error = payload.get("wrapper_error")
            if not isinstance(timed_out, bool) or not isinstance(
                output_truncated, bool
            ):
                raise TypeError("capture status fields must be booleans")
            if not isinstance(wrapper_error, str):
                raise TypeError("capture wrapper_error must be a string")
            if len(wrapper_error.encode("utf-8", errors="replace")) > collection_limit:
                raise ValueError("capture wrapper_error exceeded its byte limit")
            return CapturedProgramResult(
                stdout=decode_stream("stdout"),
                stderr=decode_stream("stderr"),
                exit_code=exit_code,
                timed_out=timed_out,
                output_truncated=output_truncated,
                wrapper_error=wrapper_error,
            )
        except OutputCaptureError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as ex:
            raise OutputCaptureError(
                "output wrapper returned a malformed protocol envelope"
            ) from ex

    def close(self) -> None:
        """Clean the review workspace exactly once and close the owned event loop."""
        if self._cleanup_attempted:
            return
        self._cleanup_attempted = True
        if self._loop is None:
            return
        try:
            self._loop.run_until_complete(self._cleanup_async())
        except Exception as ex:  # noqa: BLE001 - cleanup failures are audit records
            self.cleanup_failure = self._failure_from_exception(
                SandboxRequest(
                    name="workspace-cleanup",
                    command=[],
                    display_command="cleanup review workspace",
                    cwd=".",
                ),
                FilterDecision(
                    action="allow",
                    rule_id="allow",
                    reason="workspace cleanup is an internal lifecycle operation",
                    command="cleanup review workspace",
                ),
                ex,
                0,
            )
        finally:
            self._loop.close()

    async def _cleanup_async(self) -> None:
        first_error: Exception | None = None
        if self._runtime is not None and self._workspace is not None:
            try:
                await self._runtime.manager().cleanup(self.exec_id)
            except Exception as ex:  # noqa: BLE001 - still destroy an owned Cube sandbox
                first_error = ex

        # A Cube runtime created here owns a remote sandbox in addition to its
        # per-review workspace.  Destroy it after workspace cleanup so the
        # deterministic CLI does not leak remote sandboxes.
        if self.runtime == "cube" and self._runtime is not None and self._owns_runtime:
            destroy = getattr(self._runtime, "destroy", None)
            if callable(destroy):
                try:
                    destroyed = destroy()
                    if inspect.isawaitable(destroyed):
                        await destroyed
                except Exception as ex:  # noqa: BLE001 - preserve the first lifecycle error
                    if first_error is None:
                        first_error = ex
        elif (
            self.runtime == "container"
            and self._runtime is not None
            and self._owns_runtime
        ):
            # ContainerWorkspaceRuntime currently exposes no public close
            # method; its owned ContainerClient otherwise stops only at
            # process exit. Release it here so repeated library calls cannot
            # accumulate live Docker containers.
            container_client = getattr(self._runtime, "container", None)
            cleanup = getattr(container_client, "_cleanup_container", None)
            if callable(cleanup):
                try:
                    cleaned = cleanup()
                    if inspect.isawaitable(cleaned):
                        await cleaned
                except Exception as ex:  # noqa: BLE001 - preserve lifecycle audit
                    if first_error is None:
                        first_error = ex
        if first_error is not None:
            raise first_error

    def _failure_from_exception(
        self,
        request: SandboxRequest,
        decision: FilterDecision,
        error: Exception,
        duration_ms: int,
        *,
        started_at: str | None = None,
    ) -> SandboxRun:
        stderr, truncated = self._truncate(str(error), request.max_output_bytes)
        return SandboxRun(
            name=request.name,
            runtime=self.runtime,
            command=request.display_command,
            status="failed",
            exit_code=None,
            duration_ms=duration_ms,
            stderr=stderr,
            output_truncated=truncated,
            error_type=type(error).__name__,
            filter_decision=decision,
            started_at=started_at or utc_now_iso(),
            finished_at=utc_now_iso(),
        )

    @staticmethod
    def _resolve_command(command: list[str]) -> list[str]:
        return ["python" if part == "$PYTHON" else part for part in command]

    def _workspace_path_decision(
        self, path: str, *, allow_current: bool
    ) -> FilterDecision:
        decision = self.execution_filter.evaluate_path(path)
        if not decision.allowed:
            return decision
        normalized = path.replace("\\", "/")
        candidate = PurePosixPath(normalized)
        has_drive = (
            len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
        )
        if candidate.is_absolute() or has_drive or ".." in candidate.parts:
            return FilterDecision(
                action="deny",
                rule_id="path.workspace_escape",
                reason="path must stay relative to the review workspace",
                path=str(candidate),
            )
        if not allow_current and str(candidate) in {"", "."}:
            return FilterDecision(
                action="deny",
                rule_id="path.invalid",
                reason="input and output paths must name a file inside the review workspace",
                path=str(candidate),
            )
        return decision

    @staticmethod
    def _safe_env(extra: dict[str, str]) -> dict[str, str]:
        # Do not copy host values into Linux/remote runtimes (in particular a
        # Windows PATH would make the container unable to locate Python).
        env = {
            key: str(value)
            for key, value in extra.items()
            if key in SAFE_ENV_KEYS or key.startswith("TRPC_REVIEW_")
        }
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    @staticmethod
    def _truncate(value: str, max_bytes: int) -> tuple[str, bool]:
        redacted, _ = redact_text(value or "")
        limit = max(int(max_bytes), 0)
        encoded = redacted.encode("utf-8", errors="replace")
        if len(encoded) <= limit:
            return redacted, False
        truncated = encoded[:limit].decode("utf-8", errors="replace")
        return truncated + "\n[output truncated]", True
