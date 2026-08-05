"""Workflow orchestration for one code review run."""

import asyncio
import hashlib
import json
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from agent.agent import OUTPUT_KEY
from agent.agent import create_review_agent
from agent.config import ModelConfig
from agent.config import ReviewLimits
from agent.fake import analyze_with_fake_model
from agent.normalization import normalize_analysis
from agent.normalization import enforce_analysis_scope
from agent.prompts import build_review_request
from filters.policy import SandboxCommand
from filters.policy import ReviewPolicyContext
from filters.sdk_filter import FILTER_DECISIONS_METADATA_KEY
from inputs.models import ParsedReviewInput
from inputs.parser import parse_diff_file
from inputs.parser import parse_file_list
from inputs.parser import parse_fixture
from inputs.parser import parse_git_worktree
from inputs.parser import cleanup_parsed_input
from reports.models import FilterDecision
from reports.models import MonitoringSummary
from reports.models import ReviewAnalysis
from reports.models import ReviewFinding
from reports.models import ReviewInputSummary
from reports.models import ReviewReport
from reports.models import ReviewScope
from reports.models import SandboxRun
from reports.writers import ReportArtifacts
from reports.writers import ReportWriter
from sandbox.base import SandboxProvider
from sandbox.fake import FakeSandbox
from security import redact_text
from storage.base import BaseReviewStore

REVIEW_PROFILE_VERSION = "2"


@dataclass(frozen=True)
class ReviewRequest:
    """Input and execution mode accepted by the review workflow."""

    repository_path: Path | None = None
    diff_file: Path | None = None
    file_list: Path | None = None
    fixture: str | None = None
    scope: ReviewScope = ReviewScope.CHANGED
    fake_model: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class ReviewWorkflowResult:
    """Completed report and its rendered artifacts."""

    report: ReviewReport
    artifacts: ReportArtifacts


class AgentExecutionFailure(RuntimeError):
    """Carry partial Agent audit data when a model or tool turn aborts."""

    def __init__(
        self,
        cause: Exception,
        decisions: list[FilterDecision],
        runs: list[SandboxRun],
        tool_calls: int,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.decisions = decisions
        self.runs = runs
        self.tool_calls = tool_calls


class CodeReviewWorkflow:
    """Coordinate trusted lifecycle steps while the Agent decides checks."""

    def __init__(
        self,
        model_config: ModelConfig | None,
        sandbox: SandboxProvider | None,
        store: BaseReviewStore,
        report_writer: ReportWriter,
        skills_path: Path,
        limits: ReviewLimits | None = None,
    ) -> None:
        self.model_config = model_config
        self.sandbox = sandbox
        self.store = store
        self.report_writer = report_writer
        self.skills_path = skills_path
        self.limits = limits or ReviewLimits()

    async def run(self, request: ReviewRequest) -> ReviewWorkflowResult:
        """Execute a review without letting sandbox failures suppress reports."""
        started = time.perf_counter()
        created_at = datetime.now(timezone.utc)
        task_id = str(uuid.uuid4())
        self.store.initialize()
        repository = self._request_repository_label(request)
        self.store.start_task(task_id, created_at, repository, request.scope)
        parsed_input = None
        try:
            parsed_input = self._parse_input(request)
            parsed_input.summary.review_profile = self._review_profile(request)
            return await self._run_parsed(
                request,
                parsed_input,
                started,
                created_at,
                task_id,
                repository,
            )
        except BaseException as error:
            try:
                self.store.mark_task_failed(
                    task_id,
                    datetime.now(timezone.utc),
                    f"{type(error).__name__}: {redact_text(str(error))}"[:4000],
                )
            except Exception:
                pass
            raise
        finally:
            if parsed_input is not None:
                cleanup_parsed_input(parsed_input)

    @staticmethod
    def _request_repository_label(request: ReviewRequest) -> str:
        """Build a redacted audit label before input parsing can fail."""
        if request.repository_path is not None:
            value = str(request.repository_path.resolve())
        elif request.diff_file is not None:
            value = request.diff_file.name
        elif request.file_list is not None:
            value = request.file_list.name
        elif request.fixture is not None:
            value = request.fixture
        else:
            value = "input"
        return redact_text(value)[:2048]

    async def _run_parsed(
        self,
        request: ReviewRequest,
        parsed_input: ParsedReviewInput,
        started: float,
        created_at: datetime,
        task_id: str,
        repository: str,
    ) -> ReviewWorkflowResult:
        """Run the task after its durable running row has been created."""
        # Cached evidence is advisory; the real Agent still decides whether to reuse it.
        cached_report = self._find_cached_report(parsed_input)
        parsed_input.exact_cache_available = cached_report is not None
        parsed_input.review_scope = request.scope.value
        fatal_failure = False

        if request.fake_model or request.dry_run:
            analysis, decisions, runs, tool_calls = self._run_fake(parsed_input)
        else:
            try:
                analysis, decisions, runs, tool_calls = await asyncio.wait_for(
                    self._run_agent(
                        request,
                        parsed_input,
                        task_id,
                        cached_report,
                    ),
                    timeout=self.limits.timeout_seconds,
                )
            except Exception as error:  # Keep failures auditable instead of dropping the task.
                fatal_failure = True
                cause = error.cause if isinstance(error, AgentExecutionFailure) else error
                error_text = redact_text(str(cause))[:4000]
                analysis = ReviewAnalysis(
                    summary="Review execution failed and requires human follow-up.",
                    needs_human_review=[
                        ReviewFinding(
                            severity="high",
                            category="review_execution",
                            file="input",
                            line=None,
                            title="Review execution failed",
                            evidence=error_text,
                            recommendation="Inspect the recorded error and rerun safely.",
                            confidence=1.0,
                            source="workflow",
                        )
                    ],
                )
                if isinstance(error, AgentExecutionFailure):
                    decisions = error.decisions
                    runs = self._apply_filter_decisions(
                        list(error.runs),
                        decisions,
                    )
                    tool_calls = error.tool_calls
                else:
                    decisions = []
                    runs = []
                    tool_calls = 0
                runs.append(
                    SandboxRun(
                        run_id=str(uuid.uuid4()),
                        command="sandbox initialization or Agent execution",
                        status="failed",
                        stderr_summary=error_text,
                        error_type=type(cause).__name__,
                    )
                )

        analysis = self._append_execution_limitations(
            analysis,
            parsed_input,
            decisions,
            runs,
            require_complete_execution=not (
                request.fake_model or request.dry_run or fatal_failure
            ),
        )
        # Model output must remain tied to the selected files and changed lines.
        if not (request.fake_model or request.dry_run or fatal_failure):
            analysis = enforce_analysis_scope(analysis, parsed_input)
        # Normalize and redact before data crosses persistence or report boundaries.
        analysis = normalize_analysis(analysis)
        input_summary = self._redact_input_summary(parsed_input.summary)
        decisions = [
            decision.model_copy(
                update={
                    "command": redact_text(decision.command),
                    "reason": redact_text(decision.reason),
                }
            )
            for decision in decisions
        ]
        runs = [
            run.model_copy(
                update={
                    "command": redact_text(run.command),
                    "stdout_summary": redact_text(run.stdout_summary),
                    "stderr_summary": redact_text(run.stderr_summary),
                    "error_type": redact_text(run.error_type) if run.error_type else None,
                }
            )
            for run in runs
        ]
        monitoring = self._build_monitoring(
            started,
            analysis,
            decisions,
            runs,
            tool_calls,
        )
        has_warnings = bool(
            analysis.warnings
            or analysis.needs_human_review
            or any(
                run.status not in {"success", "simulated"}
                or run.output_truncated
                for run in runs
            )
        )
        if fatal_failure:
            status = "failed"
        elif has_warnings:
            status = "completed_with_warnings"
        else:
            status = "completed"
        report = ReviewReport(
            task_id=task_id,
            created_at=created_at,
            completed_at=datetime.now(timezone.utc),
            status=status,
            repository=repository,
            scope=request.scope,
            input_summary=input_summary,
            analysis=analysis,
            filter_decisions=decisions,
            sandbox_runs=runs,
            monitoring=monitoring,
            conclusion=analysis.summary,
        )
        artifacts = self.report_writer.write(report)
        try:
            self.store.save(report)
        except Exception as error:
            failed_report = report.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc),
                    "conclusion": (
                        "Persistence failed: "
                        f"{type(error).__name__}: {redact_text(str(error))}"
                    )[:4000],
                }
            )
            try:
                self.report_writer.write(failed_report)
            except Exception:
                pass
            raise
        return ReviewWorkflowResult(report=report, artifacts=artifacts)

    def _review_profile(self, request: ReviewRequest) -> str:
        """Version cache entries by behavior, rules, model, mode, and scope."""
        digest = hashlib.sha256()
        values = [
            REVIEW_PROFILE_VERSION,
            request.scope.value,
            "fake" if request.fake_model or request.dry_run else "real",
            self.model_config.model_name if self.model_config else "no-model",
            self.model_config.base_url if self.model_config else "no-provider",
        ]
        for value in values:
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        example_root = Path(__file__).resolve().parent
        profile_roots = [
            example_root / "agent",
            example_root / "filters",
            example_root / "inputs",
            example_root / "reports",
            example_root / "sandbox",
            self.skills_path / "code-review",
        ]
        profile_files = {
            path
            for root in profile_roots
            for path in root.rglob("*")
            if path.is_file()
            and (
                path.suffix in {".py", ".md", ".sql"}
                or path.name == "Dockerfile"
            )
        }
        profile_files.update({example_root / "security.py", example_root / "workflow.py"})
        for path in sorted(profile_files):
            digest.update(path.relative_to(example_root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _redact_input_summary(summary: ReviewInputSummary) -> ReviewInputSummary:
        return summary.model_copy(
            update={
                "source": redact_text(summary.source),
                "files": [redact_text(item) for item in summary.files],
                "redacted_preview": redact_text(summary.redacted_preview),
            }
        )

    @staticmethod
    def _append_execution_limitations(
        analysis: ReviewAnalysis,
        parsed_input: ParsedReviewInput,
        decisions: list[FilterDecision],
        runs: list[SandboxRun],
        *,
        require_complete_execution: bool = False,
    ) -> ReviewAnalysis:
        """Add a trusted human-review item for incomplete execution evidence."""
        limitations = []
        if parsed_input.input_changed_during_review:
            limitations.append("the Git input changed between paginated reads")
        if parsed_input.input_evidence_incomplete:
            limitations.append("a Git path could not be represented safely")
        if any(run.timed_out or run.status == "timeout" for run in runs):
            limitations.append("a sandbox command timed out")
        if any(run.status == "failed" for run in runs):
            limitations.append("a sandbox command failed")
        if any(run.output_truncated for run in runs):
            limitations.append("sandbox output was truncated")
        if any(decision.decision != "allow" for decision in decisions):
            limitations.append("a Filter decision blocked execution")
        if require_complete_execution:
            limitations.extend(
                CodeReviewWorkflow._execution_completeness_issues(
                    parsed_input,
                    runs,
                )
            )
        if not limitations:
            return analysis
        if any(
            item.category == "review_execution_limitation"
            for item in analysis.needs_human_review
        ):
            return analysis
        finding = ReviewFinding(
            severity="medium",
            category="review_execution_limitation",
            file="input",
            line=None,
            title="Review execution produced incomplete evidence",
            evidence="; ".join(dict.fromkeys(limitations)),
            recommendation=(
                "Stabilize the input, resolve the recorded execution issue, and rerun."
            ),
            confidence=1.0,
            source="workflow",
        )
        return analysis.model_copy(
            update={
                "summary": (
                    f"{analysis.summary} Execution evidence was incomplete; "
                    "see needs_human_review."
                )[:4000],
                "needs_human_review": [
                    *analysis.needs_human_review,
                    finding,
                ]
            }
        )

    @staticmethod
    def _execution_completeness_issues(
        parsed_input: ParsedReviewInput,
        runs: list[SandboxRun],
    ) -> list[str]:
        """Return trusted gaps that prevent claiming a complete review."""
        successful_runs = [run for run in runs if run.status == "success"]
        if not successful_runs and parsed_input.exact_cache_available:
            return []

        kind = parsed_input.summary.kind
        if kind in {"diff_file", "fixture"}:
            required = {"rules"}
        elif kind == "file_list":
            required = {"file-list", "inspect:file-list"}
        elif kind == "git_worktree":
            required = (
                {"files:tracked"}
                if parsed_input.review_scope == "full"
                else {"files:changed", "diff:unstaged", "diff:staged"}
            )
        else:
            required = set()

        observed = set(parsed_input.pagination_next_cursors)
        missing = sorted(required - observed)
        pending = sorted(
            key
            for key, cursor in parsed_input.pagination_next_cursors.items()
            if cursor is not None
        )
        issues = []
        if missing:
            issues.append("required sandbox evidence was not collected: " + ", ".join(missing))
        if pending:
            issues.append("sandbox pagination did not finish: " + ", ".join(pending))

        if kind == "git_worktree":
            if "files:tracked" in observed:
                unread = set(parsed_input.summary.files) - parsed_input.inspected_files
            else:
                unread = parsed_input.untracked_files - parsed_input.inspected_files
            if unread:
                issues.append(
                    f"{len(unread)} selected file(s) were not inspected "
                    "through the controlled reader"
                )
        return issues

    @staticmethod
    def _parse_input(request: ReviewRequest) -> ParsedReviewInput:
        if request.scope is ReviewScope.FULL and (
            request.repository_path is None
            or request.diff_file is not None
            or request.file_list is not None
            or request.fixture is not None
        ):
            raise ValueError("Full review requires only --repo-path")
        if request.file_list is not None:
            if request.diff_file is not None or request.fixture is not None:
                raise ValueError("File list cannot be combined with diff or fixture input")
            if (
                request.repository_path is None
                and not request.fake_model
                and not request.dry_run
            ):
                raise ValueError("Real file-list review also requires --repo-path")
            return parse_file_list(request.file_list, request.repository_path)

        selected = sum(
            value is not None
            for value in (
                request.repository_path,
                request.diff_file,
                request.fixture,
            )
        )
        if selected != 1:
            raise ValueError("Select exactly one review input")
        if request.diff_file is not None:
            return parse_diff_file(request.diff_file)
        if request.fixture is not None:
            return parse_fixture(request.fixture)
        if request.repository_path is None:
            raise ValueError("Repository path is required")
        return parse_git_worktree(request.repository_path)

    @staticmethod
    def _sandbox_request(parsed_input: ParsedReviewInput) -> SandboxCommand:
        if parsed_input.summary.kind == "git_worktree":
            command = (
                "python3 scripts/review_git_changes.py "
                "work/inputs --mode unstaged"
            )
        elif parsed_input.summary.kind in {"diff_file", "fixture"}:
            filename = Path(parsed_input.summary.source).name
            if parsed_input.summary.kind == "fixture":
                filename = f"{parsed_input.summary.source}.diff"
            command = f"python3 scripts/run_review_rules.py work/inputs/{filename}"
        else:
            command = (
                "python3 scripts/inspect_file_list.py "
                f"work/inputs/{parsed_input.summary.source}"
            )
        return SandboxCommand(command=command)

    def _run_fake(
        self,
        parsed_input: ParsedReviewInput,
    ) -> tuple[ReviewAnalysis, list[FilterDecision], list[SandboxRun], int]:
        decision, run = FakeSandbox().run(
            self._sandbox_request(parsed_input),
            parsed_input,
        )
        if run.status in {"failed", "timeout", "blocked"}:
            analysis = ReviewAnalysis(
                summary="Review completed with a sandbox execution warning.",
                needs_human_review=[
                    ReviewFinding(
                        severity="medium",
                        category="sandbox_execution",
                        file=parsed_input.summary.files[0]
                        if parsed_input.summary.files
                        else "input",
                        line=None,
                        title="Sandbox check did not complete",
                        evidence=redact_text(run.stderr_summary),
                        recommendation="Inspect the sandbox failure and rerun safely.",
                        confidence=1.0,
                        source="sandbox-runtime",
                    )
                ],
                checks_performed=["input parsing", "sandbox policy evaluation"],
            )
        else:
            analysis = analyze_with_fake_model(parsed_input)
        return analysis, [decision], [run], 1

    async def _run_agent(
        self,
        request: ReviewRequest,
        parsed_input: ParsedReviewInput,
        session_id: str,
        cached_report: ReviewReport | None,
    ) -> tuple[ReviewAnalysis, list[FilterDecision], list[SandboxRun], int]:
        if self.model_config is None or self.sandbox is None:
            raise ValueError("Real mode requires model and Docker sandbox configuration")
        review_agent = create_review_agent(
            self.model_config,
            self.sandbox,
            parsed_input.input_root,
            self.skills_path,
            ReviewPolicyContext(
                input_kind=parsed_input.summary.kind,
                source=parsed_input.summary.source,
                scope=request.scope.value,
            ),
        )
        session_service = InMemorySessionService()
        runner = Runner(
            app_name="skills_code_review_agent",
            agent=review_agent,
            session_service=session_service,
        )
        user_id = "code-review-user"
        await session_service.create_session(
            app_name="skills_code_review_agent",
            user_id=user_id,
            session_id=session_id,
        )
        message = Content(
            parts=[
                Part.from_text(
                    text=build_review_request(
                        request.scope,
                        parsed_input.summary,
                        cached_report,
                    )
                )
            ],
        )
        agent_context = new_agent_context()
        tool_calls = 0
        pending_runs: dict[str, tuple[str, float]] = {}
        sandbox_runs: list[SandboxRun] = []

        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=message,
                agent_context=agent_context,
            ):
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if part.function_call:
                        tool_calls += 1
                        if tool_calls > self.limits.max_tool_calls:
                            raise RuntimeError("review tool-call budget exhausted")
                        if part.function_call.name == "skill_run":
                            # Pair asynchronous call/response events to measure each run.
                            call_id = part.function_call.id or str(uuid.uuid4())
                            command = str(
                                (part.function_call.args or {}).get("command", "")
                            )[:4096]
                            pending_runs[call_id] = (command, time.perf_counter())
                    elif part.function_response:
                        pending = pending_runs.pop(part.function_response.id, None)
                        if pending is not None:
                            response = part.function_response.response
                            run = self._sandbox_run_from_response(pending, response)
                            sandbox_runs.append(run)
                            self._update_runtime_input(
                                parsed_input,
                                pending[0],
                                response,
                            )
            session = await session_service.get_session(
                app_name="skills_code_review_agent",
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as error:
            for command, run_started in pending_runs.values():
                sandbox_runs.append(
                    SandboxRun(
                        run_id=str(uuid.uuid4()),
                        command=command,
                        status="failed",
                        duration_ms=(time.perf_counter() - run_started) * 1000,
                        stderr_summary="Agent execution stopped before the tool returned.",
                        error_type="AgentInterrupted",
                    )
                )
            decisions = self._filter_decisions(agent_context)
            sandbox_runs = self._apply_filter_decisions(sandbox_runs, decisions)
            raise AgentExecutionFailure(
                error,
                decisions,
                sandbox_runs,
                tool_calls,
            ) from error
        finally:
            await runner.close()

        if session is None or not session.state or OUTPUT_KEY not in session.state:
            analysis = ReviewAnalysis(
                summary="The review agent did not produce a structured result.",
                needs_human_review=[
                    ReviewFinding(
                        severity="high",
                        category="agent_failure",
                        file="input",
                        line=None,
                        title="Structured Agent result is missing",
                        evidence="The Agent run ended without review_analysis state.",
                        recommendation="Inspect Agent and model logs, then rerun.",
                        confidence=1.0,
                        source="workflow",
                    )
                ],
            )
        else:
            raw_analysis = session.state[OUTPUT_KEY]
            if isinstance(raw_analysis, str):
                analysis = ReviewAnalysis.model_validate_json(raw_analysis)
            else:
                analysis = ReviewAnalysis.model_validate(raw_analysis)

        decisions = self._filter_decisions(agent_context)
        # A rejected tool call is represented as an auditable blocked sandbox run.
        sandbox_runs = self._apply_filter_decisions(sandbox_runs, decisions)
        return analysis, decisions, sandbox_runs, tool_calls

    @staticmethod
    def _filter_decisions(agent_context) -> list[FilterDecision]:
        return [
            FilterDecision.model_validate(item)
            for item in agent_context.get_metadata(FILTER_DECISIONS_METADATA_KEY, [])
        ]

    @staticmethod
    def _sandbox_run_from_response(
        pending: tuple[str, float],
        response: object,
    ) -> SandboxRun:
        command, run_started = pending
        response_data = response if isinstance(response, dict) else {}
        exit_code = response_data.get("exit_code")
        timed_out = bool(response_data.get("timed_out", False))
        warnings = response_data.get("warnings") or []
        response_failed = bool(response_data.get("error")) or (
            response_data.get("status") == "failed"
        )
        if timed_out:
            status = "timeout"
        elif response_failed or exit_code not in {None, 0}:
            status = "failed"
        else:
            status = "success"
        stdout = str(response_data.get("stdout", ""))
        stderr = str(
            response_data.get("stderr") or response_data.get("message") or ""
        )
        output_truncated = (
            "output truncated by sandbox policy" in stdout
            or "output truncated by sandbox policy" in stderr
            or any("truncat" in str(item).lower() for item in warnings)
        )
        return SandboxRun(
            run_id=str(uuid.uuid4()),
            command=command,
            status=status,
            duration_ms=float(
                response_data.get("duration_ms")
                or (time.perf_counter() - run_started) * 1000
            ),
            exit_code=exit_code,
            timed_out=timed_out,
            output_truncated=output_truncated,
            stdout_summary=redact_text(stdout)[:2000],
            stderr_summary=redact_text(stderr)[:2000],
            error_type=(
                "TimeoutError"
                if timed_out
                else str(response_data.get("error") or "SandboxExecutionError")
                if response_failed or exit_code not in {None, 0}
                else None
            ),
        )

    @staticmethod
    def _apply_filter_decisions(
        runs: list[SandboxRun],
        decisions: list[FilterDecision],
    ) -> list[SandboxRun]:
        """Mark denied tool attempts as blocked rather than sandbox failures."""
        blocked = {
            decision.command: decision
            for decision in decisions
            if decision.decision != "allow"
        }
        normalized = []
        for run in runs:
            decision = blocked.get(run.command)
            if decision is None:
                normalized.append(run)
                continue
            normalized.append(
                run.model_copy(
                    update={
                        "status": "blocked",
                        "exit_code": None,
                        "stderr_summary": redact_text(decision.reason),
                        "error_type": "FilterBlocked",
                    }
                )
            )
        recorded_blocked = {
            run.command for run in normalized if run.status == "blocked"
        }
        for decision in decisions:
            if (
                decision.decision != "allow"
                and decision.command not in recorded_blocked
            ):
                normalized.append(
                    SandboxRun(
                        run_id=str(uuid.uuid4()),
                        command=decision.command,
                        status="blocked",
                        stderr_summary=redact_text(decision.reason),
                        error_type="FilterBlocked",
                    )
                )
                recorded_blocked.add(decision.command)
        return normalized

    @staticmethod
    def _update_runtime_input(
        parsed_input: ParsedReviewInput,
        command: str,
        response: object,
    ) -> None:
        response_data = response if isinstance(response, dict) else {}
        stdout = str(response_data.get("stdout", ""))
        if response_data.get("error") or response_data.get("status") == "failed":
            return
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            CodeReviewWorkflow._observe_pagination(
                parsed_input,
                command,
                payload,
            )
        if parsed_input.summary.kind != "git_worktree":
            return
        is_structured_git_diff = "scripts/review_git_changes.py" in command
        is_git_file_list = "scripts/inspect_git_files.py" in command
        is_file_inspection = "scripts/inspect_files.py" in command
        if not any(
            (
                is_structured_git_diff,
                is_git_file_list,
                is_file_inspection,
            )
        ):
            return
        evidence = "\0".join((parsed_input.summary.digest, command, stdout))
        evidence_digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        existing_files = list(parsed_input.summary.files)

        def observe_git_digest(kind: str, payload: dict[str, object]) -> None:
            mode = str(payload.get("mode", ""))
            digest = str(payload.get("input_digest", ""))
            if not mode or not digest:
                return
            key = f"{kind}:{mode}"
            previous = parsed_input.git_evidence_digests.get(key)
            if previous is not None and previous != digest:
                parsed_input.input_changed_during_review = True
            parsed_input.git_evidence_digests[key] = digest

        if is_structured_git_diff:
            try:
                page = payload
                if not page:
                    raise ValueError
            except (TypeError, ValueError):
                return
            observe_git_digest("diff", page)
            records = page.get("records", [])
            discovered_files = list(
                dict.fromkeys(
                    str(item.get("file", ""))
                    for item in records
                    if item.get("file")
                )
            )
            parsed_input.summary.digest = evidence_digest
            if discovered_files:
                merged_files = list(
                    dict.fromkeys([*existing_files, *discovered_files])
                )
                parsed_input.summary.files = merged_files
                parsed_input.summary.file_count = len(merged_files)
            mode = str(page.get("mode", ""))
            if (
                int(page.get("cursor", 0)) == 0
                and mode in {"unstaged", "staged"}
                and mode not in parsed_input.observed_git_modes
            ):
                summary = page.get("summary", {})
                parsed_input.summary.hunk_count += int(summary.get("hunk_count", 0))
                parsed_input.summary.added_lines += int(summary.get("added_lines", 0))
                parsed_input.summary.removed_lines += int(summary.get("removed_lines", 0))
                parsed_input.observed_git_modes.add(mode)
            if not parsed_input.summary.redacted_preview:
                parsed_input.summary.redacted_preview = redact_text(stdout)[:2000]
            return

        discovered_files: list[str] = []
        if is_git_file_list:
            try:
                listed = payload
                if not listed:
                    raise ValueError
            except (TypeError, ValueError):
                listed = {}
            observe_git_digest("files", listed)
            discovered_files.extend(
                str(item.get("path", ""))
                for item in listed.get("records", [])
                if item.get("path")
                and not item.get("truncated")
                and not item.get("normalized")
            )
            if any(
                item.get("truncated") or item.get("normalized")
                for item in listed.get("records", [])
            ):
                parsed_input.input_evidence_incomplete = True
            parsed_input.untracked_files.update(
                str(item.get("path", ""))
                for item in listed.get("records", [])
                if item.get("status") == "??" and item.get("path")
            )
        elif is_file_inspection:
            try:
                inspected = payload
                if not inspected:
                    raise ValueError
            except (TypeError, ValueError):
                inspected = {}
            discovered_files.extend(
                str(item.get("path", ""))
                for item in inspected.get("files", [])
                if item.get("path")
            )
            parsed_input.inspected_files.update(discovered_files)

        parsed_input.summary.digest = evidence_digest
        if discovered_files:
            merged_files = list(
                dict.fromkeys([*existing_files, *discovered_files])
            )
            parsed_input.summary.files = merged_files
            parsed_input.summary.file_count = len(merged_files)
            if not parsed_input.summary.redacted_preview:
                parsed_input.summary.redacted_preview = redact_text(stdout)[:2000]

    @staticmethod
    def _observe_pagination(
        parsed_input: ParsedReviewInput,
        command: str,
        payload: dict[str, object],
    ) -> None:
        """Record whether every bounded sandbox result was read to completion."""
        if "cursor" not in payload or "next_cursor" not in payload:
            return
        try:
            tokens = shlex.split(command)
        except ValueError:
            return
        if "scripts/run_review_rules.py" in tokens:
            key = "rules"
        elif "scripts/inspect_file_list.py" in tokens:
            key = "file-list"
        elif "scripts/review_git_changes.py" in tokens:
            mode = str(payload.get("mode", ""))
            key = f"diff:{mode}" if mode else "diff"
        elif "scripts/inspect_git_files.py" in tokens:
            mode = str(payload.get("mode", ""))
            key = f"files:{mode}" if mode else "files"
        elif "scripts/inspect_files.py" in tokens:
            if parsed_input.summary.kind == "file_list":
                key = "inspect:file-list"
            else:
                stable_tokens = []
                skip = False
                for token in tokens:
                    if skip:
                        skip = False
                        continue
                    if token in {"--cursor", "--limit"}:
                        skip = True
                        continue
                    stable_tokens.append(token)
                key = "inspect:" + hashlib.sha256(
                    "\0".join(stable_tokens).encode("utf-8")
                ).hexdigest()[:16]
            parsed_input.inspected_files.update(
                str(item.get("path", ""))
                for item in payload.get("files", [])
                if isinstance(item, dict) and item.get("path")
            )
        else:
            return
        next_cursor = payload.get("next_cursor")
        cursor = payload.get("cursor")
        if not isinstance(cursor, int) or (
            next_cursor is not None and not isinstance(next_cursor, int)
        ):
            parsed_input.input_evidence_incomplete = True
            return
        seen = parsed_input.pagination_seen_cursors.setdefault(key, set())
        if cursor in seen:
            return
        expected = parsed_input.pagination_next_cursors.get(key, 0)
        if cursor != expected:
            parsed_input.input_evidence_incomplete = True
            return
        seen.add(cursor)
        parsed_input.pagination_next_cursors[key] = next_cursor

    def _find_cached_report(
        self,
        parsed_input: ParsedReviewInput,
    ) -> ReviewReport | None:
        """Expose only exact immutable-input history to the reasoning Agent."""
        if parsed_input.summary.kind not in {"diff_file", "fixture"}:
            return None
        return self.store.get_latest_by_input_digest(
            parsed_input.summary.digest,
            parsed_input.summary.review_profile,
        )

    @staticmethod
    def _build_monitoring(
        started: float,
        analysis: ReviewAnalysis,
        decisions: list[FilterDecision],
        runs: list[SandboxRun],
        tool_calls: int,
    ) -> MonitoringSummary:
        severities: dict[str, int] = {}
        for finding in analysis.findings:
            severities[finding.severity] = severities.get(finding.severity, 0) + 1
        exceptions: dict[str, int] = {}
        for run in runs:
            if run.error_type:
                exceptions[run.error_type] = exceptions.get(run.error_type, 0) + 1
        return MonitoringSummary(
            total_duration_ms=(time.perf_counter() - started) * 1000,
            sandbox_duration_ms=sum(
                run.duration_ms for run in runs if run.status != "blocked"
            ),
            tool_call_count=tool_calls,
            blocked_count=sum(decision.decision != "allow" for decision in decisions),
            finding_count=len(analysis.findings),
            severity_distribution=severities,
            exception_distribution=exceptions,
        )
