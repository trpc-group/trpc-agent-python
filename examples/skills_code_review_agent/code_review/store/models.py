#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""SQLAlchemy models for the five code-review tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trpc_agent_sdk.storage._sql_common import (
    DynamicJSON,
    PreciseTimestamp,
)


def _utc_now() -> datetime:
    """返回供可移植时间戳列使用的带时区 UTC 当前时间。"""

    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Isolated metadata for the code-review business tables."""


class ReviewTaskModel(Base):
    """One automatic code-review task."""

    __tablename__ = "cr_review_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_ref: Mapped[str] = mapped_column(Text, nullable=False)
    diff_summary: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class SandboxRunModel(Base):
    """One governed sandbox execution attempt."""

    __tablename__ = "cr_sandbox_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("cr_review_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    filter_action: Mapped[str | None] = mapped_column(String(32))
    stdout_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stderr_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        nullable=False,
    )


class FilterEventModel(Base):
    """One Filter decision made before sandbox execution."""

    __tablename__ = "cr_filter_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("cr_review_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(128), nullable=False)
    reasons: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        nullable=False,
    )


class FindingModel(Base):
    """One deduplicated or suppressed review candidate."""

    __tablename__ = "cr_finding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("cr_review_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_cr_finding_task_file_line_category",
            "task_id",
            "file",
            "line",
            "category",
        ),
    )


class ReportModel(Base):
    """Canonical, fully redacted report for a task."""

    __tablename__ = "cr_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("cr_review_task.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_pack_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    severity_stats: Mapped[dict[str, Any]] = mapped_column(
        DynamicJSON,
        nullable=False,
    )
    filter_summary: Mapped[dict[str, Any]] = mapped_column(
        DynamicJSON,
        nullable=False,
    )
    sandbox_summary: Mapped[dict[str, Any]] = mapped_column(
        DynamicJSON,
        nullable=False,
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        PreciseTimestamp,
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )
