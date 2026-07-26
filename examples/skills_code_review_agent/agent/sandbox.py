"""Sandbox adapter built on repository workspace runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceResourceLimits
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime

from .constants import APP_NAME
from .constants import CLEANUP_TIMEOUT_SECONDS
from .constants import LOCAL_RUNTIME_OPT_IN_ENV
from .constants import MAX_OUTPUT_BYTES
from .models import ExecutionPlan
from .models import SandboxRun
from .models import SandboxStatus
from .policy import SecretRedactor
from .policy import calculate_plan_digest

CPU_PERCENT_LIMIT = 100
MEMORY_LIMIT_MB = 256
PROCESS_LIMIT = 32
TRUNCATION_SUFFIX = "\n[OUTPUT TRUNCATED]"
MILLISECONDS_PER_SECOND = 1_000
SKILL_ASSET_PATHS = (
    "SKILL.md",
    "references/rules.md",
    "scripts/scan_rules.py",
)


@dataclass(frozen=True)
class RuntimeHandle:
    """Runtime plus capabilities guaranteed by its factory."""

    runtime: BaseWorkspaceRuntime
    kind: str
    network_disabled: bool


@dataclass(frozen=True)
class SandboxExecution:
    """Sandbox record and bounded primary output."""

    run: SandboxRun
    output: str


@dataclass(frozen=True)
class _RunContext:
    started: float
    plan: ExecutionPlan
    deadline: float


@dataclass
class _ExecutionState:
    exec_id: str
    context: _RunContext
    workspace: object | None = None


def create_runtime(kind: str, work_root: Path | None = None) -> RuntimeHandle:
    """Create a supported runtime with safe defaults."""
    if kind == "container":
        runtime = create_container_workspace_runtime(
            host_config={"network_mode": "none"},
            auto_inputs=False,
        )
        return RuntimeHandle(runtime=runtime, kind=kind, network_disabled=True)
    if kind != "local":
        raise ValueError(f"unsupported runtime: {kind}")
    if os.getenv(LOCAL_RUNTIME_OPT_IN_ENV) != "1":
        raise PermissionError("local runtime requires explicit development opt-in")
    root = work_root or Path(tempfile.gettempdir()) / APP_NAME
    runtime = create_local_workspace_runtime(
        work_root=str(root),
        read_only_staged_skill=True,
        auto_inputs=False,
        enable_provider_env=False,
    )
    return RuntimeHandle(runtime=runtime, kind=kind, network_disabled=False)


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = TRUNCATION_SUFFIX.encode("utf-8")
    if limit <= len(suffix):
        return encoded[:limit].decode("utf-8", errors="ignore")
    content = encoded[:limit - len(suffix)]
    return content.decode("utf-8", errors="ignore") + TRUNCATION_SUFFIX


def _digest_assets(assets: tuple[WorkspacePutFileInfo, ...]) -> str:
    digest = hashlib.sha256()
    for asset in assets:
        digest.update(asset.path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(asset.content)
        digest.update(b"\x00")
    return digest.hexdigest()


def load_skill_assets(skill_dir: Path) -> tuple[WorkspacePutFileInfo, ...]:
    """Read the fixed trusted Skill files into an immutable tuple."""
    assets = []
    for relative in SKILL_ASSET_PATHS:
        source = skill_dir / relative
        assets.append(WorkspacePutFileInfo(
            path=f"skills/code-review/{relative}",
            content=source.read_bytes(),
        ), )
    return tuple(assets)


def skill_assets_digest(skill_dir: Path) -> str:
    """Calculate the digest embedded in an execution plan."""
    return _digest_assets(load_skill_assets(skill_dir))


class SandboxExecutor:
    """Stage fixed inputs, execute one plan, collect output, and clean up."""

    def __init__(
        self,
        handle: RuntimeHandle,
        redactor: SecretRedactor,
        skill_dir: Path,
    ) -> None:
        self._handle = handle
        self._redactor = redactor
        self._skill_assets = load_skill_assets(skill_dir)
        self._skill_digest = _digest_assets(self._skill_assets)

    @property
    def skill_digest(self) -> str:
        """Digest of the exact Skill bytes staged for execution."""
        return self._skill_digest

    async def execute(
        self,
        plan: ExecutionPlan,
        input_bytes: bytes,
    ) -> SandboxExecution:
        """Execute one already-approved immutable plan."""
        self._validate_plan(plan, input_bytes)
        started = time.monotonic()
        context = _RunContext(
            started=started,
            plan=plan,
            deadline=started + plan.timeout_seconds,
        )
        state = _ExecutionState(exec_id=f"review-{uuid.uuid4().hex}", context=context)
        execution: SandboxExecution | None = None
        try:
            state.workspace = await self._within_budget(
                self._handle.runtime.manager().create_workspace(state.exec_id),
                context,
            )
            result, output = await self._run_steps(state, input_bytes)
            execution = self._normalize_result(result, output, context)
        except asyncio.TimeoutError:
            execution = self._failure(
                SandboxStatus.TIMED_OUT,
                asyncio.TimeoutError(),
                context,
            )
        except Exception as exc:  # pylint: disable=broad-except
            execution = self._failure(SandboxStatus.FAILED, exc, context)
        finally:
            cleanup_error = await self._cleanup(state)
        if cleanup_error:
            return self._record_cleanup_error(execution, cleanup_error, context)
        return execution

    def _record_cleanup_error(
        self,
        execution: SandboxExecution,
        error: Exception,
        context: _RunContext,
    ) -> SandboxExecution:
        if execution.run.status == SandboxStatus.SUCCEEDED:
            return self._failure(SandboxStatus.FAILED, error, context)
        detail = self._redactor.redact_text(str(error))
        limit = min(context.plan.output_limit_bytes, MAX_OUTPUT_BYTES)
        stdout, stderr, output = self._bounded_outputs(
            execution.run.stdout,
            f"{execution.run.stderr}\ncleanup: {detail}".strip(),
            execution.output,
            limit,
        )
        error_type = f"{execution.run.error_type}+{type(error).__name__}"
        return SandboxExecution(
            run=execution.run.model_copy(update={
                "stdout": stdout,
                "stderr": stderr,
                "error_type": error_type
            }, ),
            output=output,
        )

    def _validate_plan(self, plan: ExecutionPlan, input_bytes: bytes) -> None:
        if plan.digest != calculate_plan_digest(plan):
            raise ValueError("execution plan digest mismatch")
        if plan.input_digest != hashlib.sha256(input_bytes).hexdigest():
            raise ValueError("execution input digest mismatch")
        if plan.skill_digest != self._skill_digest:
            raise ValueError("execution Skill digest mismatch")
        if plan.runtime != self._handle.kind:
            raise ValueError("execution plan runtime mismatch")
        if plan.network_allowed or (plan.runtime == "container" and not self._handle.network_disabled):
            raise PermissionError("runtime cannot prove network isolation")

    async def _run_steps(self, state: _ExecutionState, input_bytes: bytes):
        input_file = WorkspacePutFileInfo(
            path=state.context.plan.input_path,
            content=input_bytes,
        )
        files = [*self._skill_assets, input_file]
        await self._within_budget(
            self._handle.runtime.fs().put_files(state.workspace, files),
            state.context,
        )
        result = await self._within_budget(
            self._run(state.workspace, state.context.plan),
            state.context,
        )
        output = await self._within_budget(
            self._collect(state.workspace, state.context.plan),
            state.context,
        )
        return result, output

    async def _within_budget(self, operation, context: _RunContext):
        remaining = context.deadline - time.monotonic()
        if remaining <= 0:
            close = getattr(operation, "close", None)
            if close:
                close()
            raise asyncio.TimeoutError
        return await asyncio.wait_for(operation, timeout=remaining)

    async def _run(self, workspace, plan: ExecutionPlan):
        spec = WorkspaceRunProgramSpec(
            cmd=plan.argv[0],
            args=list(plan.argv[1:]),
            env=dict(plan.environment),
            cwd=plan.cwd,
            timeout=plan.timeout_seconds,
            limits=WorkspaceResourceLimits(
                cpu_percent=CPU_PERCENT_LIMIT,
                memory_mb=MEMORY_LIMIT_MB,
                max_pids=PROCESS_LIMIT,
            ),
        )
        return await self._handle.runtime.runner().run_program(workspace, spec)

    async def _collect(self, workspace, plan: ExecutionPlan) -> str:
        if self._handle.kind == "local":
            return self._collect_local(workspace.path, plan)
        files = await self._handle.runtime.fs().collect(
            workspace,
            [plan.output_path],
        )
        if not files:
            return ""
        output = files[0]
        if output.truncated or output.size_bytes > plan.output_limit_bytes:
            raise ValueError("sandbox output exceeded configured limit")
        return _truncate(output.content, plan.output_limit_bytes)

    @staticmethod
    def _collect_local(workspace_path: str, plan: ExecutionPlan) -> str:
        root = Path(workspace_path).resolve()
        output = (root / plan.output_path).resolve()
        if root not in output.parents or output.is_symlink():
            raise ValueError("sandbox output escaped workspace")
        size = output.stat().st_size
        if size > plan.output_limit_bytes:
            raise ValueError("sandbox output exceeded configured limit")
        return output.read_text(encoding="utf-8")

    def _normalize_result(self, result, output: str, context: _RunContext) -> SandboxExecution:
        status = SandboxStatus.SUCCEEDED
        if result.timed_out:
            status = SandboxStatus.TIMED_OUT
        elif result.exit_code:
            status = SandboxStatus.FAILED
        limit = min(context.plan.output_limit_bytes, MAX_OUTPUT_BYTES)
        stdout, stderr, output = self._bounded_outputs(
            result.stdout,
            result.stderr,
            output,
            limit,
        )
        run = SandboxRun(
            status=status,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_ms=int((time.monotonic() - context.started) * MILLISECONDS_PER_SECOND),
            stdout=self._redactor.redact_text(stdout),
            stderr=self._redactor.redact_text(stderr),
            error_type=None if status == SandboxStatus.SUCCEEDED else "ProgramError",
        )
        return SandboxExecution(run=run, output=self._redactor.redact_text(output))

    @staticmethod
    def _bounded_outputs(
        stdout: str,
        stderr: str,
        output: str,
        limit: int,
    ) -> tuple[str, str, str]:
        bounded = []
        remaining = limit
        for value in (stdout, stderr, output):
            item = _truncate(value, remaining)
            bounded.append(item)
            remaining = max(0, remaining - len(item.encode("utf-8")))
        return bounded[0], bounded[1], bounded[2]

    def _failure(
        self,
        status: SandboxStatus,
        error: Exception,
        context: _RunContext,
    ) -> SandboxExecution:
        limit = min(context.plan.output_limit_bytes, MAX_OUTPUT_BYTES)
        run = SandboxRun(
            status=status,
            timed_out=status == SandboxStatus.TIMED_OUT,
            duration_ms=int((time.monotonic() - context.started) * MILLISECONDS_PER_SECOND),
            stderr=_truncate(self._redactor.redact_text(str(error)), limit),
            error_type=type(error).__name__,
        )
        return SandboxExecution(run=run, output="")

    async def _cleanup(self, state: _ExecutionState) -> Exception | None:
        if state.workspace is None:
            return None
        try:
            await asyncio.wait_for(
                self._handle.runtime.manager().cleanup(state.exec_id),
                timeout=CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return exc
        return None
