"""SQL schema and storage facade for code review tasks."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import hashlib
from typing import Any
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from trpc_agent_sdk.storage import DynamicJSON
from trpc_agent_sdk.storage import SqlAsyncContextManager
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage

from .constants import ASYNC_SQL_DRIVER_MARKERS
from .constants import DB_CATEGORY_LENGTH
from .constants import DB_ID_LENGTH
from .constants import DB_PATH_LENGTH
from .constants import DB_SOURCE_LENGTH
from .constants import DB_STATUS_LENGTH
from .constants import DB_TITLE_LENGTH
from .constants import SQLITE_BUSY_TIMEOUT_MILLISECONDS
from .constants import MAX_TEXT_FIELD_LENGTH
from .models import FilterDecision
from .models import Finding
from .models import ReviewInput
from .models import ReviewReport
from .models import SandboxRun
from .models import TaskStatus
from .policy import SecretRedactor

_SEVERITY_RANK = {
    "warning": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
_MERGE_SEPARATOR = " | "


def utc_now() -> datetime:
    """Return one timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class ReviewStorageBase(DeclarativeBase):
    """Metadata root dedicated to this example."""


class ReviewTaskRow(ReviewStorageBase):
    """Top-level review task."""

    __tablename__ = "review_tasks"

    id: Mapped[str] = mapped_column(String(DB_ID_LENGTH), primary_key=True)
    status: Mapped[str] = mapped_column(String(DB_STATUS_LENGTH), nullable=False)
    input_kind: Mapped[str] = mapped_column(String(DB_STATUS_LENGTH), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(DB_ID_LENGTH), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column()


class FilterDecisionRow(ReviewStorageBase):
    """Persisted decision made before sandbox execution."""

    __tablename__ = "review_filter_decisions"

    id: Mapped[str] = mapped_column(String(DB_ID_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(DB_STATUS_LENGTH), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(DB_CATEGORY_LENGTH), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(DB_ID_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)


class SandboxRunRow(ReviewStorageBase):
    """One sandbox attempt, including failures and timeouts."""

    __tablename__ = "review_sandbox_runs"

    id: Mapped[str] = mapped_column(String(DB_ID_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(DB_STATUS_LENGTH), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(default=False, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    stdout: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stderr: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(DB_CATEGORY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)


class FindingRow(ReviewStorageBase):
    """One normalized and deduplicated code-review finding."""

    __tablename__ = "review_findings"
    __table_args__ = (
        UniqueConstraint("task_id", "fingerprint", name="uq_review_finding_fingerprint"),
        Index(
            "ix_review_finding_location",
            "task_id",
            "file_path",
            "line",
            "category",
        ),
    )

    id: Mapped[str] = mapped_column(String(DB_ID_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(String(DB_ID_LENGTH), nullable=False)
    severity: Mapped[str] = mapped_column(String(DB_STATUS_LENGTH), nullable=False)
    category: Mapped[str] = mapped_column(String(DB_CATEGORY_LENGTH), nullable=False)
    file_path: Mapped[str] = mapped_column(String(DB_PATH_LENGTH), nullable=False)
    line: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(DB_TITLE_LENGTH), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(DB_SOURCE_LENGTH), nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)


class ReviewReportRow(ReviewStorageBase):
    """Generated JSON and Markdown report."""

    __tablename__ = "review_reports"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)


def create_sql_storage(db_url: str) -> SqlStorage:
    """Create storage with metadata limited to review tables."""
    return SqlStorage(
        is_async=any(marker in db_url for marker in ASYNC_SQL_DRIVER_MARKERS),
        db_url=db_url,
        metadata=ReviewStorageBase.metadata,
    )


def finding_fingerprint(finding: Finding) -> str:
    """Build the location/category fingerprint required for deduplication."""
    value = f"{finding.file}\x00{finding.line}\x00{finding.category.value}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReviewStore:
    """Narrow async facade over the repository SQL storage."""

    def __init__(self, db_url: str, redactor: SecretRedactor) -> None:
        self._db_url = db_url
        self._storage = create_sql_storage(db_url)
        self._redactor = redactor

    async def initialize(self) -> None:
        """Create missing tables and configure SQLite concurrency."""
        await self._storage.create_sql_engine()
        if self._db_url.startswith("sqlite"):
            await self._configure_sqlite()

    async def close(self) -> None:
        """Close the underlying SQL engine."""
        await self._storage.close()

    async def _configure_sqlite(self) -> None:
        from sqlalchemy import text

        async with SqlAsyncContextManager(self._storage) as session:
            await self._execute_sql(session, text("PRAGMA journal_mode=WAL"))
            statement = text(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
            await self._execute_sql(session, statement)
            await self._storage.commit(session)

    @staticmethod
    async def _execute_sql(session, statement):
        from sqlalchemy.ext.asyncio import AsyncSession

        if isinstance(session, AsyncSession):
            return await session.execute(statement)
        return session.execute(statement)

    async def create_task(self, task_id: str, review_input: ReviewInput) -> None:
        """Persist a CREATED task without raw diff content."""
        summary = {
            "source": review_input.source,
            "file_count": len(review_input.files),
            "warnings": review_input.warnings,
        }
        row = ReviewTaskRow(
            id=task_id,
            status=TaskStatus.CREATED.value,
            input_kind=review_input.kind.value,
            input_digest=review_input.digest,
            input_summary=self._redactor.redact_value(summary),
        )
        await self._add_and_commit(row)

    async def save_filter_decisions(
        self,
        task_id: str,
        decisions: list[FilterDecision],
    ) -> None:
        """Commit Filter audit rows before execution."""
        rows = [
            FilterDecisionRow(
                id=uuid.uuid4().hex,
                task_id=task_id,
                action=item.action.value,
                rule_id=item.rule_id,
                reason=self._redactor.redact_text(item.reason),
                plan_digest=item.plan_digest,
                created_at=item.created_at,
            ) for item in decisions
        ]
        await self._add_many_and_commit(rows)

    async def save_sandbox_run(self, task_id: str, run: SandboxRun) -> None:
        """Persist one bounded and redacted sandbox record."""
        row = SandboxRunRow(
            id=uuid.uuid4().hex,
            task_id=task_id,
            status=run.status.value,
            exit_code=run.exit_code,
            timed_out=run.timed_out,
            duration_ms=run.duration_ms,
            stdout=self._redactor.redact_text(run.stdout),
            stderr=self._redactor.redact_text(run.stderr),
            error_type=self._redactor.redact_text(run.error_type or "") or None,
        )
        await self._add_and_commit(row)

    async def save_findings(
        self,
        task_id: str,
        findings: list[Finding],
        human_review: set[str],
    ) -> None:
        """Insert deduplicated findings; tolerate concurrent duplicate writes."""
        for finding in findings:
            await self._save_finding(task_id, finding, human_review)

    async def _save_finding(
        self,
        task_id: str,
        finding: Finding,
        human_review: set[str],
    ) -> None:
        fingerprint = finding_fingerprint(finding)
        row = FindingRow(
            id=uuid.uuid4().hex,
            task_id=task_id,
            fingerprint=fingerprint,
            severity=finding.severity.value,
            category=finding.category.value,
            file_path=finding.file,
            line=finding.line,
            title=self._redactor.redact_text(finding.title),
            evidence=self._redactor.redact_text(finding.evidence),
            recommendation=self._redactor.redact_text(finding.recommendation),
            confidence=finding.confidence,
            source=self._redactor.redact_text(finding.source),
            needs_human_review=fingerprint in human_review,
        )
        try:
            await self._add_and_commit(row)
        except IntegrityError:
            await self._merge_existing_finding(task_id, row)

    async def complete_task(self, report: ReviewReport, markdown: str) -> None:
        """Atomically persist report and terminal task state."""
        payload = self._redactor.redact_value(report.model_dump(mode="json"))
        async with SqlAsyncContextManager(self._storage) as session:
            task = await self._storage.get(
                session,
                SqlKey((report.task_id, ), ReviewTaskRow),
            )
            if task is None:
                raise KeyError(f"unknown review task: {report.task_id}")
            row = ReviewReportRow(
                task_id=report.task_id,
                report_json=payload,
                report_markdown=self._redactor.redact_text(markdown),
                metrics=payload["metrics"],
                created_at=report.created_at,
            )
            task.status = report.status.value
            task.conclusion = self._redactor.redact_text(report.conclusion)
            task.finished_at = utc_now()
            await self._storage.add(session, row)
            await self._storage.commit(session)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        failure: str | None = None,
    ) -> None:
        """Update task state and terminal timestamps."""
        async with SqlAsyncContextManager(self._storage) as session:
            row = await self._storage.get(session, SqlKey((task_id, ), ReviewTaskRow))
            if row is None:
                raise KeyError(f"unknown review task: {task_id}")
            row.status = status.value
            if status == TaskStatus.FAILED and failure:
                row.conclusion = "Review task failed."
            row.failure_reason = self._redactor.redact_text(failure or "") or None
            if status in {
                    TaskStatus.COMPLETE,
                    TaskStatus.PARTIAL,
                    TaskStatus.FAILED,
                    TaskStatus.DENIED,
            }:
                row.finished_at = utc_now()
            await self._storage.commit(session)

    async def _merge_existing_finding(
        self,
        task_id: str,
        incoming: FindingRow,
    ) -> None:
        condition = SqlCondition(
            filters=[
                FindingRow.task_id == task_id,
                FindingRow.fingerprint == incoming.fingerprint,
            ],
            limit=1,
        )
        async with SqlAsyncContextManager(self._storage) as session:
            rows = await self._storage.query(
                session,
                SqlKey((), FindingRow),
                condition,
            )
            if not rows:
                raise RuntimeError("finding conflict could not be reloaded")
            existing = rows[0]
            if _incoming_finding_wins(existing, incoming):
                existing.severity = incoming.severity
                existing.title = incoming.title
                existing.recommendation = incoming.recommendation
                existing.confidence = incoming.confidence
            existing.evidence = _merge_db_text(
                existing.evidence,
                incoming.evidence,
                MAX_TEXT_FIELD_LENGTH,
            )
            existing.source = _merge_db_text(
                existing.source,
                incoming.source,
                DB_SOURCE_LENGTH,
            )
            existing.needs_human_review |= incoming.needs_human_review
            await self._storage.commit(session)

    async def get_report(self, task_id: str) -> dict[str, Any] | None:
        """Return a task and its persisted report by id."""
        async with SqlAsyncContextManager(self._storage) as session:
            task = await self._storage.get(session, SqlKey((task_id, ), ReviewTaskRow))
            if task is None:
                return None
            report = await self._storage.get(session, SqlKey((task_id, ), ReviewReportRow))
            return {
                "task_id": task.id,
                "status": task.status,
                "input_summary": task.input_summary,
                "failure_reason": task.failure_reason,
                "report": report.report_json if report else None,
                "markdown": report.report_markdown if report else None,
            }

    async def list_findings(self, task_id: str) -> list[FindingRow]:
        """Query findings for tests and integrations."""
        condition = SqlCondition(
            filters=[FindingRow.task_id == task_id],
            order_func=lambda: FindingRow.id,
        )
        async with SqlAsyncContextManager(self._storage) as session:
            return await self._storage.query(
                session,
                SqlKey((), FindingRow),
                condition,
            )

    async def _add_and_commit(self, row: ReviewStorageBase) -> None:
        async with SqlAsyncContextManager(self._storage) as session:
            await self._storage.add(session, row)
            await self._storage.commit(session)

    async def _add_many_and_commit(self, rows: list[ReviewStorageBase]) -> None:
        async with SqlAsyncContextManager(self._storage) as session:
            for row in rows:
                await self._storage.add(session, row)
            await self._storage.commit(session)


def _incoming_finding_wins(existing: FindingRow, incoming: FindingRow) -> bool:
    current = (_SEVERITY_RANK[existing.severity], existing.confidence)
    candidate = (_SEVERITY_RANK[incoming.severity], incoming.confidence)
    return candidate > current


def _merge_db_text(left: str, right: str, limit: int) -> str:
    values = []
    for value in (left, right):
        if value and value not in values:
            values.append(value)
    return _MERGE_SEPARATOR.join(values)[:limit]
