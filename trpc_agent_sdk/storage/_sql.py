# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SQL storage implementation."""
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Hashable
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Type
from typing import TypeAlias
from typing import Union
from typing_extensions import override

from sqlalchemy import MetaData
from sqlalchemy import and_
from sqlalchemy import delete as sql_delete
from sqlalchemy import Dialect
from sqlalchemy.sql.compiler import IdentifierPreparer
from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import create_engine
from sqlalchemy.engine.interfaces import DBAPICursor
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session as DatabaseSessionFactory
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from tzlocal import get_localzone

from trpc_agent_sdk.log import logger

from ._db import BaseStorage
from ._sql_common import StorageData

SqlSessionFactory: TypeAlias = Union[sessionmaker[DatabaseSessionFactory], async_sessionmaker[AsyncSession]]
SqlSession: TypeAlias = Union[AsyncSession, DatabaseSessionFactory]
SqlEngine: TypeAlias = Union[Engine, AsyncEngine]


@dataclass
class SqlKey:
    """Key for SQL storage.

    storage_cls can be any SQLAlchemy DeclarativeBase subclass, including custom base classes like SessionStorageBase.
    """
    key: Tuple[Hashable, ...]
    storage_cls: Type[DeclarativeBase]


@dataclass
class SqlCondition:
    """Condition for SQL storage."""
    filters: Optional[Sequence[ColumnElement[bool]]] = None
    order_func: Optional[Callable[[], Any]] = None
    limit: Optional[int] = None


class SqlAsyncContextManager:

    def __init__(self, sql_storage: 'SqlStorage') -> None:
        self.__sql_storage = sql_storage
        self._session: Optional[SqlSession] = None

    async def __aenter__(self):
        # Initialize resources or execute asynchronous operations
        self._session = await self.__sql_storage.create_sql_session()
        if isinstance(self._session, AsyncSession):
            await self._session.__aenter__()
        else:
            self._session.__enter__()
        return self._session  # Can return an object for use in `async with` block

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self._session:
            return
        if isinstance(self._session, AsyncSession):
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
        else:
            self._session.__exit__(exc_type, exc_val, exc_tb)


def _set_sqlite_pragma(dbapi_connection: DBAPICursor, connection_record):
    """Set sqlite pragma to enable foreign keys constraints"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class SqlStorage(BaseStorage):
    """SQL Storage Implementation."""

    def __init__(self, is_async: bool, db_url: str, metadata: Optional[MetaData] = None, **kwargs: Any) -> None:
        """Initialize SQL storage.

        Args:
            is_async: Whether to use async operations
            db_url: Database connection URL
            metadata: Optional SQLAlchemy metadata object. If None, uses StorageData.metadata.
                      This allows different services to use separate metadata and only create
                      tables they need (e.g., SqlSessionService can exclude mem_events table).
            **kwargs: Additional arguments passed to SQLAlchemy engine
        """
        super().__init__()
        # Get the local timezone
        local_timezone = get_localzone()
        logger.debug("Local timezone: %s", local_timezone)

        self.inspector = None
        self._database_session_factory: Optional[SqlSessionFactory] = None
        self._db_engine: Optional[SqlEngine] = None
        self.__metadata = metadata if metadata is not None else StorageData.metadata
        self.__is_async = is_async
        self.__db_url = db_url
        self.__kwargs = kwargs

    def _migrate_missing_columns(self, connection: Connection) -> None:
        """Add columns that exist in the ORM model but are missing from the database,
        for forward compatibility across version changes.

        SQLAlchemy's create_all only creates tables — it never ALTERs existing
        tables. This helper bridges the gap for lightweight forward-only migrations.
        Only handles adding new columns (forward-only).

        All-or-nothing semantics: on databases that support transactional DDL
        (e.g. PostgreSQL) the caller's transaction handles rollback. On databases
        where DDL auto-commits (e.g. MySQL), a compensating DROP COLUMN is issued
        for every column that was already added before the failure.

        Args:
            connection: A synchronous SQLAlchemy Connection object.
        """
        insp: Inspector = inspect(connection)
        dialect: Dialect = connection.dialect
        preparer: IdentifierPreparer = dialect.identifier_preparer
        ddl_compiler = dialect.ddl_compiler(dialect, None)

        pending_add_columns: list[tuple[str, str, str]] = []
        for table_name, table in self.__metadata.tables.items():
            if not insp.has_table(table_name):
                continue
            existing: set[str] = {col["name"] for col in insp.get_columns(table_name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type: str = column.type.compile(dialect=dialect)
                # handle different types of default value
                nullable: str = "" if column.nullable else " NOT NULL"
                default: str = ""
                default_value = ddl_compiler.get_column_default_string(column)
                if default_value is not None:
                    default = f" DEFAULT {default_value}"
                elif column.server_default is not None:
                    # if the column has server_default, but it is not a DDL server_default, warning
                    logger.warning(
                        "Column '%s' on table '%s' has a non-DDL server_default "
                        "(%s); skipping DEFAULT clause generation.",
                        column.name,
                        table_name,
                        type(column.server_default).__name__,
                    )
                elif not column.nullable:
                    # if the column is NOT NULL and has no server_default, raise error
                    logger.warning(
                        "Column '%s' on table '%s' is NOT NULL without a server_default; "
                        "migration may fail if the table already contains rows.",
                        column.name,
                        table_name,
                    )
                quoted_table: str = preparer.quote_identifier(table_name)
                quoted_col: str = preparer.quote_identifier(column.name)
                stmt: str = f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_col} {col_type}{default}{nullable}"
                pending_add_columns.append((stmt, column.name, table_name))

        if not pending_add_columns:
            return

        added_columns: list[tuple[str, str]] = []
        try:
            for stmt, col_name, table_name in pending_add_columns:
                connection.execute(text(stmt))
                added_columns.append((col_name, table_name))
                logger.info("Auto-migrated: added column '%s' to table '%s'", col_name, table_name)
        except Exception:
            logger.error("Migration failed, compensating %d already-added column(s).", len(added_columns))
            for col_name, tbl_name in reversed(added_columns):
                drop_stmt = (f"ALTER TABLE {preparer.quote_identifier(tbl_name)} "
                             f"DROP COLUMN {preparer.quote_identifier(col_name)}")
                try:
                    connection.execute(text(drop_stmt))
                    logger.info("Compensated: dropped column '%s' from table '%s'", col_name, tbl_name)
                except Exception:
                    logger.error(
                        "Failed to compensate column '%s' on table '%s'; manual cleanup required.",
                        col_name,
                        tbl_name,
                    )
            raise

    async def create_sql_engine(self):
        """Create the database engine."""
        if self._db_engine:
            return
        try:
            if self.__is_async:
                db_engine: SqlEngine = create_async_engine(self.__db_url, **self.__kwargs)

                async def _async_inspect():
                    async with db_engine.connect() as conn:
                        return await conn.run_sync(lambda sync_conn: inspect(sync_conn))

                self.inspector = await _async_inspect()
                async with db_engine.begin() as conn:
                    await conn.run_sync(self.__metadata.create_all)
                    await conn.run_sync(self._migrate_missing_columns)
                self._database_session_factory = async_sessionmaker(bind=db_engine)
            else:
                db_engine: SqlEngine = create_engine(self.__db_url, **self.__kwargs)
                self.inspector = inspect(db_engine)
                self.__metadata.create_all(db_engine)
                with db_engine.begin() as conn:
                    self._migrate_missing_columns(conn)
                self._database_session_factory = sessionmaker(bind=db_engine)

            if db_engine.dialect.name == "sqlite":
                listen_target = db_engine.sync_engine if isinstance(db_engine, AsyncEngine) else db_engine
                event.listen(listen_target, "connect", _set_sqlite_pragma)

        except Exception as ex:  # pylint: disable=broad-except
            if isinstance(ex, ArgumentError):
                raise ValueError(f"Invalid database URL format or argument '{self.__db_url}'.") from ex
            if isinstance(ex, ImportError):
                raise ValueError(f"Database related module not found for URL '{self.__db_url}'.") from ex
            raise ValueError(f"Failed to create database engine for URL '{self.__db_url}'") from ex
        self._db_engine = db_engine

    @override
    async def close(self):
        """Close the database engine."""
        if not self._db_engine:
            return
        if isinstance(self._db_engine, AsyncEngine):
            await self._db_engine.dispose()
        else:
            self._db_engine.dispose()

    async def create_sql_session(self) -> SqlSession:
        await self.create_sql_engine()
        if not self._database_session_factory:
            raise ValueError("Database session factory not initialized")
        return self._database_session_factory()

    def create_db_session(self) -> SqlAsyncContextManager:
        """Get the DB session."""
        return SqlAsyncContextManager(self)

    @override
    async def add(self, db: SqlSession, data: DeclarativeBase):
        """Add the data"""
        return db.add(data)

    @override
    async def delete(self, db: SqlSession, key: SqlKey, conditions: SqlCondition):
        """Delete the data"""
        stmt = sql_delete(key.storage_cls)
        if conditions.filters is not None:
            stmt = stmt.where(and_(*conditions.filters))
        if isinstance(db, AsyncSession):
            await db.execute(stmt)
            return
        return db.execute(stmt)

    @override
    async def get(self, db: SqlSession, key: SqlKey) -> Any:
        """Get the value by key."""
        if isinstance(db, AsyncSession):
            return await db.get(key.storage_cls, key.key)
        return db.get(key.storage_cls, key.key)

    @override
    async def query(self, db: SqlSession, key: SqlKey, conditions: SqlCondition) -> Any:
        """Query the data"""
        stmt = select(key.storage_cls)
        if conditions.filters is not None:
            stmt = stmt.where(and_(*conditions.filters))
        if conditions.order_func:
            stmt = stmt.order_by(conditions.order_func())
        stmt = stmt.limit(conditions.limit)
        if isinstance(db, AsyncSession):
            result = await db.execute(stmt)
            return result.scalars().all()

        result = db.execute(stmt)
        return result.scalars().all()

    @override
    async def commit(self, db: SqlSession) -> None:
        """Commit the changes"""
        try:
            if isinstance(db, AsyncSession):
                await db.commit()
                return
            db.commit()
        except Exception as ex:  # pylint: disable=broad-except
            if isinstance(db, AsyncSession):
                await db.rollback()
            else:
                db.rollback()
            raise ex

    @override
    async def refresh(self, db: SqlSession, data: DeclarativeBase) -> None:
        """Refresh data"""
        if isinstance(db, AsyncSession):
            await db.refresh(data)
            return
        db.refresh(data)
