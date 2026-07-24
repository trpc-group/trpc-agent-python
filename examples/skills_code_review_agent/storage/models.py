# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLAlchemy declarative models for the code-review example."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return uuid.uuid4().hex


class CrBase(DeclarativeBase):
    """Dedicated base so only this example's tables are created."""


class ReviewTaskRow(CrBase):
    __tablename__ = "cr_review_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    input_type: Mapped[str] = mapped_column(String(32), default="")
    input_ref: Mapped[str] = mapped_column(String(512), default="")
    runtime: Mapped[str] = mapped_column(String(32), default="local")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    diff_summary: Mapped[dict] = mapped_column(JSON, nullable=True)


class SandboxRunRow(CrBase):
    __tablename__ = "cr_sandbox_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("cr_review_tasks.id"))
    script: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout_summary: Mapped[str] = mapped_column(Text, default="")
    stderr_summary: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class FindingRow(CrBase):
    __tablename__ = "cr_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("cr_review_tasks.id"))
    severity: Mapped[str] = mapped_column(String(16), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    file: Mapped[str] = mapped_column(String(512), default="")
    line: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="static")
    status: Mapped[str] = mapped_column(String(32), default="reported")
    dedup_key: Mapped[str] = mapped_column(String(640), default="")


class FilterEventRow(CrBase):
    __tablename__ = "cr_filter_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("cr_review_tasks.id"))
    target: Mapped[str] = mapped_column(String(512), default="")
    decision: Mapped[str] = mapped_column(String(32), default="")
    rule: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MetricsRow(CrBase):
    __tablename__ = "cr_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("cr_review_tasks.id"))
    total_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    sandbox_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    intercepts: Mapped[int] = mapped_column(Integer, default=0)
    findings_total: Mapped[int] = mapped_column(Integer, default=0)
    severity_distribution: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_distribution: Mapped[dict] = mapped_column(JSON, nullable=True)


class ReportRow(CrBase):
    __tablename__ = "cr_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("cr_review_tasks.id"))
    report_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    report_md: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
