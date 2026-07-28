"""Execution-trace persistence for the code-review agent."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    Index,
    select,
)

from .models import ReviewReport, utc_now
from .reporting import render_markdown
from .sanitizer import redact_sensitive_text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ReviewRepository:
    """Store a complete, queryable review trace.

    One transaction commits the final skill executions, policy decisions,
    sandbox runs, findings, report and telemetry.  A failed transaction leaves
    the already-created task in ``running`` so the caller can mark it failed.
    """

    def __init__(self, db_url: str = "sqlite:///code_review.db") -> None:
        self.engine = create_engine(db_url)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.metadata = MetaData()
        self.tasks = Table(
            "review_task",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("repo_path", Text),
            Column("commit_hash", String(128)),
            Column("input_type", String(32), nullable=False),
            Column("input_digest", String(64), nullable=False),
            Column("diff_summary", Text, nullable=False),
            Column("status", String(32), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("started_at", DateTime(timezone=True)),
            Column("finished_at", DateTime(timezone=True)),
            Column("error_type", String(128)),
        )
        self.skill_executions = Table(
            "skill_execution",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), nullable=False),
            Column("skill_name", String(128), nullable=False),
            Column("skill_version", String(64), nullable=False),
            Column("rule_version", String(64), nullable=False),
            Column("detector_type", String(64), nullable=False),
            Column("started_at", DateTime(timezone=True), nullable=False),
            Column("finished_at", DateTime(timezone=True), nullable=False),
            Column("result_summary", Text, nullable=False),
        )
        self.runs = Table(
            "sandbox_run",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), nullable=False),
            Column("runtime", String(64), nullable=False),
            Column("runtime_version", String(128), nullable=False),
            Column("image", Text),
            Column("command", Text, nullable=False),
            Column("command_digest", String(64), nullable=False),
            Column("status", String(32), nullable=False),
            Column("exit_code", Integer),
            Column("timed_out", Boolean, nullable=False),
            Column("stdout", Text, nullable=False),
            Column("stderr", Text, nullable=False),
            Column("duration", Float, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self.decisions = Table(
            "filter_event",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), nullable=False),
            Column("sandbox_run_id", ForeignKey("sandbox_run.id")),
            Column("action", String(64), nullable=False),
            Column("command", Text, nullable=False),
            Column("command_digest", String(64), nullable=False),
            Column("decision", String(32), nullable=False),
            Column("risk_level", String(16), nullable=False),
            Column("reason", Text, nullable=False),
            Column("reason_code", String(64), nullable=False),
            Column("matched_rule", String(128), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self.findings = Table(
            "finding",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), nullable=False),
            Column("skill_execution_id", ForeignKey("skill_execution.id")),
            Column("sandbox_run_id", ForeignKey("sandbox_run.id")),
            Column("severity", String(16), nullable=False),
            Column("category", String(32), nullable=False),
            Column("file", Text, nullable=False),
            Column("line", Integer, nullable=False),
            Column("title", Text, nullable=False),
            Column("evidence", Text, nullable=False),
            Column("recommendation", Text, nullable=False),
            Column("confidence", Float, nullable=False),
            Column("source", String(64), nullable=False),
            Column("status", String(32), nullable=False),
            Column("needs_human_review", Boolean, nullable=False),
            Column("dedupe_key", String(64), nullable=False),
            Column("rule_id", String(128), nullable=False, default=""),
            Column("rule_version", String(64), nullable=False, default="1"),
            Column("validation_status", String(32), nullable=False, default="not_run"),
            Column("created_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint(
                "task_id", "file", "line", "category",
                name="uq_finding_location_category",
            ),
        )
        self.reports = Table(
            "review_report",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), unique=True, nullable=False),
            Column("summary", Text, nullable=False),
            Column("report_json", Text, nullable=False),
            Column("report_markdown", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self.telemetry = Table(
            "telemetry",
            self.metadata,
            Column("id", String(64), primary_key=True),
            Column("task_id", ForeignKey("review_task.id"), unique=True, nullable=False),
            Column("total_duration", Float, nullable=False),
            Column("sandbox_duration", Float, nullable=False),
            Column("tool_calls", Integer, nullable=False),
            Column("filter_blocks", Integer, nullable=False),
            Column("finding_count", Integer, nullable=False),
            Column("error_count", Integer, nullable=False),
            Column("severity_distribution", Text, nullable=False),
            Column("error_distribution", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        Index("ix_review_task_status", self.tasks.c.status)
        Index("ix_review_task_created_at", self.tasks.c.created_at)
        Index("ix_finding_task_id", self.findings.c.task_id)
        Index("ix_finding_severity", self.findings.c.severity)

    def initialize(self) -> None:
        self.metadata.create_all(self.engine)

    def create_task(
        self,
        task_id: str,
        *,
        input_type: str,
        input_digest: str,
        summary: str,
        repo_path: str | None = None,
        commit_hash: str | None = None,
    ) -> None:
        now = utc_now()
        with self.engine.begin() as connection:
            connection.execute(
                self.tasks.insert().values(
                    id=task_id,
                    repo_path=repo_path,
                    commit_hash=commit_hash,
                    input_type=input_type,
                    input_digest=input_digest,
                    diff_summary=summary,
                    status="running",
                    created_at=now,
                    started_at=now,
                )
            )

    def mark_failed(self, task_id: str, error_type: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.tasks.update().where(self.tasks.c.id == task_id).values(
                    status="failed", error_type=error_type, finished_at=utc_now()
                )
            )

    def save_review_result(
        self,
        report: ReviewReport,
        *,
        skill_version: str = "1",
        rule_version: str = "1",
    ) -> None:
        """Atomically persist every final entity in a review trace."""
        payload = report.model_dump(mode="json")
        now = utc_now()
        detector_names = sorted({item.source for item in report.findings}) or ["rule"]
        skill_id = _id("skill")
        with self.engine.begin() as connection:
            connection.execute(
                self.tasks.update().where(self.tasks.c.id == report.task_id).values(
                    status=report.status, finished_at=now
                )
            )
            connection.execute(
                self.skill_executions.insert().values(
                    id=skill_id,
                    task_id=report.task_id,
                    skill_name="code-review",
                    skill_version=skill_version,
                    rule_version=rule_version,
                    detector_type=",".join(detector_names),
                    started_at=now,
                    finished_at=now,
                    result_summary=_json(
                        {"findings": len(report.findings), "warnings": len(report.warnings)}
                    ),
                )
            )
            for run in report.sandbox_runs:
                command = redact_sensitive_text(_json(run.command))
                connection.execute(
                    self.runs.insert().values(
                        id=run.id,
                        task_id=report.task_id,
                        runtime=run.runtime,
                        runtime_version="unknown",
                        image=None,
                        command=command,
                        command_digest=hashlib.sha256(
                            "\0".join(run.command).encode()
                        ).hexdigest(),
                        status=run.status,
                        exit_code=run.exit_code,
                        timed_out=run.timed_out,
                        duration=run.duration_ms / 1000,
                        stdout=run.stdout_summary,
                        stderr=run.stderr_summary,
                        created_at=now,
                    )
                )
            for index, decision in enumerate(report.filter_decisions):
                run = report.sandbox_runs[index] if index < len(report.sandbox_runs) else None
                connection.execute(
                    self.decisions.insert().values(
                        id=_id("filter"),
                        task_id=report.task_id,
                        sandbox_run_id=run.id if run else None,
                        action="sandbox_execute",
                        command=redact_sensitive_text(_json(run.command)) if run else "[]",
                        command_digest=decision.command_digest,
                        decision=decision.decision.value,
                        risk_level=decision.risk_level,
                        reason=decision.reason,
                        reason_code=decision.reason_code,
                        matched_rule=decision.matched_rule or decision.reason_code,
                        created_at=decision.created_at,
                    )
                )
            all_findings = [*report.findings, *report.needs_human_review]
            first_run_id = report.sandbox_runs[0].id if report.sandbox_runs else None
            for finding in all_findings:
                values = finding.model_dump()
                values.update(
                    id=_id("finding"),
                    task_id=report.task_id,
                    skill_execution_id=skill_id,
                    sandbox_run_id=first_run_id,
                    status="needs_human_review" if finding.needs_human_review else "open",
                    created_at=now,
                )
                existing = connection.execute(
                    select(self.findings.c.id, self.findings.c.confidence).where(
                        self.findings.c.task_id == report.task_id,
                        self.findings.c.file == finding.file,
                        self.findings.c.line == finding.line,
                        self.findings.c.category == finding.category,
                    )
                ).first()
                if existing and existing.confidence < finding.confidence:
                    values.pop("id")
                    connection.execute(
                        self.findings.update()
                        .where(self.findings.c.id == existing.id)
                        .values(**values)
                    )
                elif not existing:
                    connection.execute(self.findings.insert().values(**values))
            severity = report.metrics.get("finding_severity", {})
            errors = report.metrics.get("errors", {})
            summary = {
                "conclusion": report.conclusion,
                "severity": severity,
                "status": report.status,
            }
            connection.execute(
                self.reports.insert().values(
                    id=_id("report"),
                    task_id=report.task_id,
                    summary=_json(summary),
                    report_json=_json(payload),
                    report_markdown=render_markdown(report),
                    created_at=report.created_at,
                )
            )
            stage_ms = report.metrics.get("stage_duration_ms", {})
            connection.execute(
                self.telemetry.insert().values(
                    id=_id("telemetry"),
                    task_id=report.task_id,
                    total_duration=float(report.metrics.get("total_duration_ms", 0)) / 1000,
                    sandbox_duration=float(stage_ms.get("sandbox", 0)) / 1000,
                    tool_calls=int(report.metrics.get("tool_calls", 0)),
                    filter_blocks=int(report.metrics.get("blocked_executions", 0)),
                    finding_count=len(all_findings),
                    error_count=sum(int(value) for value in errors.values()),
                    severity_distribution=_json(severity),
                    error_distribution=_json(errors),
                    created_at=now,
                )
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return the task and all child trace records."""
        with self.engine.connect() as connection:
            task = connection.execute(
                select(self.tasks).where(self.tasks.c.id == task_id)
            ).mappings().first()
            if task is None:
                return None
            result = dict(task)
            children = {
                "skill_executions": self.skill_executions,
                "sandbox_runs": self.runs,
                "filter_decisions": self.decisions,
                "findings": self.findings,
            }
            for key, table in children.items():
                result[key] = [
                    dict(row)
                    for row in connection.execute(
                        select(table).where(table.c.task_id == task_id)
                    ).mappings()
                ]
            for key, table in (
                ("report", self.reports),
                ("telemetry", self.telemetry),
            ):
                row = connection.execute(
                    select(table).where(table.c.task_id == task_id)
                ).mappings().first()
                result[key] = dict(row) if row else None
            return result
