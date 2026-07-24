# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQL implementation of :class:`ReviewStore` on trpc_agent_sdk.storage.SqlStorage.

Default URL: ``sqlite+aiosqlite:///<dir>/review.db``. Because every column
uses the SDK's portable types, pointing ``db_url`` at
``mysql+aiomysql://...`` or ``postgresql+asyncpg://...`` is the entire
backend swap — no code change.
"""

from __future__ import annotations

import uuid
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from sqlalchemy.inspection import inspect as sa_inspect

from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage

from .base import ReviewStore
from .models import FilterEventRow
from .models import FindingRow
from .models import ReportRow
from .models import ReviewStorageBase
from .models import ReviewTaskRow
from .models import SandboxRunRow


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """ORM row → plain dict (datetime → isoformat)."""
    data: Dict[str, Any] = {}
    for column in sa_inspect(row).mapper.column_attrs:
        value = getattr(row, column.key)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[column.key] = value
    return data


class SqlReviewStore(ReviewStore):
    """ReviewStore backed by any SQLAlchemy async URL (SQLite default)."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._storage = SqlStorage(is_async=True, db_url=db_url, metadata=ReviewStorageBase.metadata)

    @property
    def db_url(self) -> str:
        return self._db_url

    async def initialize(self) -> None:
        # create_sql_engine runs metadata.create_all plus the SDK's
        # forward-only column migration — this IS the init/migration script.
        await self._storage.create_sql_engine()

    async def close(self) -> None:
        await self._storage.close()

    # -- writes -------------------------------------------------------------

    async def _add_and_commit(self, obj: Any) -> None:
        async with self._storage.create_db_session() as session:
            await self._storage.add(session, obj)
            await self._storage.commit(session)

    async def create_task(self, task_id: str, input_type: str, input_ref: str,
                          config: Dict[str, Any]) -> None:
        await self._add_and_commit(
            ReviewTaskRow(id=task_id, status="pending", input_type=input_type,
                          input_ref=input_ref, config=config))

    async def update_task(self, task_id: str, **fields: Any) -> None:
        async with self._storage.create_db_session() as session:
            row = await self._storage.get(session, SqlKey(key=(task_id,), storage_cls=ReviewTaskRow))
            if row is None:
                raise KeyError(f"review task not found: {task_id}")
            for key, value in fields.items():
                if not hasattr(row, key):
                    raise AttributeError(f"cr_review_task has no column {key!r}")
                setattr(row, key, value)
            await self._storage.commit(session)

    async def add_sandbox_run(self, run: Dict[str, Any]) -> None:
        run.setdefault("id", uuid.uuid4().hex)
        await self._add_and_commit(SandboxRunRow(**run))

    async def add_filter_event(self, event: Dict[str, Any]) -> None:
        event.setdefault("id", uuid.uuid4().hex)
        await self._add_and_commit(FilterEventRow(**event))

    async def add_findings(self, task_id: str, findings: List[Dict[str, Any]]) -> None:
        if not findings:
            return
        async with self._storage.create_db_session() as session:
            for finding in findings:
                finding = dict(finding)
                finding.setdefault("id", uuid.uuid4().hex)
                finding["task_id"] = task_id
                await self._storage.add(session, FindingRow(**finding))
            await self._storage.commit(session)

    async def save_report(self, task_id: str, report_row: Dict[str, Any]) -> None:
        report_row = dict(report_row)
        report_row.setdefault("id", uuid.uuid4().hex)
        report_row["task_id"] = task_id
        await self._add_and_commit(ReportRow(**report_row))

    # -- queries ------------------------------------------------------------

    async def _query_by_task(self, storage_cls: Any, task_id: str,
                             order_col: str = "created_at") -> List[Dict[str, Any]]:
        async with self._storage.create_db_session() as session:
            rows = await self._storage.query(
                session,
                SqlKey(key=(task_id,), storage_cls=storage_cls),
                SqlCondition(filters=[storage_cls.task_id == task_id],
                             order_func=lambda: getattr(storage_cls, order_col).asc()),
            )
            return [_row_to_dict(row) for row in rows]

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._storage.create_db_session() as session:
            row = await self._storage.get(session, SqlKey(key=(task_id,), storage_cls=ReviewTaskRow))
            return _row_to_dict(row) if row else None

    async def get_sandbox_runs(self, task_id: str) -> List[Dict[str, Any]]:
        return await self._query_by_task(SandboxRunRow, task_id, order_col="started_at")

    async def get_filter_events(self, task_id: str) -> List[Dict[str, Any]]:
        return await self._query_by_task(FilterEventRow, task_id)

    async def get_findings(self, task_id: str) -> List[Dict[str, Any]]:
        return await self._query_by_task(FindingRow, task_id)

    async def get_report(self, task_id: str) -> Optional[Dict[str, Any]]:
        rows = await self._query_by_task(ReportRow, task_id)
        return rows[0] if rows else None

    async def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with self._storage.create_db_session() as session:
            rows = await self._storage.query(
                session,
                SqlKey(key=(), storage_cls=ReviewTaskRow),
                SqlCondition(order_func=lambda: ReviewTaskRow.created_at.desc(), limit=limit),
            )
            return [_row_to_dict(row) for row in rows]
