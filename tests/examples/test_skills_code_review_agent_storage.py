"""Storage contract tests for the skills code-review example."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent import storage as storage_module
from examples.skills_code_review_agent.agent.models import (
    FilterDecision,
    Finding,
    ReviewMetrics,
    SandboxRun,
)
from examples.skills_code_review_agent.agent.storage import (
    ReviewStore,
    ReviewStoreBase,
    SqliteReviewStore,
    TaskExistsError,
    create_store,
)
from examples.skills_code_review_agent.scripts.init_db import main as init_db_main
from examples.skills_code_review_agent.scripts.query_review import (
    main as query_review_main,
)


def _create_task(store: ReviewStoreBase, *, task_id: str = "task-1", input_ref: str = "fixture:old",
                 overwrite: bool = False, input_type: str = "fixture") -> None:
    store.create_task(
        task_id=task_id,
        input_type=input_type,
        input_ref=input_ref,
        diff_sha256="abc123",
        diff_summary={"file_count": 1},
        overwrite=overwrite,
    )


def _finding() -> Finding:
    return Finding(
        severity="high",
        category="security",
        file="app.py",
        line=3,
        title="unsafe call",
        evidence="eval(value)",
        recommendation="Use an explicit parser.",
        confidence=0.9,
        source="test",
    )


def _populate_bundle(store: ReviewStoreBase, task_id: str = "task-1") -> None:
    decision = FilterDecision(
        action="deny",
        rule_id="test.denied",
        reason="test policy",
        command="curl example.invalid",
    )
    store.add_sandbox_run(
        task_id,
        SandboxRun(
            name="probe",
            runtime="dry-run-local",
            command="curl example.invalid",
            status="filtered",
            filter_decision=decision,
        ),
    )
    store.add_finding(task_id, _finding())
    store.add_metrics(task_id, ReviewMetrics(finding_count=1, intercept_count=1))
    store.add_report(task_id, {"task_id": task_id, "summary": {"finding_count": 1}}, "# Report")


def test_storage_contract_factory_and_compatibility_alias(tmp_path: Path):
    assert issubclass(SqliteReviewStore, ReviewStoreBase)
    assert ReviewStore is SqliteReviewStore

    raw_store = create_store(tmp_path / "raw.sqlite3")
    try:
        assert isinstance(raw_store, SqliteReviewStore)
    finally:
        raw_store.close()

    dsn_path = tmp_path / "dsn.sqlite3"
    dsn_store = create_store(f"sqlite:///{dsn_path.as_posix()}")
    try:
        assert isinstance(dsn_store, SqliteReviewStore)
        assert dsn_path.is_file()
    finally:
        dsn_store.close()

    memory_store = create_store("sqlite:///:memory:")
    try:
        assert memory_store.list_tasks() == []
    finally:
        memory_store.close()


@pytest.mark.parametrize("dsn", [
    "postgresql://user:pass@localhost/reviews",
    "postgres://user:pass@localhost/reviews",
    "postgresql+psycopg://user:pass@localhost/reviews",
])
def test_postgresql_dsn_is_recognized_but_explicitly_unimplemented(dsn: str):
    with pytest.raises(NotImplementedError, match="PostgreSQL review storage is not implemented"):
        create_store(dsn)


def test_unknown_dsn_scheme_is_rejected():
    with pytest.raises(ValueError, match="unsupported review store DSN scheme: mysql"):
        create_store("mysql://localhost/reviews")


def test_init_schema_reads_schema_sql_as_the_only_ddl_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_source = Path(storage_module.__file__).read_text(encoding="utf-8")
    assert "CREATE TABLE" not in storage_source.upper()

    custom_schema = tmp_path / "schema.sql"
    custom_schema.write_text(
        storage_module.SCHEMA_PATH.read_text(encoding="utf-8")
        + "\nCREATE TABLE schema_source_probe (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_module, "SCHEMA_PATH", custom_schema)
    store = SqliteReviewStore(tmp_path / "schema.sqlite3")
    try:
        row = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_source_probe'"
        ).fetchone()
        assert row["name"] == "schema_source_probe"
    finally:
        store.close()


def test_duplicate_task_id_raises_without_destroying_existing_bundle(tmp_path: Path):
    store = SqliteReviewStore(tmp_path / "reviews.sqlite3")
    try:
        _create_task(store)
        _populate_bundle(store)

        with pytest.raises(TaskExistsError, match="task-1"):
            _create_task(store, input_ref="fixture:new")

        bundle = store.get_task("task-1")
        assert bundle["task"]["input_ref"] == "fixture:old"
        assert len(bundle["sandbox_runs"]) == 1
        assert len(bundle["findings"]) == 1
        assert len(bundle["filter_intercepts"]) == 1
        assert bundle["metrics"]["finding_count"] == 1
        assert bundle["report"]["task_id"] == "task-1"
    finally:
        store.close()


def test_explicit_overwrite_atomically_resets_the_complete_bundle(tmp_path: Path):
    store = SqliteReviewStore(tmp_path / "reviews.sqlite3")
    try:
        _create_task(store)
        _populate_bundle(store)

        _create_task(store, input_ref="fixture:new", overwrite=True)

        bundle = store.get_task("task-1")
        assert bundle["task"]["status"] == "running"
        assert bundle["task"]["input_ref"] == "fixture:new"
        assert bundle["task"]["finished_at"] is None
        assert bundle["task"]["final_conclusion"] == ""
        assert bundle["sandbox_runs"] == []
        assert bundle["findings"] == []
        assert bundle["filter_intercepts"] == []
        assert bundle["metrics"] == {}
        assert bundle["report"] == {}
    finally:
        store.close()


def test_failed_overwrite_rolls_back_parent_and_children(tmp_path: Path):
    store = SqliteReviewStore(tmp_path / "reviews.sqlite3")
    try:
        _create_task(store)
        _populate_bundle(store)

        with pytest.raises(sqlite3.IntegrityError):
            _create_task(store, input_ref="fixture:new", overwrite=True, input_type=None)  # type: ignore[arg-type]

        bundle = store.get_task("task-1")
        assert bundle["task"]["input_ref"] == "fixture:old"
        assert len(bundle["sandbox_runs"]) == 1
        assert len(bundle["findings"]) == 1
        assert len(bundle["filter_intercepts"]) == 1
        assert bundle["metrics"]["finding_count"] == 1
        assert bundle["report"]["task_id"] == "task-1"
    finally:
        store.close()


def test_list_tasks_supports_limit_and_status_filter(tmp_path: Path):
    store = SqliteReviewStore(tmp_path / "reviews.sqlite3")
    try:
        _create_task(store, task_id="running-task")
        _create_task(store, task_id="done-task")
        store.update_task("done-task", status="completed", final_conclusion="done")

        assert len(store.list_tasks(limit=1)) == 1
        completed = store.list_tasks(status="completed")
        assert [task["task_id"] for task in completed] == ["done-task"]
        assert store.list_tasks(limit=0) == []
        with pytest.raises(ValueError, match="limit must be non-negative"):
            store.list_tasks(limit=-1)
    finally:
        store.close()


def test_init_and_query_scripts_support_json_and_table_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "cli.sqlite3"
    assert init_db_main([str(db_path), "--json"]) == 0
    init_payload = json.loads(capsys.readouterr().out)
    assert init_payload["status"] == "initialized"

    store = SqliteReviewStore(db_path)
    try:
        _create_task(store, task_id="cli-task")
        store.update_task("cli-task", status="completed", final_conclusion="clean")
    finally:
        store.close()

    assert query_review_main(["--database", str(db_path), "list", "--format", "json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [task["task_id"] for task in listed] == ["cli-task"]

    assert query_review_main(["--database", str(db_path), "query", "cli-task", "--format", "json"]) == 0
    queried = json.loads(capsys.readouterr().out)
    assert queried["task"]["final_conclusion"] == "clean"

    assert query_review_main(["--database", str(db_path), "query", "cli-task", "--format", "table"]) == 0
    table = capsys.readouterr().out
    assert "Task" in table
    assert "cli-task" in table
    assert "Metrics" in table
