# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Persistence layer: 7-table review schema on top of the SDK's SqlStorage.

The schema lives in a dedicated ``ReviewStorageBase`` metadata so it can share
a database with (or stay separate from) the SDK's own tables.  SQLite is the
default backend; the DSN can point at MySQL/PostgreSQL unchanged because all
column types are the SDK's dialect-aware decorators (DynamicJSON /
UTF8MB4String / PreciseTimestamp).

There is no init-db migration script on purpose: ``SqlStorage`` runs
``create_all`` plus a forward-only add-column migration on first use.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trpc_agent_sdk.storage import DynamicJSON, PreciseTimestamp, SqlStorage, UTF8MB4String

# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class ReviewStorageBase(DeclarativeBase):
    """Dedicated metadata for the review schema."""


def _uuid() -> str:
    return uuid.uuid4().hex


class ReviewTask(ReviewStorageBase):
    """One review invocation: the aggregate root every other row points at."""

    __tablename__ = "review_task"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(PreciseTimestamp, nullable=True)
    # pending -> running -> succeeded | partial | failed
    status: Mapped[str] = mapped_column(UTF8MB4String(32), default="pending")
    input_type: Mapped[str] = mapped_column(UTF8MB4String(32), default="")  # diff_file|repo_path|fixture
    input_ref: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    diff_digest: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    mode: Mapped[str] = mapped_column(UTF8MB4String(16), default="diff_only")  # repo|diff_only
    runtime: Mapped[str] = mapped_column(UTF8MB4String(16), default="")  # container|local
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)
    error: Mapped[str] = mapped_column(UTF8MB4String(1024), default="")


class DiffFile(ReviewStorageBase):
    """Summary of one file in the input diff."""

    __tablename__ = "diff_file"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    change_type: Mapped[str] = mapped_column(UTF8MB4String(16), default="modified")
    is_binary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_rename: Mapped[bool] = mapped_column(Boolean, default=False)
    old_path: Mapped[Optional[str]] = mapped_column(UTF8MB4String(512), nullable=True)
    hunk_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_line_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str] = mapped_column(UTF8MB4String(256), default="")


class SandboxRun(ReviewStorageBase):
    """One sandbox execution attempt (maps 1:1 onto SkillRunOutput)."""

    __tablename__ = "sandbox_run"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    tool: Mapped[str] = mapped_column(UTF8MB4String(64), default="skill_run")
    command: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    runtime: Mapped[str] = mapped_column(UTF8MB4String(16), default="")
    # ok | timeout | error | denied
    status: Mapped[str] = mapped_column(UTF8MB4String(16), default="ok")
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout_digest: Mapped[str] = mapped_column(UTF8MB4String(2048), default="")
    stderr_digest: Mapped[str] = mapped_column(UTF8MB4String(2048), default="")


class FilterEvent(ReviewStorageBase):
    """One governance decision made by the review filter chain."""

    __tablename__ = "filter_event"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    tool_name: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    # allow | deny | needs_human_review
    decision: Mapped[str] = mapped_column(UTF8MB4String(32), default="allow")
    rule: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    reason: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    args_digest: Mapped[str] = mapped_column(UTF8MB4String(1024), default="")


class Finding(ReviewStorageBase):
    """One structured review finding after triage/dedup/redaction."""

    __tablename__ = "finding"
    __table_args__ = (UniqueConstraint("task_id", "dedup_key", name="uq_finding_task_dedup"), )

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    dedup_key: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    rule_id: Mapped[str] = mapped_column(UTF8MB4String(32), default="")
    category: Mapped[str] = mapped_column(UTF8MB4String(32), default="")
    severity: Mapped[str] = mapped_column(UTF8MB4String(16), default="")
    confidence: Mapped[str] = mapped_column(UTF8MB4String(16), default="")
    source: Mapped[str] = mapped_column(UTF8MB4String(32), default="static")
    file: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    line: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(UTF8MB4String(512), default="")
    evidence: Mapped[str] = mapped_column(UTF8MB4String(2048), default="")
    recommendation: Mapped[str] = mapped_column(UTF8MB4String(2048), default="")
    fix_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)
    # reported | warning | needs_human_review | suppressed
    status: Mapped[str] = mapped_column(UTF8MB4String(32), default="reported")


class Report(ReviewStorageBase):
    """Final rendered report artifacts."""

    __tablename__ = "report"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    format: Mapped[str] = mapped_column(UTF8MB4String(16), default="json")  # json|md
    content: Mapped[str] = mapped_column(Text, default="")
    summary_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)


class Metrics(ReviewStorageBase):
    """Monitoring summary for one review task."""

    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(UTF8MB4String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp, default=datetime.now)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    sandbox_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    filter_blocks: Mapped[int] = mapped_column(Integer, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    severity_dist_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)
    error_dist_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)
    token_usage_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)
    phase_timings_json: Mapped[Optional[dict]] = mapped_column(DynamicJSON, nullable=True)


# ---------------------------------------------------------------------------
# Store facade
# ---------------------------------------------------------------------------


def digest(text: str, limit: int = 512) -> str:
    """Short digest for logs stored in narrow columns: prefix + sha256."""
    if not text:
        return ""
    head = text[:limit].replace("\n", "\\n")
    return f"{head} [sha256:{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:16]}, {len(text)} chars]"


class ReviewStore:
    """Thin synchronous-friendly facade over SqlStorage for the review schema.

    All writes go through ``asyncio``-compatible methods because SqlStorage is
    async-facing even in sync mode.  A different SQL backend is one DSN away.
    """

    def __init__(self, db_url: str = "sqlite:///review.db") -> None:
        self._db_url = db_url
        # expire_on_commit=False: rows keep their attributes readable after the
        # session closes (the report renders from the same ORM objects)
        self._storage = SqlStorage(is_async=False,
                                   db_url=db_url,
                                   metadata=ReviewStorageBase.metadata,
                                   expire_on_commit=False)

    @property
    def db_url(self) -> str:
        return self._db_url

    async def init(self) -> None:
        """Create tables (idempotent).  Also validates the DSN early."""
        await self._storage.create_sql_engine()

    async def close(self) -> None:
        await self._storage.close()

    # -- generic helpers ---------------------------------------------------

    async def add_all(self, rows: list[Any]) -> None:
        async with self._storage.create_db_session() as session:
            for row in rows:
                session.add(row)
            session.commit()

    async def add(self, row: Any) -> None:
        await self.add_all([row])

    async def update_task(self, task_id: str, **fields: Any) -> None:
        async with self._storage.create_db_session() as session:
            task = session.get(ReviewTask, task_id)
            if task is None:
                return
            for key, value in fields.items():
                setattr(task, key, value)
            session.commit()

    async def insert_findings(self, rows: list[Finding]) -> int:
        """Insert findings honouring the (task_id, dedup_key) unique constraint.

        Rows violating the constraint are skipped (second line of defence —
        the in-memory dedup should already have removed them).  Returns the
        number of rows actually inserted.
        """
        inserted = 0
        async with self._storage.create_db_session() as session:
            for row in rows:
                exists = session.execute(
                    select(Finding.id).where(Finding.task_id == row.task_id,
                                             Finding.dedup_key == row.dedup_key)).first()
                if exists:
                    continue
                session.add(row)
                inserted += 1
            session.commit()
        return inserted

    # -- queries for the `show` command ------------------------------------

    async def load_task_bundle(self, task_id: str) -> Optional[dict]:
        """Everything recorded for one task, for ``show --task-id``."""
        async with self._storage.create_db_session() as session:
            task = session.get(ReviewTask, task_id)
            if task is None:
                return None

            def rows(model, order=None):
                stmt = select(model).where(model.task_id == task_id)
                if order is not None:
                    stmt = stmt.order_by(order)
                return session.execute(stmt).scalars().all()

            return {
                "task": task,
                "diff_files": rows(DiffFile),
                "sandbox_runs": rows(SandboxRun, SandboxRun.started_at),
                "filter_events": rows(FilterEvent, FilterEvent.created_at),
                "findings": rows(Finding, Finding.created_at),
                "reports": rows(Report, Report.created_at),
                "metrics": rows(Metrics, Metrics.created_at),
            }

    async def list_tasks(self, limit: int = 20) -> list[ReviewTask]:
        async with self._storage.create_db_session() as session:
            stmt = select(ReviewTask).order_by(ReviewTask.created_at.desc()).limit(limit)
            return session.execute(stmt).scalars().all()
