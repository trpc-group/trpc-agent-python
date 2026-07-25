#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Skill loading and bounded execution through tRPC-Agent workspace runtimes."""

from __future__ import annotations

import atexit
import json
import time
from pathlib import Path
from typing import Any
from typing import AsyncGenerator
from typing import Optional

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DEFAULT_INPUTS_CONTAINER
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import SkillLoadTool
from trpc_agent_sdk.skills import SkillRunTool
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.telemetry import tracer

from .core import FilterDecision
from .core import SandboxRun
from .core import SecretRedactor
from .governance import ReviewExecutionFilter


class _ReviewToolAgent(BaseAgent):
    """Minimal agent identity for direct deterministic Skill tool calls."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        if False:
            yield Event(invocation_id=ctx.invocation_id, author=self.name)


class SandboxExecution(BaseModel):
    """Result of policy evaluation and an optional sandbox run."""

    decision: FilterDecision
    run: Optional[SandboxRun] = None
    raw_findings: list[dict[str, Any]] = Field(default_factory=list)
    redaction_count: int = 0
    tool_calls: int = 0


class SandboxExecutor:
    """Execute the approved scanner through ``skill_load`` and ``skill_run``."""

    def __init__(
        self,
        *,
        runtime: str,
        skill_root: Path,
        work_root: Path,
        allowed_scripts: set[str],
        env_allowlist: set[str],
        network_allowlist: set[str],
        max_timeout_seconds: int,
        max_output_bytes: int,
        max_policy_output_bytes: int,
    ) -> None:
        if runtime not in {"container", "local"}:
            raise ValueError("runtime must be 'container' or explicit development fallback 'local'")
        self.runtime = runtime
        self.skill_root = skill_root.expanduser().resolve()
        self.work_root = work_root.expanduser().resolve()
        self.allowed_scripts = allowed_scripts
        self.env_allowlist = env_allowlist
        self.network_allowlist = network_allowlist
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_policy_output_bytes = max_policy_output_bytes

    async def execute(
        self,
        *,
        diff_path: Path,
        task_id: str,
        checker_script: str,
        timeout_seconds: int,
        environment: Optional[dict[str, str]] = None,
        network_hosts: Optional[list[str]] = None,
    ) -> SandboxExecution:
        """Authorize first; create a runtime only after an allow decision."""

        command = self._command(checker_script)
        tool_args = {
            "skill": "code-review",
            "command": command,
            "cwd": "",
            "env": environment or {"PYTHONUNBUFFERED": "1"},
            "timeout": timeout_seconds,
            "inputs": [
                WorkspaceInputSpec(
                    src=f"host://{diff_path.expanduser().resolve()}",
                    dst="work/inputs/review.diff",
                    mode="copy",
                ).model_dump()
            ],
            "outputs": {
                "globs": ["out/review_findings.json"],
                "max_files": 1,
                "max_file_bytes": self.max_output_bytes,
                "max_total_bytes": self.max_output_bytes,
                "inline": True,
                "save": False,
            },
        }
        policy_request = {
            **tool_args,
            "network_hosts": network_hosts or [],
        }
        decisions: list[FilterDecision] = []
        policy_filter = ReviewExecutionFilter(
            allowed_scripts=self.allowed_scripts,
            env_allowlist=self.env_allowlist,
            network_allowlist=self.network_allowlist,
            max_timeout_seconds=self.max_timeout_seconds,
            max_output_bytes=self.max_policy_output_bytes,
            decision_sink=decisions.append,
        )

        async def approved() -> str:
            return "approved"

        await policy_filter.run(create_agent_context(), policy_request, approved)
        decision = decisions[-1]
        if decision.decision != "allow":
            return SandboxExecution(decision=decision)

        runtime: Optional[BaseWorkspaceRuntime] = None
        context: Optional[InvocationContext] = None
        started = time.monotonic()
        skill_loaded = False
        try:
            runtime = self._create_runtime(diff_path.parent)
            context = await self._create_context(task_id)
            repository = create_default_skill_repository(
                str(self.skill_root),
                workspace_runtime=runtime,
                enable_hot_reload=False,
                use_cached_repository=False,
            )
            load_tool = SkillLoadTool(repository=repository)
            run_tool = SkillRunTool(
                repository=repository,
                require_skill_loaded=True,
                allowed_cmds=["python3"],
            )

            with tracer.start_as_current_span("code_review.skill_load") as span:
                span.set_attribute("code_review.task_id", task_id)
                await load_tool.run_async(
                    tool_context=context,
                    args={
                        "skill_name": "code-review",
                        "include_all_docs": True,
                    },
                )
            skill_loaded = True

            with tracer.start_as_current_span("code_review.skill_run") as span:
                span.set_attribute("code_review.task_id", task_id)
                span.set_attribute("code_review.runtime", self.runtime)
                output = await run_tool.run_async(tool_context=context, args=tool_args)

            duration_ms = int((time.monotonic() - started) * 1000)
            run = self._sandbox_run(output, command, duration_ms, skill_loaded)
            raw_findings, output_redactions = self._read_findings(output)
            return SandboxExecution(
                decision=decision,
                run=run,
                raw_findings=raw_findings,
                redaction_count=output_redactions,
                tool_calls=2,
            )
        except Exception as error:  # pylint: disable=broad-except
            duration_ms = int((time.monotonic() - started) * 1000)
            clean_error, redactions = SecretRedactor.redact_text(str(error))
            run = SandboxRun(
                runtime=self.runtime,
                status="failed",
                command=command,
                duration_ms=duration_ms,
                exit_code=None,
                stderr=clean_error[:self.max_output_bytes],
                output_truncated=len(clean_error.encode("utf-8")) > self.max_output_bytes,
                error_type=type(error).__name__,
                skill_loaded=skill_loaded,
            )
            return SandboxExecution(
                decision=decision,
                run=run,
                redaction_count=redactions,
                tool_calls=2 if skill_loaded else 1,
            )
        finally:
            if runtime is not None and context is not None:
                await self._cleanup_workspace(runtime, context)
            if runtime is not None and self.runtime == "container":
                try:
                    cleanup = getattr(runtime.container, "_cleanup_container", None)
                    if cleanup:
                        atexit.unregister(cleanup)
                        cleanup()
                except Exception:  # pylint: disable=broad-except
                    pass

    async def _cleanup_workspace(
        self,
        runtime: BaseWorkspaceRuntime,
        context: InvocationContext,
    ) -> None:
        manager = runtime.manager(context)
        try:
            await manager.cleanup(context.session.id, context)
            return
        except PermissionError:
            if self.runtime != "local":
                return
        except Exception:  # pylint: disable=broad-except
            return

        workspace = getattr(manager, "ws_paths", {}).get(context.session.id)
        if workspace is None:
            return
        workspace_path = Path(workspace.path).resolve()
        if not workspace_path.is_relative_to(self.work_root):
            return
        for path in [workspace_path, *workspace_path.rglob("*")]:
            if path.is_symlink():
                continue
            try:
                path.chmod(path.stat().st_mode | (0o700 if path.is_dir() else 0o600))
            except FileNotFoundError:
                continue
        try:
            await manager.cleanup(context.session.id, context)
        except Exception:  # pylint: disable=broad-except
            pass

    def _create_runtime(self, inputs_host: Path) -> BaseWorkspaceRuntime:
        if self.runtime == "container":
            return create_container_workspace_runtime(
                host_config={
                    "network_mode": "none",
                    "auto_remove": True,
                    "Binds": [
                        f"{inputs_host.expanduser().resolve()}:{DEFAULT_INPUTS_CONTAINER}:ro",
                    ],
                },
                auto_inputs=True,
                enable_provider_env=False,
            )
        self.work_root.mkdir(parents=True, exist_ok=True)
        return create_local_workspace_runtime(
            work_root=str(self.work_root),
            read_only_staged_skill=False,
            auto_inputs=True,
            enable_provider_env=False,
        )

    @staticmethod
    async def _create_context(task_id: str) -> InvocationContext:
        service = InMemorySessionService()
        session = await service.create_session(
            app_name="skills_code_review_agent",
            user_id="review_user",
            session_id=task_id,
        )
        agent = _ReviewToolAgent(name="code_review_agent")
        return InvocationContext(
            session_service=service,
            invocation_id=task_id,
            agent=agent,
            agent_context=create_agent_context(),
            session=session,
        )

    @staticmethod
    def _command(checker_script: str) -> str:
        return f"python3 {checker_script}"

    def _sandbox_run(
        self,
        output: Any,
        command: str,
        duration_ms: int,
        skill_loaded: bool,
    ) -> SandboxRun:
        if not isinstance(output, dict):
            output = {}
        stdout, _ = SecretRedactor.redact_text(str(output.get("stdout") or ""))
        stderr, _ = SecretRedactor.redact_text(str(output.get("stderr") or ""))
        stdout, stdout_truncated = self._truncate(stdout)
        stderr, stderr_truncated = self._truncate(stderr)
        file_truncated = any(bool(item.get("truncated")) for item in output.get("output_files") or [])
        timed_out = bool(output.get("timed_out"))
        exit_code = output.get("exit_code")
        if timed_out:
            status = "timed_out"
            error_type = "TimeoutError"
        elif exit_code == 0:
            status = "completed"
            error_type = ""
        else:
            status = "failed"
            error_type = "SandboxCommandError"
        return SandboxRun(
            runtime=self.runtime,
            status=status,
            command=command,
            duration_ms=int(output.get("duration_ms") or duration_ms),
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            output_truncated=stdout_truncated or stderr_truncated or file_truncated,
            error_type=error_type,
            skill_loaded=skill_loaded,
        )

    def _read_findings(self, output: Any) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(output, dict):
            return [], 0
        files = output.get("output_files") or []
        for item in files:
            if item.get("name") != "out/review_findings.json":
                continue
            content = str(item.get("content") or "")
            clean_content, host_redactions = SecretRedactor.redact_text(content)
            try:
                payload = json.loads(clean_content)
            except json.JSONDecodeError:
                return [], host_redactions
            findings = payload.get("findings") if isinstance(payload, dict) else []
            scanner_redactions = int(payload.get("redaction_count") or 0)
            if isinstance(findings, list):
                return findings, host_redactions + scanner_redactions
        return [], 0

    def _truncate(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= self.max_output_bytes:
            return value, False
        return encoded[:self.max_output_bytes].decode("utf-8", errors="ignore"), True
