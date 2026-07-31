# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for the code-review input CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from examples.skills_code_review_agent.agent import ReviewStore


def test_diff_input_writes_normalized_intermediate_artifacts(tmp_path: Path):
    diff = tmp_path / "change.diff"
    diff.write_text(_sample_diff(), encoding="utf-8")
    output = tmp_path / "out"

    result = _run_cli("--diff-file", str(diff), "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    assert "input_type=diff_file" in result.stdout
    assert "files=1" in result.stdout


def test_repo_input_writes_normalized_intermediate_artifacts(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")
    output = tmp_path / "out"

    result = _run_cli("--repo-path", str(repo), "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    assert "input_type=repo_path" in result.stdout
    assert "files=1" in result.stdout


def test_fixture_input_writes_normalized_intermediate_artifacts(tmp_path: Path):
    output = tmp_path / "out"

    result = _run_cli("--fixture", "clean", "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    assert "input_type=fixture" in result.stdout
    assert "files=2" in result.stdout


def test_file_list_input_reviews_text_files(tmp_path: Path):
    source = tmp_path / "listed.py"
    source.write_text("eval(user_input)\n", encoding="utf-8")
    file_list = tmp_path / "files.txt"
    file_list.write_text(str(source), encoding="utf-8")
    output = tmp_path / "out"

    result = _run_cli("--file-list", str(file_list), "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    assert "input_type=file_list" in result.stdout
    assert "files=1" in result.stdout


def test_unsupported_runtime_is_rejected(tmp_path: Path):
    result = _run_cli("--fixture", "clean", "--runtime", "unsafe", "--output-dir", str(tmp_path))

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_local_runtime_requires_explicit_allow_local(tmp_path: Path):
    result = _run_cli("--fixture", "clean", "--runtime", "local-dev", "--output-dir", str(tmp_path))

    assert result.returncode != 0
    assert "--runtime local-dev requires --allow-local" in result.stderr


def test_container_and_cube_runtime_write_human_review_artifacts(tmp_path: Path):
    container_output = tmp_path / "container"
    container = _run_cli(
        "--fixture",
        "clean",
        "--runtime",
        "container",
        "--docker-base-url",
        "unix:///tmp/skills-code-review-agent-missing-docker.sock",
        "--output-dir",
        str(container_output),
    )
    assert container.returncode == 0, container.stderr
    container_report = _read_report(container_output)
    assert container_report["sandbox_runs"][0]["status"] == "needs_human_review"
    assert container_report["interceptions"][0]["decision"] == "needs_human_review"

    cube_output = tmp_path / "cube"
    cube = _run_cli("--fixture", "clean", "--runtime", "cube", "--output-dir", str(cube_output))
    assert cube.returncode == 0, cube.stderr
    cube_report = _read_report(cube_output)
    assert cube_report["sandbox_runs"][0]["status"] == "needs_human_review"
    assert cube_report["interceptions"][0]["decision"] == "needs_human_review"


def test_local_runtime_with_allow_executes_rule_runner(tmp_path: Path):
    output = tmp_path / "out"

    result = _run_cli("--fixture", "clean", "--runtime", "local-dev", "--allow-local", "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    report = _read_report(output)
    assert report["sandbox_runs"][0]["status"] == "success"
    assert report["sandbox_runs"][0]["exit_code"] == 0


def test_missing_input_path_returns_clear_error(tmp_path: Path):
    missing = tmp_path / "missing.diff"

    result = _run_cli("--diff-file", str(missing), "--output-dir", str(tmp_path / "out"))

    assert result.returncode != 0
    assert "diff file not found" in result.stderr


def test_cli_creates_database_and_final_reports(tmp_path: Path):
    output = tmp_path / "out"

    result = _run_cli("--fixture", "clean", "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    task_id = _stdout_value(result.stdout, "task_id")
    db_path = _stdout_value(result.stdout, "db_path")
    with ReviewStore(db_path) as store:
        assert store.get_task(task_id)["status"] == "done"
        assert store.get_report(task_id)["report_json"]["task_id"] == task_id


def test_cli_module_execution_works(tmp_path: Path):
    output = tmp_path / "out"
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.skills_code_review_agent.run_review",
            "--fixture",
            "clean",
            "--output-dir",
            str(output),
        ],
        cwd=root.parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "input_type=fixture" in result.stdout


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    return subprocess.run(
        [sys.executable, "run_review.py", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _read_report(output: Path) -> dict:
    return json.loads((output / "review_report.json").read_text(encoding="utf-8"))


def _stdout_value(stdout: str, key: str) -> str:
    prefix = f"{key}="
    for line in stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise AssertionError(f"missing stdout key: {key}")


def _sample_diff() -> str:
    return """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
"""
