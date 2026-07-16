# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ReviewStore: persistence built on the SDK's SqlStorage (SQLite by default,
any SQLAlchemy db_url works)."""
import uuid
from datetime import datetime

from trpc_agent_sdk.storage import SqlCondition, SqlKey, SqlStorage

from review.redaction import redact_text

from .models import (CrBase, FilterEventRow, FindingRow, MetricsRow, ReportRow,
                     ReviewTaskRow, SandboxRunRow)


def _row_to_dict(row):
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


class ReviewStore:
    """CRUD facade over the code-review tables. All write paths redact secrets."""

    def __init__(self, db_url: str = "sqlite:///code_review.db"):
        self._storage = SqlStorage(is_async=False, db_url=db_url, metadata=CrBase.metadata)

    async def _add(self, row) -> None:
        async with self._storage.create_db_session() as db:
            await self._storage.add(db, row)
            await self._storage.commit(db)

    async def create_task(self, input_type: str, input_ref: str, runtime: str,
                          dry_run: bool) -> str:
        row = ReviewTaskRow(input_type=input_type, input_ref=input_ref,
                            runtime=runtime, dry_run=dry_run, status="running")
        task_id = row.id or ""
        if not task_id:
            task_id = uuid.uuid4().hex
            row.id = task_id
        await self._add(row)
        return task_id

    async def update_task(self, task_id: str, status: str = None,
                          diff_summary: dict = None, finished: bool = False) -> None:
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(db, SqlKey(key=(task_id,), storage_cls=ReviewTaskRow))
            if row is None:
                return
            if status is not None:
                row.status = status
            if diff_summary is not None:
                row.diff_summary = diff_summary
            if finished:
                row.finished_at = datetime.now()
            await self._storage.commit(db)

    async def add_sandbox_run(self, task_id: str, *, script: str, category: str,
                              status: str, exit_code: int, duration_ms: int,
                              timed_out: bool, stdout_summary: str,
                              stderr_summary: str, error_type: str) -> None:
        await self._add(SandboxRunRow(
            task_id=task_id, script=script, category=category, status=status,
            exit_code=exit_code, duration_ms=duration_ms, timed_out=timed_out,
            stdout_summary=redact_text(stdout_summary),
            stderr_summary=redact_text(stderr_summary), error_type=error_type))

    async def add_findings(self, task_id: str, findings, status: str) -> None:
        for f in findings:
            await self._add(FindingRow(
                task_id=task_id, severity=f.severity, category=f.category,
                file=f.file, line=f.line, title=f.title,
                evidence=redact_text(f.evidence), recommendation=f.recommendation,
                confidence=f.confidence, source=f.source, status=status,
                dedup_key=f.dedup_key))

    async def add_filter_event(self, task_id: str, target: str, decision: str,
                               rule: str, reason: str) -> None:
        await self._add(FilterEventRow(task_id=task_id, target=redact_text(target),
                                       decision=decision, rule=rule, reason=reason))

    async def add_metrics(self, task_id: str, metrics: dict) -> None:
        await self._add(MetricsRow(
            task_id=task_id,
            total_duration_ms=metrics.get("total_duration_ms", 0),
            sandbox_duration_ms=metrics.get("sandbox_duration_ms", 0),
            tool_calls=metrics.get("tool_calls", 0),
            intercepts=metrics.get("intercepts", 0),
            findings_total=metrics.get("findings_total", 0),
            severity_distribution=metrics.get("severity_distribution", {}),
            error_distribution=metrics.get("error_distribution", {})))

    async def add_report(self, task_id: str, report_json: dict, report_md: str) -> None:
        await self._add(ReportRow(task_id=task_id, report_json=report_json,
                                  report_md=redact_text(report_md)))

    async def _query_all(self, db, storage_cls, task_id):
        rows = await self._storage.query(
            db, SqlKey(key=(), storage_cls=storage_cls),
            SqlCondition(filters=[storage_cls.task_id == task_id]))
        return [_row_to_dict(r) for r in rows]

    async def get_task_bundle(self, task_id: str) -> dict:
        async with self._storage.create_db_session() as db:
            task = await self._storage.get(db, SqlKey(key=(task_id,), storage_cls=ReviewTaskRow))
            runs = await self._query_all(db, SandboxRunRow, task_id)
            findings = await self._query_all(db, FindingRow, task_id)
            events = await self._query_all(db, FilterEventRow, task_id)
            metrics_rows = await self._query_all(db, MetricsRow, task_id)
            report_rows = await self._query_all(db, ReportRow, task_id)
        return {
            "task": _row_to_dict(task),
            "sandbox_runs": runs,
            "findings": findings,
            "filter_events": events,
            "metrics": metrics_rows[0] if metrics_rows else None,
            "report": report_rows[0] if report_rows else None,
        }

    async def close(self) -> None:
        await self._storage.close()
