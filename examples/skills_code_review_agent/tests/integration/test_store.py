#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for the B1 SQLAlchemy review store."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from code_review.store import ReviewStore, SqlReviewStore, init_db  # noqa: E402
from code_review.store.models import Base  # noqa: E402


SCHEMA_VERSION = "1.0.0"
TASK_ID = "task-store-001"


def _db_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _task_payload(secret: str) -> dict[str, object]:
    return {
        "id": TASK_ID,
        "status": "running",
        "input_type": "diff_file",
        "input_ref": "changes.diff",
        "diff_summary": {
            "input_sha256": "a" * 64,
            "byte_count": 123,
            "file_count": 1,
            "hunk_count": 1,
            "additions": 1,
            "deletions": 0,
            "review_scopes": {"changed_lines": 1},
            "files": ["src/app.py"],
        },
        "config": {
            "schema_version": SCHEMA_VERSION,
            "rule_pack_version": "1.0.0",
            "config_digest": "b" * 64,
            "note": secret,
        },
    }


def _finding_payload(secret: str) -> dict[str, object]:
    return {
        "severity": "high",
        "category": "security",
        "file": "src/app.py",
        "line": 8,
        "title": "Avoid dynamic evaluation",
        "evidence": secret,
        "recommendation": "Use a typed parser.",
        "confidence": 0.92,
        "source": "ast",
        "rule_id": "security.dynamic-eval",
        "bucket": "findings",
        "dedup_key": "src/app.py:8:security",
        "extra": {"line_side": "new"},
    }


def _report_payload(secret: str) -> dict[str, object]:
    return {
        "task_id": TASK_ID,
        "schema_version": SCHEMA_VERSION,
        "rule_pack_version": "1.0.0",
        "config_digest": "b" * 64,
        "input_sha256": "a" * 64,
        "status": "completed_with_warnings",
        "summary": {"text": secret},
        "severity_stats": {"high": 1},
        "filter_summary": {"allow_count": 1, "deny_count": 0},
        "sandbox_summary": {"runtime_type": "local", "run_count": 1},
        "metrics": {"total_duration_ms": 25},
        "report": {
            "schema_version": SCHEMA_VERSION,
            "task_id": TASK_ID,
            "final_conclusion": {"summary": secret},
        },
    }


def test_models_create_exactly_five_tables_and_required_indexes(tmp_path: Path) -> None:
    store = SqlReviewStore(_db_url(tmp_path / "schema.db"))
    store.initialize()

    inspector = inspect(store.engine)
    assert set(inspector.get_table_names()) == {
        "cr_review_task",
        "cr_sandbox_run",
        "cr_filter_event",
        "cr_finding",
        "cr_report",
    }
    assert set(Base.metadata.tables) == set(inspector.get_table_names())

    indexed_columns = {
        table: {
            tuple(index["column_names"])
            for index in inspector.get_indexes(table)
        }
        for table in inspector.get_table_names()
    }
    assert ("status",) in indexed_columns["cr_review_task"]
    assert ("task_id",) in indexed_columns["cr_sandbox_run"]
    assert ("task_id",) in indexed_columns["cr_filter_event"]
    assert ("action",) in indexed_columns["cr_filter_event"]
    assert ("task_id",) in indexed_columns["cr_finding"]
    assert ("severity",) in indexed_columns["cr_finding"]
    assert ("category",) in indexed_columns["cr_finding"]
    assert ("task_id",) in indexed_columns["cr_report"]

    store.close()


def test_store_crud_bundle_versions_and_redaction(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    secret = "password=" + "synthetic-store-secret"
    store: ReviewStore = SqlReviewStore(_db_url(database))

    store.initialize()
    task = store.create_task(_task_payload(secret))
    assert task["status"] == "running"

    run = store.add_sandbox_run(
        TASK_ID,
        {
            "status": "failed",
            "exit_code": 7,
            "timed_out": False,
            "filter_action": "allow",
            "stdout_excerpt": secret,
            "stderr_excerpt": "checker failed",
            "error_type": "nonzero_exit",
            "duration_ms": 25,
        },
    )
    event = store.add_filter_event(
        TASK_ID,
        {
            "stage": "pre_execution",
            "target": "run_checks",
            "action": "allow",
            "rule": "manifest",
            "reasons": [secret],
        },
    )
    finding = store.add_finding(TASK_ID, _finding_payload(secret))
    report = store.save_report(TASK_ID, _report_payload(secret))
    updated = store.update_task(
        TASK_ID,
        status="completed_with_warnings",
        error_type="sandbox_failure",
        error_message=secret,
    )

    assert updated["status"] == "completed_with_warnings"
    assert run["id"] > 0
    assert event["id"] > 0
    assert finding["id"] > 0
    assert report["task_id"] == TASK_ID

    bundle = store.get_task_bundle(TASK_ID)
    assert bundle is not None
    assert bundle["task"]["id"] == TASK_ID
    assert len(bundle["sandbox_runs"]) == 1
    assert len(bundle["filter_events"]) == 1
    assert len(bundle["findings"]) == 1
    assert bundle["report"]["schema_version"] == SCHEMA_VERSION
    assert bundle["report"]["rule_pack_version"] == "1.0.0"
    assert bundle["report"]["config_digest"] == "b" * 64
    assert bundle["report"]["input_sha256"] == "a" * 64

    json_columns = (
        bundle["task"]["diff_summary"],
        bundle["task"]["config"],
        bundle["filter_events"][0]["reasons"],
        bundle["findings"][0]["extra"],
        bundle["report"]["summary"],
        bundle["report"]["severity_stats"],
        bundle["report"]["filter_summary"],
        bundle["report"]["sandbox_summary"],
        bundle["report"]["metrics"],
        bundle["report"]["report"],
    )
    assert all(value["schema_version"] == SCHEMA_VERSION for value in json_columns)
    assert secret not in json.dumps(bundle, sort_keys=True)
    assert secret.encode("utf-8") not in database.read_bytes()

    deleted = store.delete_task(TASK_ID)
    assert deleted is True
    assert store.get_task_bundle(TASK_ID) is None
    assert store.delete_task(TASK_ID) is False
    store.close()


def test_init_db_is_idempotent_and_sql_url_isolated(tmp_path: Path) -> None:
    first_database = tmp_path / "first.db"
    second_database = tmp_path / "second.db"
    first_url = _db_url(first_database)
    second_url = _db_url(second_database)

    init_db(first_url)
    first_store = SqlReviewStore(first_url)
    first_store.initialize()
    first_store.create_task(_task_payload("safe"))
    first_store.close()

    init_db(first_url)
    reopened = SqlReviewStore(first_url)
    reopened.initialize()
    assert reopened.get_task_bundle(TASK_ID) is not None
    reopened.close()

    init_db(second_url)
    isolated = SqlReviewStore(second_url)
    isolated.initialize()
    assert isolated.get_task_bundle(TASK_ID) is None
    isolated.close()


def test_init_db_module_cli_creates_all_business_tables(tmp_path: Path) -> None:
    """验证独立模块 CLI 使用指定 URL 初始化完整的五张业务表。"""

    database = tmp_path / "module-init.db"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_review.store.init_db",
            "--db-url",
            _db_url(database),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    engine = create_engine(_db_url(database))
    try:
        assert set(inspect(engine).get_table_names()) == {
            "cr_filter_event",
            "cr_finding",
            "cr_report",
            "cr_review_task",
            "cr_sandbox_run",
        }
    finally:
        engine.dispose()
