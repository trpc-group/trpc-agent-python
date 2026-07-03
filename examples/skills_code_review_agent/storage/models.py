# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""SQLAlchemy ORM models for code-review persistence (issue #92, requirement 5).

Mirrors the framework's own SQL pattern (``trpc_agent_sdk/sessions/_sql_session_service.py``):
a dedicated ``DeclarativeBase`` subclass so ``SqlStorage`` only creates *these* tables, and
portable column decorators (``UTF8MB4String`` / ``DynamicJSON`` / ``PreciseTimestamp``) so the
same schema runs on SQLite (default), PostgreSQL, or MySQL with no code change.

Four tables, all keyed by ``task_id`` so a whole review is queryable by task id (requirement 3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from trpc_agent_sdk.storage import (
    DEFAULT_MAX_KEY_LENGTH,
    DEFAULT_MAX_VARCHAR_LENGTH,
    DynamicJSON,
    PreciseTimestamp,
    UTF8MB4String,
)


class CodeReviewBase(DeclarativeBase):
    """Isolated metadata: SqlStorage(metadata=CodeReviewBase.metadata) creates only these tables."""


class ReviewTaskORM(CodeReviewBase):
    __tablename__ = "cr_review_tasks"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    source_type: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))  # diff_file|repo_path|fixture
    source_ref: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    model_name: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="")
    runtime: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="local")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="running")
    block_count: Mapped[int] = mapped_column(Integer, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    severity_dist: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, default=dict)
    exception_dist: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, default=dict)
    create_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())
    update_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())

    sandbox_runs: Mapped[list["SandboxRunORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    findings: Mapped[list["FindingORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    reports: Mapped[list["ReportORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class SandboxRunORM(CodeReviewBase):
    __tablename__ = "cr_sandbox_runs"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"))
    script: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="")
    cmd: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="")
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout_bytes: Mapped[int] = mapped_column(Integer, default=0)
    stderr_bytes: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[Optional[str]] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    block_category: Mapped[Optional[str]] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    create_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())

    task: Mapped["ReviewTaskORM"] = relationship(back_populates="sandbox_runs")


class FindingORM(CodeReviewBase):
    __tablename__ = "cr_findings"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"))
    sandbox_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("cr_sandbox_runs.id", ondelete="SET NULL"),
                                                          nullable=True)
    severity: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    category: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    file: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    evidence: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, default=dict)
    recommendation: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="rule")
    status: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="active")
    dedup_key: Mapped[Optional[str]] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), nullable=True, index=True)
    create_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())

    task: Mapped["ReviewTaskORM"] = relationship(back_populates="findings")


class ReportORM(CodeReviewBase):
    __tablename__ = "cr_reports"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="json")  # json|md
    content_ref: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), default="")
    summary: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, default=dict)
    create_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())

    task: Mapped["ReviewTaskORM"] = relationship(back_populates="reports")
