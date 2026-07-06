# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ORM schema for the code-review store (issue requirement 5, acceptance 3).

Five tables under an example-owned ``DeclarativeBase`` (own metadata, so
``SqlStorage.create_sql_engine`` only creates/migrates OUR tables):
``cr_review_task``, ``cr_sandbox_run``, ``cr_filter_event``, ``cr_finding``,
``cr_report``. Column types reuse the SDK's portable helpers
(``UTF8MB4String`` / ``DynamicJSON`` / ``PreciseTimestamp``) so the exact
same schema works on SQLite (default), MySQL and PostgreSQL — swapping the
backend is just a different SQLAlchemy URL.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional

from sqlalchemy import Boolean
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH
from trpc_agent_sdk.storage import DynamicJSON
from trpc_agent_sdk.storage import PreciseTimestamp
from trpc_agent_sdk.storage import UTF8MB4String

_STATUS_LEN = 32
_PATH_LEN = 512


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewStorageBase(DeclarativeBase):
    """Dedicated metadata root — create_all touches only cr_* tables."""


class ReviewTaskRow(ReviewStorageBase):
    """One review task (status lifecycle: pending → running → completed*/failed)."""

    __tablename__ = "cr_review_task"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow, onupdate=utcnow)
    status: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="pending")
    input_type: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    input_ref: Mapped[str] = mapped_column(UTF8MB4String(_PATH_LEN), default="")
    diff_summary: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    config: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    error_type: Mapped[str] = mapped_column(UTF8MB4String(128), default="")
    error_message: Mapped[str] = mapped_column(Text(), default="")


class SandboxRunRow(ReviewStorageBase):
    """One sandbox run attempt (including blocked and failed attempts)."""

    __tablename__ = "cr_sandbox_run"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), index=True)
    run_index: Mapped[int] = mapped_column(Integer(), default=0)
    kind: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    runtime_kind: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    cmd: Mapped[str] = mapped_column(UTF8MB4String(128), default="")
    args: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    started_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow)
    duration_ms: Mapped[float] = mapped_column(Float(), default=0.0)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer(), nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean(), default=False)
    status: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    filter_action: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    filter_reasons: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    stdout_excerpt: Mapped[str] = mapped_column(Text(), default="")
    stderr_excerpt: Mapped[str] = mapped_column(Text(), default="")
    output_truncated: Mapped[bool] = mapped_column(Boolean(), default=False)
    error_type: Mapped[str] = mapped_column(UTF8MB4String(128), default="")


class FilterEventRow(ReviewStorageBase):
    """One governance decision (allow / deny / needs_human_review) with reasons."""

    __tablename__ = "cr_filter_event"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), index=True)
    stage: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    target: Mapped[str] = mapped_column(UTF8MB4String(_PATH_LEN), default="")
    action: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    rule: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    reasons: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow)


class FindingRow(ReviewStorageBase):
    """One structured finding (bucket separates findings from needs_human_review)."""

    __tablename__ = "cr_finding"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), index=True)
    severity: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    category: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    file: Mapped[str] = mapped_column(UTF8MB4String(_PATH_LEN), default="")
    line: Mapped[int] = mapped_column(Integer(), default=0)
    title: Mapped[str] = mapped_column(UTF8MB4String(_PATH_LEN), default="")
    evidence: Mapped[str] = mapped_column(Text(), default="")  # redacted before persist
    recommendation: Mapped[str] = mapped_column(Text(), default="")
    confidence: Mapped[float] = mapped_column(Float(), default=0.0)
    source: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="")
    rule_id: Mapped[str] = mapped_column(UTF8MB4String(64), default="")
    bucket: Mapped[str] = mapped_column(UTF8MB4String(_STATUS_LEN), default="finding")
    dedup_key: Mapped[str] = mapped_column(UTF8MB4String(_PATH_LEN), default="")
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow)


class ReportRow(ReviewStorageBase):
    """Final report document + monitoring summary for one task."""

    __tablename__ = "cr_report"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp(), default=utcnow)
    summary: Mapped[str] = mapped_column(Text(), default="")
    findings_total: Mapped[int] = mapped_column(Integer(), default=0)
    severity_stats: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    filter_summary: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    sandbox_summary: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    metrics: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
    report: Mapped[Optional[Any]] = mapped_column(DynamicJSON(), nullable=True)
