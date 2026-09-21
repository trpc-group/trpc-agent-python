# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQL persistence for generated CodeAct implementations."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from datetime import timezone
from typing import Any
from typing_extensions import override

from sqlalchemy import String, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import mapped_column

from trpc_agent_sdk.storage import PreciseTimestamp
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage

from ._base import BaseCodeActImplementationStore
from ._base import CodeActImplementation


class CodeActImplementationSqlBase(DeclarativeBase):
    """Metadata owned exclusively by CodeAct implementation stores."""


class _CodeActImplementationRow(CodeActImplementationSqlBase):
    __tablename__ = "trpc_agent_codeact_impl"

    tool_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(PreciseTimestamp)


class _CodeActApprovalRow(CodeActImplementationSqlBase):
    __tablename__ = "trpc_agent_codeact_impl_approvals"

    tool_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))
    approved_at: Mapped[datetime] = mapped_column(PreciseTimestamp)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)


class SqlCodeActImplementationStore(BaseCodeActImplementationStore):
    """Persist CodeAct versions through the shared :class:`SqlStorage`."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        storage: SqlStorage | None = None,
        is_async: bool = False,
        **storage_kwargs: Any,
    ) -> None:
        if storage is None and db_url is None:
            raise ValueError("SQL CodeAct storage requires db_url or storage")
        self._storage = storage or SqlStorage(
            is_async=is_async,
            db_url=db_url or "",
            metadata=CodeActImplementationSqlBase.metadata,
            expire_on_commit=False,
            **storage_kwargs,
        )
        self._owns_storage = storage is None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    @override
    async def save_candidate(
        self,
        implementation: CodeActImplementation,
    ) -> CodeActImplementation:
        await self._ensure_initialized()
        key = self._implementation_key(
            implementation.tool_name,
            implementation.contract_hash,
            implementation.version,
        )
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(db, key)
            if row is not None:
                stored = CodeActImplementation.model_validate_json(row.payload)
                if stored.code_hash != implementation.code_hash:
                    raise ValueError(f"CodeAct version collision for {implementation.version!r}")
                return stored
            await self._storage.add(
                db,
                _CodeActImplementationRow(
                    tool_hash=key.key[0],
                    contract_hash=implementation.contract_hash,
                    version=implementation.version,
                    payload=implementation.model_dump_json(),
                    created_at=self._naive_utc(implementation.created_at),
                ),
            )
            try:
                await self._storage.commit(db)
            except IntegrityError:
                # Another worker may have inserted the same content-addressed
                # version concurrently. Verify that winner before returning.
                pass
            else:
                return implementation
        async with self._storage.create_db_session() as db:
            row = await self._storage.get(db, key)
            if row is None:
                raise RuntimeError("SQL CodeAct candidate write did not persist")
            stored = CodeActImplementation.model_validate_json(row.payload)
            if stored.code_hash != implementation.code_hash:
                raise ValueError(f"CodeAct version collision for {implementation.version!r}")
            return stored

    @override
    async def list_implementations(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> list[CodeActImplementation]:
        await self._ensure_initialized()
        tool_hash = self._tool_hash(tool_name)
        async with self._storage.create_db_session() as db:
            rows = await self._storage.query(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash),
                    storage_cls=_CodeActImplementationRow,
                ),
                SqlCondition(
                    filters=[
                        _CodeActImplementationRow.tool_hash == tool_hash,
                        _CodeActImplementationRow.contract_hash == contract_hash,
                    ],
                    order_func=lambda: _CodeActImplementationRow.created_at.asc(),
                ),
            )
        return [CodeActImplementation.model_validate_json(row.payload) for row in rows]

    @override
    async def get_approved(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> CodeActImplementation | None:
        await self._ensure_initialized()
        tool_hash = self._tool_hash(tool_name)
        async with self._storage.create_db_session() as db:
            approval = await self._storage.get(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash),
                    storage_cls=_CodeActApprovalRow,
                ),
            )
            if approval is None:
                return None
            implementation = await self._storage.get(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash, approval.version),
                    storage_cls=_CodeActImplementationRow,
                ),
            )
            if implementation is None:
                raise ValueError("Approved SQL CodeAct implementation "
                                 f"{approval.version!r} is missing")
            return CodeActImplementation.model_validate_json(implementation.payload)

    @override
    async def approve(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
        *,
        approved_by: str | None = None,
    ) -> CodeActImplementation:
        await self._ensure_initialized()
        tool_hash = self._tool_hash(tool_name)
        async with self._storage.create_db_session() as db:
            implementation = await self._storage.get(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash, version),
                    storage_cls=_CodeActImplementationRow,
                ),
            )
            if implementation is None:
                raise KeyError(f"Unknown CodeAct implementation version {version!r}")
            approval_key = SqlKey(
                key=(tool_hash, contract_hash),
                storage_cls=_CodeActApprovalRow,
            )
            approval = await self._storage.get_for_update(db, approval_key)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if approval is None:
                approval = _CodeActApprovalRow(
                    tool_hash=tool_hash,
                    contract_hash=contract_hash,
                    version=version,
                    approved_at=now,
                    approved_by=approved_by,
                )
                await self._storage.add(db, approval)
            else:
                approval.version = version
                approval.approved_at = now
                approval.approved_by = approved_by
            try:
                await self._storage.commit(db)
            except IntegrityError:
                # Two workers can create the first approval row concurrently.
                # Retry as an update after the winning transaction commits.
                pass
            else:
                return CodeActImplementation.model_validate_json(implementation.payload)
        return await self._retry_approval_update(
            tool_hash=tool_hash,
            contract_hash=contract_hash,
            version=version,
            approved_by=approved_by,
        )

    @override
    async def close(self) -> None:
        if self._owns_storage:
            await self._storage.close()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._initialization_lock:
            if self._initialized:
                return
            await self._storage.ensure_metadata(CodeActImplementationSqlBase.metadata)
            self._initialized = True

    async def _retry_approval_update(
        self,
        *,
        tool_hash: str,
        contract_hash: str,
        version: str,
        approved_by: str | None,
    ) -> CodeActImplementation:
        async with self._storage.create_db_session() as db:
            approval = await self._storage.get_for_update(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash),
                    storage_cls=_CodeActApprovalRow,
                ),
            )
            if approval is None:
                raise RuntimeError("SQL CodeAct approval write did not persist")
            approval.version = version
            approval.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
            approval.approved_by = approved_by
            implementation = await self._storage.get(
                db,
                SqlKey(
                    key=(tool_hash, contract_hash, version),
                    storage_cls=_CodeActImplementationRow,
                ),
            )
            if implementation is None:
                raise KeyError(f"Unknown CodeAct implementation version {version!r}")
            await self._storage.commit(db)
            return CodeActImplementation.model_validate_json(implementation.payload)

    @staticmethod
    def _tool_hash(tool_name: str) -> str:
        return hashlib.sha256(tool_name.encode()).hexdigest()

    def _implementation_key(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
    ) -> SqlKey:
        return SqlKey(
            key=(self._tool_hash(tool_name), contract_hash, version),
            storage_cls=_CodeActImplementationRow,
        )

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
