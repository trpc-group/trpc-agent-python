# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SDK workspace runtime adapters for the code-review sandbox."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol

from .models import RuntimeKind
from .models import SandboxRun
from .models import SandboxStatus
from .sanitizer import redact_mapping
from .sanitizer import redact_text


class _RuntimeAdapter(Protocol):
    runtime: RuntimeKind

    def run_rule_script(
        self,
        task_id: str,
        input_path: Path,
        manifest_path: Path,
        output_path: Path,
        timeout_sec: float,
        output_limit_bytes: int,
    ) -> tuple[SandboxRun, dict[str, Any]]:
        """Run the bundled rule script and return a sandbox record plus result."""


@dataclass(frozen=True)
class _RuntimeAdapterResult:
    runtime: RuntimeKind
    available: bool
    reason: str = ""
    adapter: _RuntimeAdapter | None = None


class _RuntimeUnavailableError(RuntimeError):
    """Raised when an optional workspace backend cannot be reached."""


@dataclass
class _WorkspaceRuntimeAdapter:
    """Run the rule script through an SDK workspace runtime."""

    runtime: RuntimeKind
    workspace_runtime: Any

    def run_rule_script(
        self,
        task_id: str,
        input_path: Path,
        manifest_path: Path,
        output_path: Path,
        timeout_sec: float,
        output_limit_bytes: int,
    ) -> tuple[SandboxRun, dict[str, Any]]:
        return _run_async(
            _run_workspace_rule_script_async(
                self.workspace_runtime,
                self.runtime,
                task_id,
                input_path,
                manifest_path,
                timeout_sec,
                output_limit_bytes,
            ))


@dataclass
class _CubeWorkspaceRuntimeAdapter:
    """Create and use the Cube client inside one async lifecycle."""

    runtime: RuntimeKind
    client_factory: Callable[[], Coroutine[Any, Any, Any]]
    runtime_factory: Callable[[Any], Any]

    def run_rule_script(
        self,
        task_id: str,
        input_path: Path,
        manifest_path: Path,
        output_path: Path,
        timeout_sec: float,
        output_limit_bytes: int,
    ) -> tuple[SandboxRun, dict[str, Any]]:
        return _run_async(
            self._run_rule_script_async(
                task_id,
                input_path,
                manifest_path,
                timeout_sec,
                output_limit_bytes,
            ))

    async def _run_rule_script_async(
        self,
        task_id: str,
        input_path: Path,
        manifest_path: Path,
        timeout_sec: float,
        output_limit_bytes: int,
    ) -> tuple[SandboxRun, dict[str, Any]]:
        client = None
        outcome: tuple[SandboxRun, dict[str, Any]] | None = None
        try:
            client = await self.client_factory()
            workspace_runtime = self.runtime_factory(client)
            outcome = await _run_workspace_rule_script_async(
                workspace_runtime,
                self.runtime,
                task_id,
                input_path,
                manifest_path,
                timeout_sec,
                output_limit_bytes,
            )
        except Exception as ex:
            if client is None:
                raise _RuntimeUnavailableError(f"cube runtime is unavailable: {type(ex).__name__}: {ex}") from ex
            sandbox_run = SandboxRun(
                task_id=task_id,
                runtime=self.runtime,
                command="python3 skills/code-review/scripts/rule_runner.py",
                timeout_sec=timeout_sec,
                output_limit_bytes=output_limit_bytes,
                status=SandboxStatus.FAILED,
                stderr=redact_text(str(ex)),
                error_type=type(ex).__name__,
            )
            outcome = sandbox_run, _failure_result(str(ex))
        finally:
            if client is not None:
                try:
                    await client.destroy()
                except Exception as ex:
                    if outcome is None:
                        raise _RuntimeUnavailableError(
                            f"cube runtime cleanup failed: {type(ex).__name__}: {ex}") from ex
                    sandbox_run, result = outcome
                    if sandbox_run.status is SandboxStatus.SUCCESS:
                        sandbox_run.status = SandboxStatus.FAILED
                        sandbox_run.error_type = "ClientDestroyFailed"
                    if sandbox_run.stderr:
                        sandbox_run.stderr = f"{sandbox_run.stderr}; {redact_text(str(ex))}"
                    else:
                        sandbox_run.stderr = redact_text(str(ex))
                    outcome = sandbox_run, result
        if outcome is None:  # pragma: no cover - defensive lifecycle guard
            raise _RuntimeUnavailableError("cube runtime did not return a result")
        return outcome


async def _run_workspace_rule_script_async(
    workspace_runtime: Any,
    runtime: RuntimeKind,
    task_id: str,
    input_path: Path,
    manifest_path: Path,
    timeout_sec: float,
    output_limit_bytes: int,
) -> tuple[SandboxRun, dict[str, Any]]:
    """Execute a rule script and clean up its workspace in the same loop."""
    from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
    from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec

    exec_id = f"{task_id}-{int(time.time() * 1000)}"
    command = ("python3 skills/code-review/scripts/rule_runner.py --input inputs/parsed_input.json "
               "--manifest inputs/skill_manifest.json --output outputs/rule_result.json")
    sandbox_run = SandboxRun(
        task_id=task_id,
        runtime=runtime,
        command=command,
        timeout_sec=timeout_sec,
        output_limit_bytes=output_limit_bytes,
    )
    started = time.monotonic()
    workspace = None
    workspace_creation_started = False
    result = _failure_result("")
    try:
        manager = workspace_runtime.manager()
        fs = workspace_runtime.fs()
        runner = workspace_runtime.runner()
        workspace_creation_started = True
        workspace = await manager.create_workspace(exec_id)
        await fs.put_files(workspace, _workspace_bundle(input_path, manifest_path))
        run_result = await runner.run_program(
            workspace,
            WorkspaceRunProgramSpec(
                cmd="python3",
                args=[
                    "skills/code-review/scripts/rule_runner.py",
                    "--input",
                    "inputs/parsed_input.json",
                    "--manifest",
                    "inputs/skill_manifest.json",
                    "--output",
                    "outputs/rule_result.json",
                ],
                cwd="work",
                timeout=timeout_sec,
                env={"PYTHONPATH": "."},
            ),
        )
        sandbox_run.exit_code = run_result.exit_code
        sandbox_run.duration_ms = int(run_result.duration * 1000) if run_result.duration else int(
            (time.monotonic() - started) * 1000)
        sandbox_run.stdout, sandbox_run.stdout_truncated = _truncate(redact_text(run_result.stdout), output_limit_bytes)
        sandbox_run.stderr, sandbox_run.stderr_truncated = _truncate(redact_text(run_result.stderr), output_limit_bytes)
        if run_result.timed_out:
            sandbox_run.status = SandboxStatus.TIMEOUT
            sandbox_run.error_type = "TimeoutExpired"
            result = _failure_result(sandbox_run.stderr or f"Command timed out after {timeout_sec:g}s")
        elif run_result.exit_code != 0:
            sandbox_run.status = SandboxStatus.FAILED
            sandbox_run.error_type = "CommandFailed"
            result = _failure_result(sandbox_run.stderr or sandbox_run.stdout)
        else:
            manifest = await fs.collect_outputs(
                workspace,
                WorkspaceOutputSpec(
                    globs=["work/outputs/rule_result.json"],
                    max_files=1,
                    max_file_bytes=output_limit_bytes,
                    max_total_bytes=output_limit_bytes,
                    inline=True,
                ),
            )
            if not manifest.files:
                sandbox_run.status = SandboxStatus.FAILED
                sandbox_run.error_type = "MissingOutput"
                result = _failure_result("workspace rule_result.json was not collected")
            elif manifest.limits_hit:
                sandbox_run.status = SandboxStatus.FAILED
                sandbox_run.error_type = "OutputLimitExceeded"
                result = _failure_result("workspace output collection hit configured limits")
            else:
                try:
                    result = redact_mapping(json.loads(manifest.files[0].content or "{}"))
                    sandbox_run.status = SandboxStatus.SUCCESS
                except json.JSONDecodeError as ex:
                    sandbox_run.status = SandboxStatus.FAILED
                    sandbox_run.error_type = "InvalidOutput"
                    result = _failure_result(f"workspace rule_result.json is invalid JSON: {ex}")
    except Exception as ex:
        sandbox_run.status = SandboxStatus.FAILED
        sandbox_run.error_type = type(ex).__name__
        sandbox_run.stderr = redact_text(str(ex))
        result = _failure_result(str(ex))
    finally:
        sandbox_run.duration_ms = sandbox_run.duration_ms or int((time.monotonic() - started) * 1000)
        if workspace_creation_started:
            try:
                await workspace_runtime.manager().cleanup(exec_id)
            except Exception as ex:  # pragma: no cover - backend cleanup is best effort
                cleanup_error = redact_text(str(ex))
                if sandbox_run.status is SandboxStatus.SUCCESS:
                    sandbox_run.status = SandboxStatus.FAILED
                    sandbox_run.error_type = "CleanupFailed"
                sandbox_run.stderr = f"{sandbox_run.stderr}; {cleanup_error}".strip("; ")
    return sandbox_run, result


def _resolve_runtime_adapter(
    runtime: RuntimeKind,
    *,
    container_image: str = "python:3-slim",
    docker_base_url: str = "",
    timeout_sec: float = 30.0,
) -> _RuntimeAdapterResult:
    if runtime in {RuntimeKind.DRY_RUN, RuntimeKind.LOCAL_DEV}:
        return _RuntimeAdapterResult(runtime=runtime, available=True)
    if runtime is RuntimeKind.CONTAINER:
        return _probe_container_runtime(container_image=container_image, docker_base_url=docker_base_url)
    if runtime is RuntimeKind.CUBE:
        return _probe_cube_runtime(timeout_sec=timeout_sec)
    return _RuntimeAdapterResult(runtime=runtime, available=False, reason=f"unsupported runtime: {runtime.value}")


def _probe_container_runtime(*, container_image: str, docker_base_url: str = "") -> _RuntimeAdapterResult:
    try:
        from trpc_agent_sdk.code_executors import create_container_workspace_runtime
        from trpc_agent_sdk.code_executors.container import ContainerConfig
        runtime = create_container_workspace_runtime(
            container_config=ContainerConfig(base_url=docker_base_url or None, image=container_image),
            host_config={"network_mode": "none"},
            auto_inputs=False,
        )
    except Exception as ex:  # pragma: no cover - depends on optional local environment
        return _RuntimeAdapterResult(
            runtime=RuntimeKind.CONTAINER,
            available=False,
            reason=f"container runtime is unavailable: {type(ex).__name__}: {ex}",
        )
    return _RuntimeAdapterResult(runtime=RuntimeKind.CONTAINER,
                                 available=True,
                                 adapter=_WorkspaceRuntimeAdapter(RuntimeKind.CONTAINER, runtime))


def _probe_cube_runtime(*, timeout_sec: float) -> _RuntimeAdapterResult:
    try:
        from trpc_agent_sdk.code_executors.cube import CubeClientConfig
        from trpc_agent_sdk.code_executors.cube import CubeWorkspaceRuntimeConfig
        from trpc_agent_sdk.code_executors.cube import create_cube_sandbox_client
        from trpc_agent_sdk.code_executors.cube import create_cube_workspace_runtime
        cfg = CubeClientConfig(auto_recover=True, execute_timeout=timeout_sec)
        cfg.resolve_template()
        cfg.resolve_api_url()
        cfg.resolve_api_key()
    except Exception as ex:  # pragma: no cover - depends on optional local environment
        return _RuntimeAdapterResult(
            runtime=RuntimeKind.CUBE,
            available=False,
            reason=f"cube runtime is unavailable: {type(ex).__name__}: {ex}",
        )

    async def create_client() -> Any:
        return await create_cube_sandbox_client(cfg)

    def create_runtime(client: Any) -> Any:
        return create_cube_workspace_runtime(
            sandbox_client=client,
            execute_timeout=timeout_sec,
            workspace_cfg=CubeWorkspaceRuntimeConfig(),
        )

    return _RuntimeAdapterResult(runtime=RuntimeKind.CUBE,
                                 available=True,
                                 adapter=_CubeWorkspaceRuntimeAdapter(RuntimeKind.CUBE, create_client, create_runtime))


def _workspace_bundle(input_path: Path, manifest_path: Path) -> list[Any]:
    from trpc_agent_sdk.code_executors import WorkspacePutFileInfo

    example_root = Path(__file__).parents[1]
    rule_runner_path = example_root / "skills" / "code-review" / "scripts" / "rule_runner.py"
    return [
        WorkspacePutFileInfo(path="work/agent/__init__.py", content=b"", mode=0o644),
        WorkspacePutFileInfo(
            path="work/agent/models.py",
            content=(example_root / "agent" / "models.py").read_bytes(),
            mode=0o644,
        ),
        WorkspacePutFileInfo(path="work/agent/review_rules.py",
                             content=(example_root / "agent" / "review_rules.py").read_bytes(),
                             mode=0o644),
        WorkspacePutFileInfo(path="work/agent/sanitizer.py",
                             content=(example_root / "agent" / "sanitizer.py").read_bytes(),
                             mode=0o644),
        WorkspacePutFileInfo(path="work/skills/code-review/scripts/rule_runner.py",
                             content=rule_runner_path.read_bytes(),
                             mode=0o755),
        WorkspacePutFileInfo(path="work/inputs/parsed_input.json", content=input_path.read_bytes(), mode=0o600),
        WorkspacePutFileInfo(path="work/inputs/skill_manifest.json", content=manifest_path.read_bytes(), mode=0o600),
    ]


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as ex:  # pragma: no cover - only used from async callers
            box["error"] = ex

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    trimmed = encoded[:limit].decode("utf-8", errors="ignore")
    return trimmed, True


def _failure_result(message: str) -> dict[str, Any]:
    return {
        "schema_version": "code-review.rules.v1",
        "skill_name": "code-review",
        "findings": [],
        "diagnostics": [redact_text(message)],
    }
