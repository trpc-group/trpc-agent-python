from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

from examples.multi_tenant_im_agent.repository import Base


class OperationRecorder:
    def __init__(self) -> None:
        self.tables: dict[str, set[str]] = {}
        self.indexes: set[tuple[str, str, tuple[str, ...]]] = set()
        self.dropped: list[str] = []

    def create_table(self, name: str, *items: object) -> None:
        self.tables[name] = {item.name for item in items if isinstance(item, sa.Column)}

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

    migration["downgrade"]()
    assert set(recorder.dropped) == set(expected_tables)
    drop_position = {name: position for position, name in enumerate(recorder.dropped)}
    for table in Base.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            assert (
                drop_position[table.name] < drop_position[foreign_key.column.table.name]
            )
