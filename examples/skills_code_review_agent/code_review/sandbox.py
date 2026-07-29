#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Strict SDK workspace factory, Skill staging and bounded sandbox primitives."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import ENV_OUTPUT_DIR
from trpc_agent_sdk.code_executors import ENV_RUN_DIR
from trpc_agent_sdk.code_executors import ENV_SKILLS_DIR
from trpc_agent_sdk.code_executors import ENV_WORK_DIR
from trpc_agent_sdk.code_executors import LocalProgramRunner
from trpc_agent_sdk.code_executors import WORKSPACE_ENV_DIR_KEY
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.stager import SkillStageRequest
from trpc_agent_sdk.skills.tools import CopySkillStager
from trpc_agent_sdk.context import new_invocation_context_id

from code_review.config import ReviewConfig
from code_review.redaction import redact_text
from code_review.skill_integrity import (
    IntegrityFile,
    canonical_source_sha256,
    parse_integrity_files,
    safe_script_relative_path,
)


_SKILL_NAME = "code-review"
_RUN_CHECKS_SCRIPT_ID = "run_checks"
_ALLOWED_CONTAINER_CONFIG_KEYS = frozenset({"network_mode"})


class SandboxConfigurationError(ValueError):
    """表示在创建 runtime 前发现的不可接受沙箱配置。"""


class SandboxBudgetExceeded(ValueError):
    """表示本次运行会在启动前突破锁定的评审资源预算。"""


class SandboxStageError(RuntimeError):
    """表示 Skill staging 或 staging 后摘要复验失败。"""


@dataclass(frozen=True)
class SandboxRuntimeSelection:
    """描述已选 SDK runtime 及其可供治理层验证的有效网络配置。"""

    runtime: BaseWorkspaceRuntime
    runtime_type: str
    effective_network_mode: str | None
    network_policy_verified: bool
    explicit_local: bool


@dataclass(frozen=True)
class BudgetReservation:
    """保存一次已经通过预检的运行编号、时间和输出额度。"""

    run_number: int
    timeout_seconds: int
    output_bytes: int


@dataclass(frozen=True)
class OutputCapture:
    """保存受总字节上限约束的 stdout/stderr 摘要及截断标记。"""

    stdout: str
    stderr: str
    truncated: bool


@dataclass(frozen=True)
class SandboxRunCapture:
    """收敛 SDK 单次运行的超时、退出码、耗时和已限额输出摘要。"""

    exit_code: int | None
    timed_out: bool
    duration_ms: int
    output: OutputCapture


@dataclass(frozen=True)
class StagedSkill:
    """记录已复验的 workspace 相对 Skill 目录和入口脚本路径。"""

    workspace_skill_dir: str
    entrypoint: str
    script_id: str
    sha256: str


@dataclass(frozen=True)
class ManifestScript:
    """保存 manifest 中已校验入口和完整执行文件闭包。"""

    entrypoint: str
    sha256: str
    files: tuple[IntegrityFile, ...]


@dataclass(frozen=True)
class SandboxInvocationContext:
    """为 SDK Skill stager 提供仅含调用标识的最小运行上下文，避免伪造完整 Agent 会话。"""

    invocation_id: str


@dataclass(frozen=True)
class AgentWorkspaceBinding:
    """保存本次 Agent skill_load 已创建的 workspace 标识和真实调用上下文。"""

    workspace_id: str
    context: Any


class SandboxBudget:
    """在宿主启动 runtime 前集中预检并累计单次与全局沙箱预算。"""

    def __init__(self, config: ReviewConfig) -> None:
        """绑定不可变 ReviewConfig，避免调用方单独放宽运行资源上限。"""

        self._config = config
        self._runs_started = 0
        self._reserved_seconds = 0
        self._reserved_output_bytes = 0

    def reserve(self, *, timeout_seconds: int, output_bytes: int) -> BudgetReservation:
        """预留一次运行额度；超过任一锁定上限时在执行前抛出受控错误代码。"""

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or isinstance(output_bytes, bool)
            or not isinstance(output_bytes, int)
            or output_bytes <= 0
        ):
            raise SandboxBudgetExceeded("sandbox_budget_invalid")
        if timeout_seconds > self._config.per_run_timeout_seconds:
            raise SandboxBudgetExceeded("sandbox_timeout_budget_exceeded")
        if output_bytes > self._config.max_output_bytes_per_run:
            raise SandboxBudgetExceeded("sandbox_output_budget_exceeded")
        if self._runs_started + 1 > self._config.max_sandbox_runs:
            raise SandboxBudgetExceeded("sandbox_run_budget_exceeded")
        if self._reserved_seconds + timeout_seconds > self._config.sandbox_time_budget_seconds:
            raise SandboxBudgetExceeded("sandbox_time_budget_exceeded")
        if self._reserved_output_bytes + output_bytes > self._config.max_output_bytes_per_review:
            raise SandboxBudgetExceeded("sandbox_review_output_budget_exceeded")
        self._runs_started += 1
        self._reserved_seconds += timeout_seconds
        self._reserved_output_bytes += output_bytes
        return BudgetReservation(
            run_number=self._runs_started,
            timeout_seconds=timeout_seconds,
            output_bytes=output_bytes,
        )


class SanitizedLocalProgramRunner(LocalProgramRunner):
    """在 SDK local fallback 上移除宿主环境，仅保留受控运行变量。"""

    def _build_program_env(
        self,
        workspace: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
    ) -> dict[str, str]:
        """过滤 SDK 构造的环境，阻止 API Key、token 和任意宿主值进入子进程。"""

        candidate = super()._build_program_env(workspace, spec)
        allowed_names = {
            *(spec.env or {}),
            WORKSPACE_ENV_DIR_KEY,
            ENV_SKILLS_DIR,
            ENV_WORK_DIR,
            ENV_OUTPUT_DIR,
            ENV_RUN_DIR,
        }
        return {
            name: value
            for name, value in candidate.items()
            if name in allowed_names
        }


class _SanitizedLocalRuntime(BaseWorkspaceRuntime):
    """复用 SDK local manager/fs，并用净化 runner 替换执行边界。"""

    def __init__(self, runtime: BaseWorkspaceRuntime) -> None:
        """绑定 SDK 创建的 local runtime，不修改共享 SDK 对象。"""

        self._runtime = runtime
        self._runner = SanitizedLocalProgramRunner()

    def manager(self, ctx: Any = None) -> Any:
        """返回 SDK 原生 workspace manager。"""

        return self._runtime.manager(ctx)

    def fs(self, ctx: Any = None) -> Any:
        """返回 SDK 原生 workspace filesystem。"""

        return self._runtime.fs(ctx)

    def runner(self, ctx: Any = None) -> SanitizedLocalProgramRunner:
        """返回不继承宿主环境值的 local program runner。"""

        return self._runner

    def describe(self, ctx: Any = None) -> Any:
        """透传 SDK local runtime 的能力说明。"""

        return self._runtime.describe(ctx)


class SdkSkillSandbox:
    """将 C2 的 runtime、staging 与限额原语组合为 ReviewPipeline 可调用的 SDK 沙箱端口。"""

    def __init__(
        self,
        selection: SandboxRuntimeSelection,
        skill_root: Path,
        *,
        config: ReviewConfig | None = None,
    ) -> None:
        """绑定已治理的 runtime 和 Skill 根目录；任务 workspace 仅在 execute 后短暂保存到 cleanup。"""

        self.runtime_type = selection.runtime_type
        self._runtime = selection.runtime
        self._skill_root = Path(skill_root)
        self._config = ReviewConfig() if config is None else config
        self._budgets: dict[str, SandboxBudget] = {}
        self._workspaces: dict[str, WorkspaceInfo] = {}
        self._workspace_ids: dict[str, str] = {}
        self._contexts: dict[str, Any] = {}
        self._agent_workspace: ContextVar[AgentWorkspaceBinding | None] = ContextVar(
            "code_review_agent_workspace",
            default=None,
        )

    @contextmanager
    def bind_agent_workspace(self, workspace_id: str, context: Any) -> Iterator[None]:
        """把 skill_load 的 session workspace 限定绑定到当前 skill_run/pipeline 调用。"""

        if not workspace_id:
            raise ValueError("agent_workspace_id_required")
        token = self._agent_workspace.set(
            AgentWorkspaceBinding(workspace_id=workspace_id, context=context)
        )
        try:
            yield
        finally:
            self._agent_workspace.reset(token)

    @property
    def container_id(self) -> str | None:
        """返回 SDK 容器 runtime 的实际 Docker ID，仅供终端 INFO 诊断且绝不持久化。"""

        if self.runtime_type != "container":
            return None
        client = getattr(self._runtime, "container", None)
        container = getattr(client, "container", None)
        container_id = getattr(container, "id", None)
        if not isinstance(container_id, str) or len(container_id) != 64:
            return None
        if any(character not in "0123456789abcdef" for character in container_id.lower()):
            return None
        return container_id

    def execute(self, *, task_id: str, change_set: Any, config: ReviewConfig) -> dict[str, Any]:
        """同步执行一个已授权的 run_checks 请求，并把任何运行失败收敛为结构化数据。"""

        if config != self._config:
            return _sandbox_error("sandbox_config_mismatch")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._execute_async(task_id, change_set))
        return _sandbox_error("sandbox_event_loop_unsupported")

    def cleanup(self, *, task_id: str) -> None:
        """清理本任务 workspace；异常由 pipeline 转为不含路径的 cleanup warning。"""

        if task_id not in self._workspaces:
            self._budgets.pop(task_id, None)
            return
        context = self._contexts.get(task_id)
        workspace_id = self._workspace_ids.get(task_id, task_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._runtime.manager(context).cleanup(workspace_id, context))
            self._workspaces.pop(task_id, None)
            self._workspace_ids.pop(task_id, None)
            self._contexts.pop(task_id, None)
            self._budgets.pop(task_id, None)
            return
        raise RuntimeError("sandbox_event_loop_unsupported")

    async def _execute_async(self, task_id: str, change_set: Any) -> dict[str, Any]:
        """创建 workspace、staging Skill、写入最小 diff 载荷、运行固定 argv 并收集限制输出。"""

        try:
            budget = self._budgets.setdefault(
                task_id,
                SandboxBudget(self._config),
            )
            budget.reserve(
                timeout_seconds=self._config.per_run_timeout_seconds,
                output_bytes=self._config.max_output_bytes_per_run,
            )
        except SandboxBudgetExceeded as exc:
            return _sandbox_error(str(exc), status="blocked")
        try:
            binding = self._agent_workspace.get()
            context = (
                binding.context
                if binding is not None
                else SandboxInvocationContext(invocation_id=new_invocation_context_id())
            )
            workspace_id = binding.workspace_id if binding is not None else task_id
            workspace = await self._runtime.manager(context).create_workspace(workspace_id, context)
            self._workspaces[task_id] = workspace
            self._workspace_ids[task_id] = workspace_id
            self._contexts[task_id] = context
            if binding is None:
                staged = await stage_code_review_skill(
                    self._runtime,
                    workspace,
                    self._skill_root,
                    ctx=context,
                )
            else:
                staged = await verify_loaded_code_review_skill(
                    self._runtime,
                    workspace,
                    self._skill_root,
                    ctx=context,
                )
            payload = change_set_payload(change_set)
            uses_host_local_workspace = (
                self.runtime_type == "local" and _local_workspace_path(workspace, ".") is not None
            )
            input_path = "work/inputs/diff.json"
            await self._runtime.fs(context).put_files(
                workspace,
                [WorkspacePutFileInfo(path=input_path, content=payload)],
                context,
            )
            result = await self._runtime.runner(context).run_program(
                workspace,
                build_run_spec(
                    staged,
                    self._config,
                    python_executable=sys.executable if uses_host_local_workspace else "python3",
                    use_workspace_root=uses_host_local_workspace,
                ),
                context,
            )
            capture = capture_workspace_run(result, max_output_bytes=self._config.max_output_bytes_per_run)
            if capture.timed_out:
                return _sandbox_result(capture, status="timeout", error_type="timeout")
            if capture.exit_code != 0:
                return _sandbox_result(capture, status="failed", error_type="nonzero_exit")
            if uses_host_local_workspace:
                findings, output_truncated = _local_findings_from_workspace(
                    workspace,
                    staged,
                    max_output_bytes=self._config.max_output_bytes_per_run,
                    use_workspace_root=True,
                )
                if output_truncated:
                    return _sandbox_result(capture, status="error", truncated=True, error_type="output_truncated")
            else:
                outputs = await self._runtime.fs(context).collect_outputs(
                    workspace,
                    build_output_spec(self._config),
                    context,
                )
                if outputs.limits_hit:
                    return _sandbox_result(capture, status="error", truncated=True, error_type="output_truncated")
                findings = _findings_from_outputs(outputs)
            return _sandbox_result(capture, status="ok", findings=findings)
        except SandboxStageError as exc:
            return _sandbox_error(str(exc))
        except Exception:
            return _sandbox_error("sandbox_runtime_error")


def create_sandbox_runtime(
    runtime_type: str = "container",
    *,
    host_config: Mapping[str, Any] | None = None,
    explicit_local: bool = False,
    local_work_root: str = "",
    container_runtime_factory: Callable[..., BaseWorkspaceRuntime] = create_container_workspace_runtime,
    local_runtime_factory: Callable[..., BaseWorkspaceRuntime] = create_local_workspace_runtime,
    cube_runtime_factory: Callable[[], BaseWorkspaceRuntime] | None = None,
) -> SandboxRuntimeSelection:
    """创建 SDK workspace runtime，并把有效网络配置显式交给治理层复验。"""

    if runtime_type == "container":
        effective_config = _strict_container_host_config(host_config)
        runtime = container_runtime_factory(host_config=effective_config)
        return SandboxRuntimeSelection(
            runtime=runtime,
            runtime_type="container",
            effective_network_mode="none",
            network_policy_verified=True,
            explicit_local=False,
        )
    if runtime_type == "local":
        if not explicit_local:
            raise SandboxConfigurationError("local_runtime_not_explicit")
        runtime = _SanitizedLocalRuntime(
            local_runtime_factory(
                work_root=local_work_root,
                read_only_staged_skill=True,
                auto_inputs=False,
            )
        )
        return SandboxRuntimeSelection(
            runtime=runtime,
            runtime_type="local",
            effective_network_mode=None,
            network_policy_verified=False,
            explicit_local=True,
        )
    if runtime_type == "cube":
        if cube_runtime_factory is None:
            raise SandboxConfigurationError("cube_runtime_unavailable")
        return SandboxRuntimeSelection(
            runtime=cube_runtime_factory(),
            runtime_type="cube",
            effective_network_mode=None,
            network_policy_verified=False,
            explicit_local=False,
        )
    raise SandboxConfigurationError("sandbox_runtime_unsupported")


def build_sandbox_environment() -> dict[str, str]:
    """构造最小白名单环境，不透传宿主 API Key、token 或任意现有环境变量。"""

    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }


def build_output_spec(config: ReviewConfig) -> WorkspaceOutputSpec:
    """为单一 findings 文件构造 SDK 声明式输出收集限额。"""

    return WorkspaceOutputSpec(
        globs=["out/findings.json"],
        max_files=1,
        max_file_bytes=config.max_output_bytes_per_run,
        max_total_bytes=config.max_output_bytes_per_run,
        save=False,
        inline=True,
    )


def build_run_spec(
    staged_skill: StagedSkill,
    config: ReviewConfig,
    *,
    python_executable: str = "python3",
    use_workspace_root: bool = False,
) -> WorkspaceRunProgramSpec:
    """构造固定 run_checks argv、白名单环境和超时；local 可从 workspace 根目录运行以避免依赖链接。"""

    args = [staged_skill.entrypoint] if use_workspace_root else ["scripts/run_checks.py"]
    cwd = "." if use_workspace_root else staged_skill.workspace_skill_dir

    return WorkspaceRunProgramSpec(
        cmd=python_executable,
        args=args,
        env=build_sandbox_environment(),
        cwd=cwd,
        timeout=config.per_run_timeout_seconds,
    )


async def stage_code_review_skill(
    runtime: BaseWorkspaceRuntime,
    workspace: WorkspaceInfo,
    skill_root: Path,
    *,
    script_id: str = _RUN_CHECKS_SCRIPT_ID,
    ctx: Any = None,
) -> StagedSkill:
    """经 SDK SkillRepository 与 CopySkillStager 复制 Skill，并在沙箱内复验入口摘要。"""

    resolved_root = _validate_skill_root(skill_root)
    repository = create_default_skill_repository(
        str(resolved_root.parent),
        workspace_runtime=runtime,
        enable_hot_reload=False,
        use_cached_repository=False,
    )
    try:
        repository_root = Path(repository.path(_SKILL_NAME)).resolve()
    except (OSError, ValueError) as exc:
        raise SandboxStageError("skill_repository_lookup_failed") from exc
    if repository_root != resolved_root:
        raise SandboxStageError("skill_repository_root_mismatch")

    try:
        result = await CopySkillStager().stage_skill(
            SkillStageRequest(
                skill_name=_SKILL_NAME,
                repository=repository,
                workspace=workspace,
                ctx=ctx,
            )
        )
    except Exception as exc:
        raise SandboxStageError(f"skill_stage_{type(exc).__name__.lower()}") from exc

    definition = _manifest_entry(resolved_root, script_id)
    actual_sha256 = await _verify_staged_skill_files(
        runtime,
        workspace,
        result.workspace_skill_dir,
        definition,
        ctx=ctx,
    )
    workspace_entrypoint = (
        f"{result.workspace_skill_dir}/scripts/{definition.entrypoint}"
    )
    return StagedSkill(
        workspace_skill_dir=result.workspace_skill_dir,
        entrypoint=workspace_entrypoint,
        script_id=script_id,
        sha256=actual_sha256,
    )


async def verify_loaded_code_review_skill(
    runtime: BaseWorkspaceRuntime,
    workspace: WorkspaceInfo,
    skill_root: Path,
    *,
    script_id: str = _RUN_CHECKS_SCRIPT_ID,
    ctx: Any = None,
) -> StagedSkill:
    """复验 skill_load 已复制到当前 workspace 的固定入口，不再次 staging Skill。"""

    resolved_root = _validate_skill_root(skill_root)
    definition = _manifest_entry(resolved_root, script_id)
    workspace_skill_dir = f"skills/{_SKILL_NAME}"
    actual_sha256 = await _verify_staged_skill_files(
        runtime,
        workspace,
        workspace_skill_dir,
        definition,
        ctx=ctx,
    )
    workspace_entrypoint = (
        f"{workspace_skill_dir}/scripts/{definition.entrypoint}"
    )
    return StagedSkill(
        workspace_skill_dir=workspace_skill_dir,
        entrypoint=workspace_entrypoint,
        script_id=script_id,
        sha256=actual_sha256,
    )


async def _verify_staged_skill_files(
    runtime: BaseWorkspaceRuntime,
    workspace: WorkspaceInfo,
    workspace_skill_dir: str,
    definition: ManifestScript,
    *,
    ctx: Any,
) -> str:
    """逐文件复验 staged Skill 闭包，并返回入口脚本的规范化摘要。"""

    for integrity_file in definition.files:
        workspace_path = (
            f"{workspace_skill_dir}/scripts/{integrity_file.path}"
        )
        actual_content = await _staged_file_content(
            runtime,
            workspace,
            workspace_path,
            ctx=ctx,
        )
        actual_sha256 = canonical_source_sha256(actual_content)
        if actual_sha256 != integrity_file.sha256:
            raise SandboxStageError("staged_script_integrity_mismatch")
    return definition.sha256


async def _staged_file_content(
    runtime: BaseWorkspaceRuntime,
    workspace: WorkspaceInfo,
    workspace_path: str,
    *,
    ctx: Any,
) -> str:
    """通过 runtime collector 验证 staged 脚本；显式 local fallback 才使用等价的受限本地读取。"""

    try:
        collected = await runtime.fs(ctx).collect(
            workspace,
            [workspace_path],
            ctx,
        )
    except Exception:
        collected = []
    if len(collected) == 1 and collected[0].name == workspace_path:
        return collected[0].content
    local_path = _local_workspace_path(workspace, workspace_path)
    if local_path is None:
        raise SandboxStageError("staged_script_missing")
    try:
        return local_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SandboxStageError("staged_script_collect_failed") from exc


def _local_workspace_path(workspace: WorkspaceInfo, relative_path: str) -> Path | None:
    """在显式 local runtime 中把 workspace 相对路径映射到受 containment 约束的本机文件。"""

    try:
        root = Path(workspace.path).resolve(strict=True)
        candidate = (root / relative_path).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def _local_findings_from_workspace(
    workspace: WorkspaceInfo,
    staged_skill: StagedSkill,
    *,
    max_output_bytes: int,
    use_workspace_root: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """读取 local fallback 的受限 findings 文件；workspace 根模式不依赖 Windows Skill 链接。"""

    output_relative_path = "out/findings.json"
    if not use_workspace_root:
        output_relative_path = f"{staged_skill.workspace_skill_dir}/out/findings.json"

    output_path = _local_workspace_path(
        workspace,
        output_relative_path,
    )
    if output_path is None:
        raise ValueError("sandbox_output_missing")
    try:
        if output_path.stat().st_size > max_output_bytes:
            return [], True
        content = output_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("sandbox_output_missing") from exc
    payload = json.loads(content)
    findings = payload.get("findings") if isinstance(payload, Mapping) else None
    if not isinstance(findings, list):
        raise ValueError("sandbox_findings_invalid")
    return [dict(finding) for finding in findings if isinstance(finding, Mapping)], False


def bounded_output(stdout: str, stderr: str, *, max_bytes: int) -> OutputCapture:
    """按总字节额度截断 stdout/stderr，避免 UTF-8 半字符和无界日志占用。"""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("sandbox_output_limit_invalid")
    bounded_stdout, stdout_truncated = _truncate_utf8(stdout, max_bytes)
    remaining = max_bytes - len(bounded_stdout.encode("utf-8"))
    bounded_stderr, stderr_truncated = _truncate_utf8(stderr, remaining)
    return OutputCapture(
        stdout=bounded_stdout,
        stderr=bounded_stderr,
        truncated=stdout_truncated or stderr_truncated,
    )


def capture_workspace_run(result: Any, *, max_output_bytes: int) -> SandboxRunCapture:
    """从 SDK 运行结果提取最小运行事实，并在进入宿主后续处理前执行输出限额。"""

    raw_stdout = getattr(result, "stdout", "")
    raw_stderr = getattr(result, "stderr", "")
    output = bounded_output(
        redact_text(raw_stdout) if isinstance(raw_stdout, str) else "",
        redact_text(raw_stderr) if isinstance(raw_stderr, str) else "",
        max_bytes=max_output_bytes,
    )
    duration = getattr(result, "duration", 0.0)
    duration_ms = max(int(float(duration) * 1000), 0) if isinstance(duration, (int, float)) else 0
    exit_code = getattr(result, "exit_code", None)
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None
    return SandboxRunCapture(
        exit_code=exit_code,
        timed_out=bool(getattr(result, "timed_out", False)),
        duration_ms=duration_ms,
        output=output,
    )


def _strict_container_host_config(host_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """拒绝 bind、未知字段和 network_mode 覆盖，确保容器实例有效网络配置为 none。"""

    supplied = {} if host_config is None else dict(host_config)
    if "Binds" in supplied or "binds" in supplied or "volumes" in supplied:
        raise SandboxConfigurationError("host_mount_forbidden")
    unknown = set(supplied) - _ALLOWED_CONTAINER_CONFIG_KEYS
    if unknown:
        raise SandboxConfigurationError("container_host_config_unsupported")
    if supplied.get("network_mode", "none") != "none":
        raise SandboxConfigurationError("container_network_mode_invalid")
    return {"network_mode": "none"}


def _validate_skill_root(skill_root: Path) -> Path:
    """验证 host Skill 根目录是规范 code-review 目录，防止 staging 任意宿主路径。"""

    try:
        resolved_root = Path(skill_root).resolve(strict=True)
    except OSError as exc:
        raise SandboxStageError("skill_root_unavailable") from exc
    if resolved_root.name != _SKILL_NAME or not (resolved_root / "SKILL.md").is_file():
        raise SandboxStageError("skill_root_invalid")
    return resolved_root


def _manifest_entry(skill_root: Path, script_id: str) -> ManifestScript:
    """从受控 manifest 提取入口和完整脚本文件闭包。"""

    try:
        manifest = json.loads((skill_root / "scripts" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise SandboxStageError("manifest_unavailable") from exc
    scripts = manifest.get("scripts") if isinstance(manifest, Mapping) else None
    if not isinstance(scripts, list):
        raise SandboxStageError("manifest_invalid")
    for item in scripts:
        if not isinstance(item, Mapping) or item.get("script_id") != script_id:
            continue
        entrypoint = item.get("entrypoint")
        normalized_entrypoint = safe_script_relative_path(entrypoint)
        sha256 = item.get("sha256")
        integrity_files = parse_integrity_files(item.get("files"))
        if (
            normalized_entrypoint is None
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or integrity_files is None
        ):
            break
        integrity_by_path = {
            integrity_file.path: integrity_file.sha256
            for integrity_file in integrity_files
        }
        if integrity_by_path.get(normalized_entrypoint) != sha256:
            break
        return ManifestScript(
            entrypoint=normalized_entrypoint,
            sha256=sha256,
            files=integrity_files,
        )
    raise SandboxStageError("manifest_script_unregistered")


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """返回不超过字节额度的 UTF-8 前缀，必要时回退到完整字符边界。"""

    if not isinstance(text, str):
        return "", True
    if max_bytes <= 0:
        return "", bool(text)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    prefix = encoded[:max_bytes]
    while prefix:
        try:
            return prefix.decode("utf-8"), True
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return "", True


def _sandbox_error(error_type: str, *, status: str = "error") -> dict[str, Any]:
    """生成不含异常原文、路径或凭据的失败运行记录。"""

    return {
        "status": status,
        "exit_code": None,
        "timed_out": False,
        "truncated": False,
        "stdout_excerpt": "",
        "stderr_excerpt": "",
        "error_type": error_type,
        "duration_ms": 0,
        "findings": [],
    }


def _sandbox_result(
    capture: SandboxRunCapture,
    *,
    status: str,
    truncated: bool = False,
    error_type: str | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """将已脱敏且限额的 SDK 运行结果转换为 pipeline 所需的稳定字段。"""

    return {
        "status": status,
        "exit_code": capture.exit_code,
        "timed_out": capture.timed_out,
        "truncated": capture.output.truncated or truncated,
        "stdout_excerpt": capture.output.stdout,
        "stderr_excerpt": capture.output.stderr,
        "error_type": error_type,
        "duration_ms": capture.duration_ms,
        "findings": [] if findings is None else findings,
    }


def _findings_from_outputs(outputs: Any) -> list[dict[str, Any]]:
    """只从受 WorkspaceOutputSpec 限制且内联的 findings 文件读取结构化候选。"""

    files = getattr(outputs, "files", ())
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("sandbox_output_missing")
    content = getattr(files[0], "content", "")
    if not isinstance(content, str):
        raise ValueError("sandbox_output_invalid")
    payload = json.loads(content)
    findings = payload.get("findings") if isinstance(payload, Mapping) else None
    if not isinstance(findings, list):
        raise ValueError("sandbox_findings_invalid")
    return [dict(finding) for finding in findings if isinstance(finding, Mapping)]


def change_set_payload(change_set: Any) -> bytes:
    """重建最小 diff，并仅为可分析文件附带受控完整文本。"""

    files = getattr(change_set, "files", ())
    if not isinstance(files, tuple):
        raise ValueError("sandbox_change_set_invalid")
    lines: list[str] = []
    full_files: dict[str, str] = {}
    file_metadata: dict[str, dict[str, Any]] = {}
    for file_change in files:
        path = getattr(file_change, "normalized_path", "")
        hunks = getattr(file_change, "hunks", ())
        if not isinstance(path, str) or not path or not isinstance(hunks, tuple):
            raise ValueError("sandbox_change_set_invalid")
        full_text = getattr(file_change, "full_text", None)
        if full_text is not None:
            if not isinstance(full_text, str):
                raise ValueError("sandbox_change_set_invalid")
            full_files[path] = full_text
        status = getattr(file_change, "status", None)
        review_scope = getattr(file_change, "review_scope", None)
        analysis_mode = getattr(file_change, "analysis_mode", None)
        if not all(
            isinstance(value, str)
            for value in (status, review_scope, analysis_mode)
        ):
            raise ValueError("sandbox_change_set_invalid")
        file_metadata[path] = {
            "status": status,
            "review_scope": review_scope,
            "analysis_mode": analysis_mode,
            "is_binary": bool(getattr(file_change, "is_binary", False)),
        }
        lines.extend((f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"))
        for hunk in hunks:
            lines.append(
                f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            )
            old_line = hunk.old_start
            new_line = hunk.new_start
            while old_line < hunk.old_start + hunk.old_count or new_line < hunk.new_start + hunk.new_count:
                if old_line in hunk.deleted_lines:
                    lines.append("-" + hunk.deleted_lines[old_line])
                    old_line += 1
                elif new_line in hunk.added_lines:
                    lines.append("+" + hunk.added_lines[new_line])
                    new_line += 1
                else:
                    context = hunk.context_lines.get(new_line)
                    if context is None:
                        raise ValueError("sandbox_change_set_hunk_invalid")
                    lines.append(" " + context)
                    old_line += 1
                    new_line += 1
    source_kind = getattr(change_set, "source_kind", None)
    if source_kind not in {"diff_file", "repo_path", "fixture", "files"}:
        raise ValueError("sandbox_change_set_invalid")
    payload = {
        "source_kind": source_kind,
        "input_sha256": getattr(change_set, "input_sha256", ""),
        "diff": "\n".join(lines) + "\n",
        "full_files": full_files,
        "file_metadata": file_metadata,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
