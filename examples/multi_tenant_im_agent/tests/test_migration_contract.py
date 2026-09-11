from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from examples.multi_tenant_im_agent.repository import Base


class OperationRecorder:
    def __init__(self) -> None:
        self.tables: dict[str, set[str]] = {}
        self.table_items: dict[str, tuple[object, ...]] = {}
        self.indexes: set[tuple[str, str, tuple[str, ...]]] = set()
        self.dropped: list[str] = []

    def create_table(self, name: str, *items: object) -> None:
        self.tables[name] = {item.name for item in items if isinstance(item, sa.Column)}
        self.table_items[name] = items

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.indexes.add((name, table, tuple(columns)))

    def drop_table(self, name: str) -> None:
        self.dropped.append(name)


def test_initial_migration_matches_orm_metadata(monkeypatch) -> None:
    recorder = OperationRecorder()
    fake_alembic = ModuleType("alembic")
    fake_alembic.op = recorder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    migration_path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260910_0001_initial.py"
    )
    migration = runpy.run_path(str(migration_path))
    migration["upgrade"]()

    expected_tables = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }
    expected_indexes = {
        (index.name, table.name, tuple(column.name for column in index.columns))
        for table in Base.metadata.sorted_tables
        for index in table.indexes
    }
    assert recorder.tables == expected_tables
    assert recorder.indexes == expected_indexes

    for table in Base.metadata.sorted_tables:
        migration_items = recorder.table_items[table.name]
        migration_columns = {
            item.name: item for item in migration_items if isinstance(item, sa.Column)
        }
        composite_column_names = {
            column.name
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
            and len(constraint.elements) > 1
            for column in constraint.columns
        }
        for column in table.columns:
            migrated = migration_columns[column.name]
            assert migrated.nullable == column.nullable
            assert migrated.primary_key == column.primary_key
            assert migrated.type.compile(dialect=mysql.dialect()) == (
                column.type.compile(dialect=mysql.dialect())
            )
            if column.name not in composite_column_names:
                assert {str(key.target_fullname) for key in migrated.foreign_keys} == {
                    str(key.target_fullname) for key in column.foreign_keys
                }

        migrated_unique = {
            (item.name, tuple(str(column) for column in item._pending_colargs))
            for item in migration_items
            if isinstance(item, sa.UniqueConstraint)
        }
        expected_unique = {
            (constraint.name, tuple(column.name for column in constraint.columns))
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        assert migrated_unique == expected_unique

        migrated_composite_fks = {
            (
                item.name,
                tuple(item.column_keys),
                tuple(element.target_fullname for element in item.elements),
            )
            for item in migration_items
            if isinstance(item, sa.ForeignKeyConstraint) and len(item.elements) > 1
        }
        expected_composite_fks = {
            (
                constraint.name,
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
            )
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
            and len(constraint.elements) > 1
        }
        assert migrated_composite_fks == expected_composite_fks

    migration["downgrade"]()
    assert set(recorder.dropped) == set(expected_tables)
    drop_position = {name: position for position, name in enumerate(recorder.dropped)}
    for table in Base.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            assert (
                drop_position[table.name] < drop_position[foreign_key.column.table.name]
            )
