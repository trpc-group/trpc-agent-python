"""CLI smoke tests for durable run and query commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

CLI_PATH = Path("examples/skills_code_review_agent/run_agent.py")
CLI_TIMEOUT_SECONDS = 30


def _run_cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT_SECONDS,
        env=environment,
    )


def test_dry_run_can_be_queried_by_task_id(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    output_dir = tmp_path / "reports"
    result = _run_cli([
        "run",
        "--fixture",
        "clean",
        "--dry-run",
        "--db-url",
        database_url,
        "--output-dir",
        str(output_dir),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    task_line = next(line for line in result.stdout.splitlines() if line.startswith("task_id="))
    task_id = task_line.partition("=")[2]

    query = _run_cli([
        "show",
        "--task-id",
        task_id,
        "--db-url",
        database_url,
    ])
    assert query.returncode == 0, query.stdout + query.stderr
    payload = json.loads(query.stdout)
    assert payload["task_id"] == task_id
    assert payload["status"] == "complete"
    assert payload["report"]["sandbox_runs"] == []


def test_database_initializer_entrypoint_runs(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'initialized.db').as_posix()}"
    result = subprocess.run(
        [
            sys.executable,
            "examples/skills_code_review_agent/scripts/init_db.py",
            "--db-url",
            database_url,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "initialized.db").is_file()
