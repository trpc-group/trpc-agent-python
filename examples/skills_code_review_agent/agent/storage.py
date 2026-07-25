#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Portable SQL persistence for review tasks and audit records."""

from __future__ import annotations

import inspect
import json
from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Any
from typing import Optional

from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from trpc_agent_sdk.storage import SqlStorage

from .core import FilterDecision
from .core import Finding
from .core import InputSummary
from .core import MonitoringSummary
from .core import ReviewReport
from .core import SandboxRun
from .core import SecretRedactor


class ReviewStorageBase(DeclarativeBase):
    """Separate metadata keeps this example isolated from SDK service tables."""


class ReviewTaskRecord(ReviewStorageBase):
    """Top-level review task."""

    __tablename__ = "cr_review_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SandboxRunRecord(ReviewStorageBase):
    """One sandbox execution attempt."""

    __tablename__ = "cr_sandbox_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"), index=True)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timed_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stdout: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stderr: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_truncated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    skill_loaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class FilterDecisionRecord(ReviewStorageBase):
    """Filter governance audit record."""

    __tablename__ = "cr_filter_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    network_hosts_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


class FindingRecord(ReviewStorageBase):
    """Deduplicated finding or low-confidence warning."""

    __tablename__ = "cr_findings"
    __table_args__ = (
        UniqueConstraint("task_id", "file", "line", "category", name="uq_cr_finding_location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"), index=True)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    file: Mapped[str] = mapped_column(String(512), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)


class MetricsRecord(ReviewStorageBase):
    """Monitoring summary for a review."""

    __tablename__ = "cr_monitoring_summaries"

    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"), primary_key=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    interception_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sandbox_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class ReportRecord(ReviewStorageBase):
    """Final report snapshot for replay."""

    __tablename__ = "cr_reports"

    task_id: Mapped[str] = mapped_column(ForeignKey("cr_review_tasks.id", ondelete="CASCADE"), primary_key=True)
    conclusion: Mapped[str] = mapped_column(Text, nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _clean(value: Any) -> Any:
    clean, _ = SecretRedactor.redact_value(value)
    return clean


class ReviewStore(ABC):
    """Backend-neutral review persistence interface."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create or migrate storage."""

    @abstractmethod
    async def get_review(self, task_id: str) -> dict[str, Any]:
        """Return the complete persisted review."""

    @abstractmethod
    async def close(self) -> None:
        """Release backend resources."""


class SqlReviewStore(ReviewStore):
    """Review persistence backed by the SDK's portable ``SqlStorage``."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._storage = SqlStorage(
            is_async=False,
            db_url=db_url,
            metadata=ReviewStorageBase.metadata,
            expire_on_commit=False,
        )

    async def initialize(self) -> None:
        if self._db_url.startswith("sqlite:///") and self._db_url != "sqlite:///:memory:":
            Path(self._db_url.removeprefix("sqlite:///")).expanduser().resolve().parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        await self._storage.create_sql_engine()

    async def create_task(
        self,
        task_id: str,
        summary: InputSummary,
        model_mode: str,
        created_at: str,
    ) -> None:
        clean_summary = _clean(summary.model_dump())
        async with self._storage.create_db_session() as db:
            db.add(
                ReviewTaskRecord(
                    id=task_id,
                    status="running",
                    model_mode=model_mode,
                    input_type=summary.input_type,
                    input_sha256=summary.sha256,
                    input_summary_json=json.dumps(clean_summary, ensure_ascii=True, sort_keys=True),
                    created_at=created_at,
                    updated_at=created_at,
                    duration_ms=0,
                ))
            await _maybe_await(db.commit())

    async def add_filter_decision(self, task_id: str, decision: FilterDecision) -> None:
        clean = _clean(decision.model_dump())
        async with self._storage.create_db_session() as db:
            db.add(
                FilterDecisionRecord(
                    task_id=task_id,
                    decision=clean["decision"],
                    rule_id=clean["rule_id"],
                    reason=clean["reason"],
                    script=clean["script"],
                    network_hosts_json=json.dumps(clean["network_hosts"], ensure_ascii=True),
                ))
            await _maybe_await(db.commit())

    async def add_sandbox_run(self, task_id: str, run: SandboxRun) -> None:
        clean = _clean(run.model_dump())
        async with self._storage.create_db_session() as db:
            db.add(
                SandboxRunRecord(
                    task_id=task_id,
                    runtime=clean["runtime"],
                    status=clean["status"],
                    command=clean["command"],
                    duration_ms=clean["duration_ms"],
                    exit_code=clean["exit_code"],
                    timed_out=int(clean["timed_out"]),
                    stdout=clean["stdout"],
                    stderr=clean["stderr"],
                    output_truncated=int(clean["output_truncated"]),
                    error_type=clean["error_type"],
                    skill_loaded=int(clean["skill_loaded"]),
                ))
            await _maybe_await(db.commit())

    async def add_findings(
        self,
        task_id: str,
        findings: list[Finding],
        warnings: list[Finding],
    ) -> None:
        async with self._storage.create_db_session() as db:
            for bucket, items in (("finding", findings), ("needs_human_review", warnings)):
                for item in items:
                    clean = _clean(item.model_dump())
                    db.add(FindingRecord(task_id=task_id, bucket=bucket, **clean))
            await _maybe_await(db.commit())

    async def save_metrics(self, task_id: str, metrics: MonitoringSummary) -> None:
        clean = _clean(metrics.model_dump())
        async with self._storage.create_db_session() as db:
            db.add(
                MetricsRecord(
                    task_id=task_id,
                    metrics_json=json.dumps(clean, ensure_ascii=True, sort_keys=True),
                    finding_count=metrics.finding_count,
                    interception_count=metrics.interception_count,
                    total_duration_ms=metrics.total_duration_ms,
                    sandbox_duration_ms=metrics.sandbox_duration_ms,
                ))
            await _maybe_await(db.commit())

    async def save_report(self, report: ReviewReport) -> None:
        clean = _clean(report.model_dump())
        report_json = json.dumps(clean, ensure_ascii=True, sort_keys=True)
        async with self._storage.create_db_session() as db:
            db.add(
                ReportRecord(
                    task_id=report.task_id,
                    conclusion=clean["conclusion"],
                    report_json=report_json,
                    created_at=report.generated_at,
                ))
            await _maybe_await(db.commit())

    async def complete_task(self, task_id: str, status: str, duration_ms: int, updated_at: str) -> None:
        async with self._storage.create_db_session() as db:
            task = await _maybe_await(db.get(ReviewTaskRecord, task_id))
            if task is None:
                raise KeyError(f"review task not found: {task_id}")
            task.status = status
            task.duration_ms = duration_ms
            task.updated_at = updated_at
            await _maybe_await(db.commit())

    async def get_review(self, task_id: str) -> dict[str, Any]:
        async with self._storage.create_db_session() as db:
            task = await _maybe_await(db.get(ReviewTaskRecord, task_id))
            if task is None:
                raise KeyError(f"review task not found: {task_id}")
            sandbox_runs = (await _maybe_await(
                db.execute(select(SandboxRunRecord).where(SandboxRunRecord.task_id == task_id)
                           .order_by(SandboxRunRecord.id)))).scalars().all()
            decisions = (await _maybe_await(
                db.execute(select(FilterDecisionRecord).where(FilterDecisionRecord.task_id == task_id)
                           .order_by(FilterDecisionRecord.id)))).scalars().all()
            findings = (await _maybe_await(
                db.execute(select(FindingRecord).where(FindingRecord.task_id == task_id)
                           .order_by(FindingRecord.id)))).scalars().all()
            metrics = await _maybe_await(db.get(MetricsRecord, task_id))
            report = await _maybe_await(db.get(ReportRecord, task_id))

        return {
            "task": {
                "id": task.id,
                "status": task.status,
                "model_mode": task.model_mode,
                "input_type": task.input_type,
                "input_sha256": task.input_sha256,
                "input_summary": json.loads(task.input_summary_json),
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "duration_ms": task.duration_ms,
            },
            "sandbox_runs": [{
                "runtime": item.runtime,
                "status": item.status,
                "command": item.command,
                "duration_ms": item.duration_ms,
                "exit_code": item.exit_code,
                "timed_out": bool(item.timed_out),
                "stdout": item.stdout,
                "stderr": item.stderr,
                "output_truncated": bool(item.output_truncated),
                "error_type": item.error_type,
                "skill_loaded": bool(item.skill_loaded),
            } for item in sandbox_runs],
            "filter_decisions": [{
                "decision": item.decision,
                "rule_id": item.rule_id,
                "reason": item.reason,
                "script": item.script,
                "network_hosts": json.loads(item.network_hosts_json),
            } for item in decisions],
            "findings": [{
                "bucket": item.bucket,
                "severity": item.severity,
                "category": item.category,
                "file": item.file,
                "line": item.line,
                "title": item.title,
                "evidence": item.evidence,
                "recommendation": item.recommendation,
                "confidence": item.confidence,
                "source": item.source,
            } for item in findings],
            "metrics": json.loads(metrics.metrics_json) if metrics else None,
            "report": {
                "conclusion": report.conclusion,
                "report_json": report.report_json,
                "created_at": report.created_at,
            } if report else None,
        }

    async def close(self) -> None:
        await self._storage.close()
