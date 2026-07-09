# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQLAlchemy ORM models for the Code Review Agent (Phase 0, SDK-backed).

The seven business tables are declared as a custom ``CRBase(DeclarativeBase)``
metadata and persisted through the SDK's generic :class:`SqlStorage` layer
(``SqlStorage(metadata=CRBase.metadata)``). This replaces the original
hand-rolled ``sqlite3`` implementation while keeping the same schema and
the same :class:`ReviewStore` protocol surface.

Table layout mirrors ``db/schema.sql`` (ARCHITECTURE.md §5.2): seven tables
aggregated around ``review_task`` with ``task_id`` as the global FK. The
composite index ``idx_finding_dedup (task_id, file, line, category)`` is
declared on :class:`Finding` to serve the Phase-4 dedupe lookup.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy import Float
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class CRBase(DeclarativeBase):
    """Declarative base for all CR Agent business tables.

    Passed to ``SqlStorage(metadata=CRBase.metadata)`` so the SDK storage
    layer creates/manages exactly these tables (independent of the SDK's own
    session/memory tables).
    """


class ReviewTask(CRBase):
    """L6 master table — one row per review run."""

    __tablename__ = "review_task"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending|running|done|failed
    input_type: Mapped[str] = mapped_column(String, nullable=False)  # diff|repo|fixture
    input_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # dry-run|real
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InputDiff(CRBase):
    """L1 parsed diff per changed file (summary only, never the full blob)."""

    __tablename__ = "input_diff"
    __table_args__ = (Index("idx_input_diff_task", "task_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    hunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class SandboxRun(CRBase):
    """L4 sandbox execution evidence (replayable / auditable)."""

    __tablename__ = "sandbox_run"
    __table_args__ = (Index("idx_sandbox_run_task", "task_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    runtime: Mapped[str | None] = mapped_column(String, nullable=True)  # local|container|cube
    script: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)  # ok|timeout|failed|truncated
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timed_out: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0|1 (no native bool)
    masked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Finding(CRBase):
    """L5 structured findings (bucket enforces confidence tiering)."""

    __tablename__ = "finding"
    # Composite index directly serves L5 dedupe: O(1) "same file/line/category".
    __table_args__ = (
        Index("idx_finding_dedup", "task_id", "file", "line", "category"),
        Index("idx_finding_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)  # critical|high|medium|low
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    file: Mapped[str | None] = mapped_column(String, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0-1.0
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # rule|sandbox|llm
    bucket: Mapped[str | None] = mapped_column(String, nullable=True)  # findings|warnings|needs_human_review


class FilterBlock(CRBase):
    """L3 filter governance blocks (deny / needs_human_review skip sandbox)."""

    __tablename__ = "filter_block"
    __table_args__ = (Index("idx_filter_block_task", "task_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)  # high-risk|forbidden-path|network|budget
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)  # deny|needs_human_review
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class MonitorSummary(CRBase):
    """Per-task telemetry rollup (severity flattened to columns for indexed reads)."""

    __tablename__ = "monitor_summary"
    __table_args__ = (Index("idx_monitor_summary_task", "task_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sandbox_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finding_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sev_critical: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sev_high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sev_medium: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sev_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exception_types: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string


class ReviewReport(CRBase):
    """Final report artefact paths + human-readable summary."""

    __tablename__ = "review_report"
    __table_args__ = (Index("idx_review_report_task", "task_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("review_task.id"), nullable=False)
    report_json_path: Mapped[str | None] = mapped_column(String, nullable=True)
    report_md_path: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True)


__all__ = [
    "CRBase",
    "ReviewTask",
    "InputDiff",
    "SandboxRun",
    "Finding",
    "FilterBlock",
    "MonitorSummary",
    "ReviewReport",
]
