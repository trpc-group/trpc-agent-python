# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Storage abstraction for the Code Review Agent (Phase 0, SDK-backed).

Defines the :class:`ReviewStore` protocol — the single persistence surface
every downstream phase (P1–P6) talks to — plus the default
:class:`SQLiteStore` implementation backed by the SDK's generic
:class:`trpc_agent_sdk.storage.SqlStorage` layer.

Design notes
------------
* The seven business tables are declared in :mod:`db.models` as a custom
  ``CRBase(DeclarativeBase)`` metadata and handed to
  ``SqlStorage(metadata=CRBase.metadata)``. The SDK storage layer manages
  table creation (``metadata.create_all``) and SQLite pragmas
  (``PRAGMA foreign_keys=ON``) automatically.
* All methods are ``async`` — the SDK storage is async-first. Downstream
  orchestration (``agent.py``) runs under ``asyncio``.
* Swapping SQLite for Postgres only requires a different ``db_url`` (e.g.
  ``postgresql+asyncpg://...``) — the protocol and ORM stay unchanged.
* Identifiers are ``uuid4().hex``; ``executemany``-style bulk inserts use
  ``session.add_all``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Protocol
from typing import runtime_checkable

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlalchemy.pool import StaticPool

from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage

from .models import CRBase
from .models import FilterBlock
from .models import Finding
from .models import InputDiff
from .models import MonitorSummary
from .models import ReviewReport
from .models import ReviewTask
from .models import SandboxRun

# monitor_summary columns that map 1:1 from the summary dict (exception_types
# is JSON-encoded separately).
_MONITOR_COLUMNS = (
    "total_duration_ms",
    "sandbox_duration_ms",
    "tool_calls",
    "blocks",
    "finding_count",
    "sev_critical",
    "sev_high",
    "sev_medium",
    "sev_low",
)


def _now_iso() -> str:
    """ISO-8601 UTC timestamp — single source for `created_at`."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """Hex UUID4 — the id format for every table row."""
    return uuid.uuid4().hex


def _to_dict(obj) -> dict:
    """Convert an ORM instance to a plain dict (column name → value)."""
    if obj is None:
        return None
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _build_db_url(db_path: str) -> tuple[str, dict]:
    """Return (db_url, extra_kwargs) for SqlStorage.

    ``:memory:`` needs ``StaticPool`` so every connection shares one DB
    (async sqlite otherwise gives each connection its own private memory DB).
    """
    if db_path == ":memory:":
        return (
            "sqlite+aiosqlite://",
            {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}},
        )
    return f"sqlite+aiosqlite:///{db_path}", {}


@runtime_checkable
class ReviewStore(Protocol):
    """Async persistence surface for one code-review pipeline run.

    All seven tables are written/read through this protocol. ``get_task``
    returns the fully joined record so a single call reconstructs an
    entire review (task + inputs + sandbox runs + findings + filter blocks
    + telemetry + report).
    """

    # —— task lifecycle ——
    async def create_task(self, input_type: str, input_ref: str, mode: str) -> str: ...
    async def update_task_status(
        self, task_id: str, status: str, total_duration_ms: int | None = None
    ) -> None: ...

    # —— child-table writes (each returns the new row id) ——
    async def add_input_diff(
        self, task_id, file_path, sha256, hunk_count, line_count, summary
    ) -> str: ...
    async def add_sandbox_run(
        self, task_id, runtime, script, status, duration_ms, exit_code,
        output_bytes, timed_out, masked_count,
    ) -> str: ...
    async def add_finding(
        self, task_id, severity, category, file, line, title, evidence,
        recommendation, confidence, source, bucket,
    ) -> str: ...
    async def add_filter_block(
        self, task_id, reason, target, decision, detail
    ) -> str: ...
    async def set_monitor_summary(self, task_id: str, summary: dict) -> None: ...
    async def set_report(
        self, task_id: str, json_path: str, md_path: str, summary: str
    ) -> str: ...

    # —— query ——
    async def get_task(self, task_id: str) -> dict: ...


class SQLiteStore:
    """Async :class:`ReviewStore` over a single SQLite file via SDK SqlStorage.

    Pass ``db_path=":memory:"`` for an ephemeral in-memory DB (tests).
    """

    def __init__(self, db_path: str | Path = "cr_agent.db"):
        self.db_path = str(db_path)
        self._db_url, self._extra_kwargs = _build_db_url(self.db_path)
        self._storage = SqlStorage(
            is_async=True,
            db_url=self._db_url,
            metadata=CRBase.metadata,
            **self._extra_kwargs,
        )
        self._engine_ready = False

    # ------------------------------------------------------------------ #
    # engine / lifecycle
    # ------------------------------------------------------------------ #
    async def _ensure_engine(self) -> None:
        """Create the async engine + tables (idempotent).

        Also explicitly enables ``PRAGMA foreign_keys=ON``: the SDK's
        connect-event hook doesn't always fire for ``:memory:`` + StaticPool,
        so we set it on the pooled connection to be safe.
        """
        if not self._engine_ready:
            await self._storage.create_sql_engine()
            engine = self._storage._db_engine
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA foreign_keys=ON"))
            self._engine_ready = True

    @property
    def storage(self) -> SqlStorage:
        """Underlying SDK storage (for advanced callers)."""
        return self._storage

    async def close(self) -> None:
        await self._storage.close()
        self._engine_ready = False

    async def __aenter__(self) -> "SQLiteStore":
        await self._ensure_engine()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _session(self):
        """An async session context manager (``async with store._session() as db``)."""
        return self._storage.create_db_session()

    @staticmethod
    def _query_all(db, model, task_id: str, order_col=None):
        """Select all rows of ``model`` for ``task_id``, ordered."""
        stmt = select(model).where(model.task_id == task_id)
        if order_col is not None:
            stmt = stmt.order_by(order_col)
        return db.execute(stmt).scalars().all()

    # ------------------------------------------------------------------ #
    # task lifecycle
    # ------------------------------------------------------------------ #
    async def create_task(self, input_type: str, input_ref: str, mode: str) -> str:
        await self._ensure_engine()
        task_id = _new_id()
        task = ReviewTask(
            id=task_id,
            created_at=_now_iso(),
            status="pending",
            input_type=input_type,
            input_ref=input_ref,
            mode=mode,
        )
        async with self._session() as db:
            await self._storage.add(db, task)
            await self._storage.commit(db)
        return task_id

    async def update_task_status(
        self, task_id: str, status: str, total_duration_ms: int | None = None
    ) -> None:
        await self._ensure_engine()
        async with self._session() as db:
            await db.execute(
                sa_update(ReviewTask)
                .where(ReviewTask.id == task_id)
                .values(status=status, total_duration_ms=total_duration_ms)
            )
            await self._storage.commit(db)

    # ------------------------------------------------------------------ #
    # child-table writes
    # ------------------------------------------------------------------ #
    async def add_input_diff(
        self, task_id, file_path, sha256, hunk_count, line_count, summary
    ) -> str:
        await self._ensure_engine()
        diff_id = _new_id()
        obj = InputDiff(
            id=diff_id, task_id=task_id, file_path=file_path, sha256=sha256,
            hunk_count=hunk_count, line_count=line_count, summary=summary,
        )
        async with self._session() as db:
            await self._storage.add(db, obj)
            await self._storage.commit(db)
        return diff_id

    async def add_sandbox_run(
        self, task_id, runtime, script, status, duration_ms, exit_code,
        output_bytes, timed_out, masked_count,
    ) -> str:
        await self._ensure_engine()
        run_id = _new_id()
        obj = SandboxRun(
            id=run_id, task_id=task_id, runtime=runtime, script=script,
            status=status, duration_ms=duration_ms, exit_code=exit_code,
            output_bytes=output_bytes, timed_out=timed_out, masked_count=masked_count,
        )
        async with self._session() as db:
            await self._storage.add(db, obj)
            await self._storage.commit(db)
        return run_id

    async def add_finding(
        self, task_id, severity, category, file, line, title, evidence,
        recommendation, confidence, source, bucket,
    ) -> str:
        await self._ensure_engine()
        finding_id = _new_id()
        obj = Finding(
            id=finding_id, task_id=task_id, severity=severity, category=category,
            file=file, line=line, title=title, evidence=evidence,
            recommendation=recommendation, confidence=confidence,
            source=source, bucket=bucket,
        )
        async with self._session() as db:
            await self._storage.add(db, obj)
            await self._storage.commit(db)
        return finding_id

    async def add_findings(self, task_id: str, findings: list[dict]) -> list[str]:
        """Bulk-insert findings via ``session.add_all`` — one round-trip.

        A review can emit hundreds of findings, so this is preferred over
        looping :meth:`add_finding`.
        """
        if not findings:
            return []
        await self._ensure_engine()
        ids = [_new_id() for _ in findings]
        rows = [
            Finding(
                id=fid, task_id=task_id,
                severity=f["severity"], category=f["category"], file=f["file"],
                line=f["line"], title=f["title"], evidence=f["evidence"],
                recommendation=f["recommendation"], confidence=f["confidence"],
                source=f["source"], bucket=f["bucket"],
            )
            for fid, f in zip(ids, findings)
        ]
        async with self._session() as db:
            db.add_all(rows)
            await self._storage.commit(db)
        return ids

    async def add_filter_block(
        self, task_id, reason, target, decision, detail
    ) -> str:
        await self._ensure_engine()
        block_id = _new_id()
        obj = FilterBlock(
            id=block_id, task_id=task_id, reason=reason, target=target,
            decision=decision, detail=detail,
        )
        async with self._session() as db:
            await self._storage.add(db, obj)
            await self._storage.commit(db)
        return block_id

    async def set_monitor_summary(self, task_id: str, summary: dict) -> None:
        """Upsert the 1:1 telemetry rollup row for a task."""
        await self._ensure_engine()
        exc = summary.get("exception_types")
        if isinstance(exc, dict):
            exc = json.dumps(exc, ensure_ascii=False)
        async with self._session() as db:
            # 1:1 relationship — delete any existing row first.
            await db.execute(
                sa_delete(MonitorSummary).where(MonitorSummary.task_id == task_id)
            )
            summary_id = _new_id()
            values = {col: summary.get(col) for col in _MONITOR_COLUMNS}
            obj = MonitorSummary(id=summary_id, task_id=task_id, exception_types=exc, **values)
            await self._storage.add(db, obj)
            await self._storage.commit(db)

    async def set_report(
        self, task_id: str, json_path: str, md_path: str, summary: str
    ) -> str:
        """Upsert the 1:1 report row for a task, return its id."""
        await self._ensure_engine()
        async with self._session() as db:
            existing = await db.execute(
                select(ReviewReport).where(ReviewReport.task_id == task_id)
            )
            row = existing.scalars().one_or_none()
            if row is not None:
                row.report_json_path = json_path
                row.report_md_path = md_path
                row.summary = summary
                row.created_at = _now_iso()
                report_id = row.id
            else:
                report_id = _new_id()
                obj = ReviewReport(
                    id=report_id, task_id=task_id, report_json_path=json_path,
                    report_md_path=md_path, summary=summary, created_at=_now_iso(),
                )
                await self._storage.add(db, obj)
            await self._storage.commit(db)
        return report_id

    # ------------------------------------------------------------------ #
    # query
    # ------------------------------------------------------------------ #
    async def get_task(self, task_id: str) -> dict:
        """Reconstruct the full review record via per-table queries.

        One session, one query per child table — keeps the result a clean
        nested dict with stable ordering.
        """
        await self._ensure_engine()
        async with self._session() as db:
            task_row = await db.execute(
                select(ReviewTask).where(ReviewTask.id == task_id)
            )
            task = task_row.scalars().one_or_none()
            if task is None:
                raise KeyError(f"review_task not found: {task_id}")

            input_diffs = (await db.execute(
                select(InputDiff).where(InputDiff.task_id == task_id).order_by(InputDiff.id)
            )).scalars().all()
            sandbox_runs = (await db.execute(
                select(SandboxRun).where(SandboxRun.task_id == task_id).order_by(SandboxRun.id)
            )).scalars().all()
            findings = (await db.execute(
                select(Finding).where(Finding.task_id == task_id).order_by(Finding.id)
            )).scalars().all()
            filter_blocks = (await db.execute(
                select(FilterBlock).where(FilterBlock.task_id == task_id).order_by(FilterBlock.id)
            )).scalars().all()
            ms_row = await db.execute(
                select(MonitorSummary).where(MonitorSummary.task_id == task_id)
            )
            monitor_summary = ms_row.scalars().one_or_none()
            rep_row = await db.execute(
                select(ReviewReport).where(ReviewReport.task_id == task_id)
            )
            report = rep_row.scalars().one_or_none()

        return {
            "task": _to_dict(task),
            "input_diffs": [_to_dict(x) for x in input_diffs],
            "sandbox_runs": [_to_dict(x) for x in sandbox_runs],
            "findings": [_to_dict(x) for x in findings],
            "filter_blocks": [_to_dict(x) for x in filter_blocks],
            "monitor_summary": _to_dict(monitor_summary),
            "report": _to_dict(report),
        }
