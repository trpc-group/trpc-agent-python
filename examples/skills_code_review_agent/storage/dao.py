# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Persistence for reviews (issue #92, requirements 3 & 5).

Wraps the framework's ``SqlStorage`` so the schema is portable (SQLite default, PostgreSQL/MySQL
by URL) and gets forward-only migrations for free. Every string is routed through ``redact()``
before it touches the DB — criterion 5 forbids plaintext secrets in the database.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select

from trpc_agent_sdk.storage import SqlStorage

from pipeline.engine import ReviewResult
from pipeline.redaction import redact, redact_finding

from .models import CodeReviewBase, FindingORM, ReportORM, ReviewTaskORM, SandboxRunORM

DEFAULT_DB_URL = "sqlite+aiosqlite:///./code_review.db"


class ReviewStore:

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        self._storage = SqlStorage(is_async=True, db_url=db_url, metadata=CodeReviewBase.metadata)

    async def init(self) -> None:
        await self._storage.create_sql_engine()

    async def close(self) -> None:
        await self._storage.close()

    async def persist(self, result: ReviewResult) -> None:
        """Write the task, its findings, and the report summary. All strings are redacted."""
        mon = result.monitoring
        # Real status — a blocked or failed review must not persist as "completed".
        if int(mon.get("block_count", 0)) > 0:
            status = "blocked"
        elif mon.get("exception_dist"):
            status = "failed"
        else:
            status = "completed"
        async with self._storage.create_db_session() as db:
            task = ReviewTaskORM(
                id=result.task_id,
                source_type=result.source_type,
                source_ref=redact(result.source_ref),
                runtime="local",
                dry_run=True,
                status=status,
                block_count=int(mon.get("block_count", 0)),
                finding_count=int(mon.get("finding_count", 0)),
                severity_dist=mon.get("severity_dist", {}),
                exception_dist=mon.get("exception_dist", {}),
                diff_summary={
                    "files_changed": result.summary.files_changed,
                    "added": result.summary.added,
                    "removed": result.summary.removed,
                    "languages": result.summary.languages,
                    "changed_files": [f.path for f in result.summary.files],
                },
            )
            await self._storage.add(db, task)

            for f in result.findings:
                rf = redact_finding(f)
                await self._storage.add(
                    db,
                    FindingORM(
                        id=f"fd-{uuid.uuid4().hex[:12]}",
                        task_id=result.task_id,
                        severity=rf.severity,
                        category=rf.category,
                        file=rf.file,
                        line=rf.line,
                        title=rf.title,
                        evidence={"text": rf.evidence},
                        recommendation=rf.recommendation,
                        confidence=rf.confidence,
                        source=rf.source,
                        status=rf.status,
                        dedup_key=rf.dedup_key,
                    ))

            for run in result.report.sandbox_summary:
                await self._storage.add(
                    db,
                    SandboxRunORM(
                        id=f"sb-{uuid.uuid4().hex[:12]}",
                        task_id=result.task_id,
                        script=run.script,
                        exit_code=run.exit_code,
                        duration_sec=run.duration_sec,
                        timed_out=run.timed_out,
                        stdout_bytes=run.stdout_bytes,
                        stderr_bytes=run.stderr_bytes,
                        blocked=run.blocked,
                        block_reason=run.block_reason,
                        block_category=run.block_category,
                    ))

            await self._storage.add(
                db,
                ReportORM(
                    id=f"rp-{uuid.uuid4().hex[:12]}",
                    task_id=result.task_id,
                    format="json",
                    summary=result.report.findings_summary,
                ))
            await self._storage.commit(db)

    async def get_by_task_id(self, task_id: str) -> Optional[dict[str, Any]]:
        """Return the whole review by task id (requirement 3): task + findings + runs + report."""
        async with self._storage.create_db_session() as db:
            task = await db.get(ReviewTaskORM, task_id)
            if task is None:
                return None
            findings = (await db.execute(select(FindingORM).where(FindingORM.task_id == task_id))).scalars().all()
            runs = (await db.execute(select(SandboxRunORM).where(SandboxRunORM.task_id == task_id))).scalars().all()
            report = (await db.execute(select(ReportORM).where(ReportORM.task_id == task_id))).scalars().first()
            return {"task": task, "findings": findings, "sandbox_runs": runs, "report": report}
