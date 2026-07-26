"""End-to-end Agent, Skill, Filter, sandbox, storage, and report pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
import stat
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import List
from typing_extensions import override

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.tools import CopySkillStager
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part

from .constants import APP_NAME
from .constants import CLEANUP_TIMEOUT_SECONDS
from .constants import MODEL_TIMEOUT_SECONDS
from .constants import TOTAL_TIMEOUT_SECONDS
from .models import DecisionAction
from .models import ReviewInput
from .models import ReviewMetrics
from .models import ReviewReport
from .models import SandboxStatus
from .models import TaskStatus
from .policy import ReviewPolicyFilter
from .policy import SecretRedactor
from .policy import build_execution_plan
from .policy import run_guarded
from .reporting import FindingBuckets
from .reporting import FindingOutputError
from .reporting import parse_findings_jsonl
from .reporting import prepare_findings
from .reporting import write_report_files
from .sandbox import RuntimeHandle
from .sandbox import SandboxExecution
from .sandbox import SandboxExecutor
from .storage import ReviewStore

MILLISECONDS_PER_SECOND = 1_000
FAKE_MODEL_NAME = "fake-code-review-model"
SKILL_WORKSPACE_LINK_NAMES = ("out", "work", "inputs")
REVIEW_INSTRUCTION = """
Review the staged input. Call skill_load for code-review first, then call
review_skill_run exactly once. Never call another tool. Summarize completion
without reproducing source code or credentials.
""".strip()

ToolAction = Callable[[], Awaitable[SandboxExecution | None]]


class FakeReviewModel(LLMModel):
    """Deterministic model that performs the same two tool calls."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [FAKE_MODEL_NAME]

    @override
    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx=None,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream, ctx
        responses = _function_response_names(request)
        if "skill_load" not in responses:
            call = FunctionCall(
                id="fake-load",
                name="skill_load",
                args={"skill_name": "code-review"},
            )
            yield LlmResponse(content=Content(role="model", parts=[Part(function_call=call)]))
            return
        if "review_skill_run" not in responses:
            call = FunctionCall(
                id="fake-run",
                name="review_skill_run",
                args={},
            )
            yield LlmResponse(content=Content(role="model", parts=[Part(function_call=call)]))
            return
        yield LlmResponse(content=Content(
            role="model",
            parts=[Part(text="Code review Skill completed.")],
        ), )

    def validate_request(self, request: LlmRequest) -> None:
        super().validate_request(request)


def _function_response_names(request: LlmRequest) -> set[str]:
    names = set()
    for content in request.contents:
        for part in content.parts or []:
            if part.function_response:
                names.add(part.function_response.name)
    return names


class _LoadOnlySkillToolSet(SkillToolSet):
    """Expose only skill_load; execution goes through the guarded tool."""

    @override
    async def get_tools(self, invocation_context=None):
        tools = await super().get_tools(invocation_context)
        return [tool for tool in tools if tool.name == "skill_load"]


@dataclass
class AgentRunResult:
    """Agent events relevant to pipeline metrics and recovery."""

    execution: SandboxExecution | None
    tool_names: list[str]
    failures: list[str]
    guard_blocked: bool


class ReviewAgentExecutor:
    """Run an LlmAgent with SkillToolSet and one guarded execution tool."""

    def __init__(self, dependencies: "AgentDependencies") -> None:
        self._model = dependencies.model
        self._skill_root = dependencies.skill_root
        self._runtime = dependencies.runtime
        self._action = dependencies.action
        self._execution: SandboxExecution | None = None
        self._skill_loaded = False
        self._skill_workspace_created = False
        self._guard_blocked = False

    async def review_skill_run(self) -> dict[str, Any]:
        """Execute the already-planned code-review Skill."""
        if not self._skill_loaded:
            self._guard_blocked = True
            return {"status": "blocked", "reason": "skill_load is required first"}
        if self._execution is None:
            self._execution = await self._action()
        if self._execution is None:
            return {"status": "blocked"}
        return {
            "status": self._execution.run.status.value,
            "finding_output": self._execution.output,
        }

    def _create_agent(self) -> LlmAgent:
        repository = create_default_skill_repository(
            str(self._skill_root),
            workspace_runtime=self._runtime.require_runtime(),
        )
        skill_tools = _LoadOnlySkillToolSet(
            repository=repository,
            skill_stager=CopySkillStager(),
            create_ws_name_cb=self._skill_workspace_name,
        )

        def after_tool_callback(context, tool, args, response):
            del context, args
            if tool.name == "skill_load" and _tool_succeeded(response):
                self._skill_loaded = True

        return LlmAgent(
            name="skills_code_review_agent",
            description="Policy-gated code review Agent.",
            model=self._model,
            instruction=REVIEW_INSTRUCTION,
            tools=[skill_tools, FunctionTool(func=self.review_skill_run)],
            skill_repository=repository,
            after_tool_callback=after_tool_callback,
        )

    def _skill_workspace_name(self, context) -> str:
        self._skill_workspace_created = True
        return context.session.id

    async def run(self, task_id: str) -> AgentRunResult:
        """Run one isolated Agent session with a hard model timeout."""
        agent = self._create_agent()
        runner = Runner(
            app_name=APP_NAME,
            agent=agent,
            session_service=InMemorySessionService(),
        )
        tool_names: list[str] = []
        failures: list[str] = []
        try:
            await asyncio.wait_for(
                self._consume(runner, task_id, tool_names, failures),
                timeout=MODEL_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # pylint: disable=broad-except
            failures.append(type(exc).__name__)
        finally:
            await self._cleanup(runner, task_id, failures)
        return AgentRunResult(
            execution=self._execution,
            tool_names=tool_names,
            failures=failures,
            guard_blocked=self._guard_blocked,
        )

    async def _cleanup(
        self,
        runner: Runner,
        task_id: str,
        failures: list[str],
    ) -> None:
        actions = [("RunnerCloseError", runner.close)]
        if self._skill_workspace_created:
            actions.append((
                "SkillWorkspaceCleanupError",
                lambda: self._cleanup_skill_workspace(task_id),
            ))
        for label, action in actions:
            try:
                await asyncio.wait_for(action(), timeout=CLEANUP_TIMEOUT_SECONDS)
            except Exception as exc:  # pylint: disable=broad-except
                failures.append(f"{label}:{type(exc).__name__}")

    async def _cleanup_skill_workspace(self, task_id: str) -> None:
        manager = self._runtime.require_runtime().manager()
        if self._runtime.kind == "local":
            workspace = await manager.create_workspace(task_id)
            _make_workspace_removable(Path(workspace.path))
        await manager.cleanup(task_id)

    @staticmethod
    async def _consume(
        runner: Runner,
        task_id: str,
        tool_names: list[str],
        failures: list[str],
    ) -> None:
        message = Content(parts=[Part(text="Run the code-review Skill for the prepared input.")])
        async for event in runner.run_async(
                user_id="code-review-user",
                session_id=task_id,
                new_message=message,
        ):
            if event.error_code or event.error_message:
                failures.append(event.error_code or "AgentError")
            for part in event.content.parts if event.content else []:
                if part.function_call:
                    tool_names.append(part.function_call.name)


@dataclass(frozen=True)
class PipelineDependencies:
    """Services and paths needed by the pipeline."""

    store: ReviewStore
    runtime: RuntimeHandle
    skill_root: Path
    output_dir: Path
    model: LLMModel


@dataclass(frozen=True)
class AgentDependencies:
    """Dependencies used to construct one Agent session."""

    model: LLMModel
    skill_root: Path
    runtime: RuntimeHandle
    action: ToolAction


@dataclass
class _PipelineState:
    task_id: str
    started: float
    decisions: list = field(default_factory=list)
    execution: SandboxExecution | None = None
    tool_names: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    attempted: bool = False


class ReviewPipeline:
    """Coordinate one durable code-review task."""

    def __init__(self, dependencies: PipelineDependencies) -> None:
        self._deps = dependencies
        self._redactor = SecretRedactor()

    async def run(
        self,
        review_input: ReviewInput,
        dry_run: bool = False,
    ) -> tuple[ReviewReport, Path, Path]:
        """Run one task and persist every auditable phase."""
        state = _PipelineState(task_id=uuid.uuid4().hex, started=time.monotonic())
        await self._deps.store.create_task(state.task_id, review_input)
        try:
            return await asyncio.wait_for(
                self._run_task(state, review_input, dry_run),
                timeout=TOTAL_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            failure = self._redactor.redact_text(type(exc).__name__)
            await self._deps.store.update_status(
                state.task_id,
                TaskStatus.FAILED,
                failure,
            )
            raise

    async def _run_task(
        self,
        state: _PipelineState,
        review_input: ReviewInput,
        dry_run: bool,
    ) -> tuple[ReviewReport, Path, Path]:
        input_bytes = self._serialize_input(review_input)
        executor = SandboxExecutor(
            self._deps.runtime,
            self._redactor,
            self._deps.skill_root / "code-review",
        )
        plan = build_execution_plan(
            self._deps.runtime.kind,
            hashlib.sha256(input_bytes).hexdigest(),
            executor.skill_digest,
        )

        async def audit(decisions):
            state.decisions = decisions
            await self._deps.store.save_filter_decisions(state.task_id, decisions)
            await self._deps.store.update_status(state.task_id, TaskStatus.FILTERED)

        async def execute_approved(approved):
            await self._deps.store.update_status(state.task_id, TaskStatus.RUNNING)
            return await executor.execute(approved, input_bytes)

        policy = ReviewPolicyFilter(audit)

        async def tool_action() -> SandboxExecution | None:
            if state.attempted:
                return state.execution
            state.attempted = True
            state.execution, _ = await run_guarded(
                plan,
                policy,
                execute_approved,
            )
            return state.execution

        if dry_run:
            await policy.evaluate_and_audit(plan)
        else:
            await self._run_agent(state, tool_action)
        report = await self._finalize(state, review_input, dry_run)
        json_path, markdown_path, markdown = write_report_files(
            report,
            self._deps.output_dir,
        )
        await self._deps.store.complete_task(report, markdown)
        return report, json_path, markdown_path

    @staticmethod
    def _serialize_input(review_input: ReviewInput) -> bytes:
        return json.dumps(
            review_input.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def _run_agent(
        self,
        state: _PipelineState,
        tool_action: ToolAction,
    ) -> None:
        agent = ReviewAgentExecutor(
            AgentDependencies(
                model=self._deps.model,
                skill_root=self._deps.skill_root,
                runtime=self._deps.runtime,
                action=tool_action,
            ), )
        result = await agent.run(state.task_id)
        state.execution = result.execution
        state.tool_names = result.tool_names
        state.failures.extend(result.failures)
        if result.guard_blocked:
            state.failures.append("SkillLoadRequired")
        if state.execution and "skill_load" not in state.tool_names:
            state.failures.append("SkillNotLoadedByAgent")
        if state.execution is None and "review_skill_run" not in state.tool_names:
            state.failures.append("AgentDidNotRunSkill")
            state.execution = await tool_action()

    async def _finalize(
        self,
        state: _PipelineState,
        review_input: ReviewInput,
        dry_run: bool,
    ) -> ReviewReport:
        buckets = FindingBuckets([], [], [])
        if state.execution:
            await self._deps.store.save_sandbox_run(state.task_id, state.execution.run)
            buckets = self._parse_execution(state)
            await self._deps.store.save_findings(
                state.task_id,
                buckets.all_findings,
                buckets.human_fingerprints,
            )
        status = self._status(state, dry_run)
        metrics = self._metrics(state, buckets)
        conclusion = self._conclusion(status, buckets)
        return ReviewReport(
            task_id=state.task_id,
            status=status,
            input_summary={
                "kind": review_input.kind.value,
                "digest": review_input.digest,
                "file_count": len(review_input.files),
            },
            findings=buckets.actionable,
            warnings=buckets.warnings,
            needs_human_review=buckets.needs_human_review,
            filter_decisions=state.decisions,
            sandbox_runs=[state.execution.run] if state.execution else [],
            failures=[self._redactor.redact_text(item) for item in state.failures],
            metrics=metrics,
            conclusion=conclusion,
            created_at=datetime_now(),
        )

    def _parse_execution(self, state: _PipelineState) -> FindingBuckets:
        execution = state.execution
        if execution is None or execution.run.status != SandboxStatus.SUCCEEDED:
            return FindingBuckets([], [], [])
        try:
            findings = parse_findings_jsonl(execution.output, self._redactor)
            return prepare_findings(findings, self._redactor)
        except FindingOutputError as exc:
            state.failures.append(type(exc).__name__)
            return FindingBuckets([], [], [])

    @staticmethod
    def _status(state: _PipelineState, dry_run: bool) -> TaskStatus:
        blocked = any(item.action != DecisionAction.ALLOW for item in state.decisions)
        if blocked:
            return TaskStatus.DENIED
        if dry_run:
            return TaskStatus.COMPLETE
        if state.execution is None or state.execution.run.status != SandboxStatus.SUCCEEDED:
            return TaskStatus.FAILED
        if "FindingOutputError" in state.failures:
            return TaskStatus.FAILED
        if state.failures:
            return TaskStatus.PARTIAL
        return TaskStatus.COMPLETE

    @staticmethod
    def _metrics(state: _PipelineState, buckets: FindingBuckets) -> ReviewMetrics:
        execution = state.execution
        findings = buckets.all_findings
        exceptions = list(state.failures)
        if execution and execution.run.error_type:
            exceptions.append(execution.run.error_type)
        filter_actions: dict[str, int] = {}
        for decision in state.decisions:
            name = decision.action.value
            filter_actions[name] = filter_actions.get(name, 0) + 1
        return ReviewMetrics(
            total_duration_ms=int((time.monotonic() - state.started) * MILLISECONDS_PER_SECOND),
            sandbox_duration_ms=execution.run.duration_ms if execution else 0,
            tool_calls=len(state.tool_names),
            blocked_count=sum(count for action, count in filter_actions.items()
                              if action != DecisionAction.ALLOW.value),
            finding_count=len(findings),
            filter_actions=filter_actions,
            findings_by_severity=_count_values(item.severity.value for item in findings),
            findings_by_category=_count_values(item.category.value for item in findings),
            exceptions_by_type=_count_values(exceptions),
        )

    @staticmethod
    def _conclusion(status: TaskStatus, buckets: FindingBuckets) -> str:
        if status == TaskStatus.DENIED:
            return "Execution denied by policy; no sandbox command ran."
        if status == TaskStatus.FAILED:
            return "Review failed; inspect sandbox and exception records."
        if not buckets.all_findings:
            return "No review findings."
        return (f"{len(buckets.actionable)} actionable findings and "
                f"{len(buckets.warnings)} warnings.")


def _count_values(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _tool_succeeded(response: Any) -> bool:
    if response is None:
        return False
    if isinstance(response, dict):
        return not response.get("error")
    return True


def _make_workspace_removable(root: Path) -> None:
    skill_dir = root / "skills" / "code-review"
    for path in [root, *root.rglob("*")]:
        if _is_workspace_link(path):
            continue
        try:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        except FileNotFoundError:
            continue
    for name in SKILL_WORKSPACE_LINK_NAMES:
        target = skill_dir / name
        if os.path.lexists(target) and _is_workspace_link(target):
            _remove_workspace_link(target)


def _is_workspace_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _remove_workspace_link(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()


def datetime_now() -> datetime:
    """Return a timezone-aware report timestamp."""
    return datetime.now(timezone.utc)
