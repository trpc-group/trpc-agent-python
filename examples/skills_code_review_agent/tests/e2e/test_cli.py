#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""End-to-end tests for the public code-review command line interface."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = PROJECT_ROOT / "run_agent.py"


def _db_url(path: Path) -> str:
    """构造仅供本测试进程使用的临时 SQLite URL。"""

    return f"sqlite+pysqlite:///{path.as_posix()}"


def _run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """在仓库虚拟环境解释器下执行 CLI，并移除可能影响离线路径的模型凭据。"""

    environment = os.environ.copy()
    for name in tuple(environment):
        if "API_KEY" in name or "TOKEN" in name or "PASSWORD" in name:
            environment.pop(name)
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *arguments],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=120,
    )


def _trace_events(stderr: str) -> list[dict[str, object]]:
    """提取 stderr 中的受控 JSONL trace，忽略第三方库的非结构化告警。"""

    prefix = "[code-review-trace] "
    return [
        json.loads(line[len(prefix):])
        for line in stderr.splitlines()
        if line.startswith(prefix)
    ]


def _write_high_severity_file(tmp_path: Path) -> Path:
    """写入能够触发确定性 shell 注入规则的最小快照文件。"""

    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "import subprocess\n\nsubprocess.run('echo hello', shell=True)\n",
        encoding="utf-8",
    )
    return source


def _write_valid_diff(tmp_path: Path) -> Path:
    """写入最小合法 unified diff，供 Agent diff-file 输入回归使用。"""

    diff_file = tmp_path / "changes.diff"
    diff_file.write_text(
        "diff --git a/src/service.py b/src/service.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/service.py\n"
        "+++ b/src/service.py\n"
        "@@ -1,1 +1,2 @@\n"
        " def run():\n"
        "+    return 'changed'\n",
        encoding="utf-8",
    )
    return diff_file


def _create_changed_git_repository(tmp_path: Path) -> Path:
    """创建带一处未提交变更的独立 Git 仓库，供 repo-path Agent 输入回归使用。"""

    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "service.py"
    source.write_text("def run():\n    return 'before'\n", encoding="utf-8")
    for command in (
        ("git", "init"),
        ("git", "config", "user.email", "test@example.invalid"),
        ("git", "config", "user.name", "Code Review Test"),
        ("git", "add", "service.py"),
        ("git", "commit", "-m", "initial"),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True, timeout=20)
    source.write_text("def run():\n    return 'after'\n", encoding="utf-8")
    return repository


def _review_arguments(database: Path, source: Path, output_dir: Path) -> list[str]:
    """返回每个 CLI 评审场景共享的显式 local dry-run 参数。"""

    return [
        "review",
        "--files",
        str(source.relative_to(source.parents[1])),
        "--input-root",
        str(source.parents[1]),
        "--db-url",
        _db_url(database),
        "--output-dir",
        str(output_dir),
        "--sandbox",
        "local",
        "--dry-run",
    ]


def _docker_daemon_available() -> bool:
    """仅探测 Docker daemon 是否可用，使可选 container 用例在缺少前置条件时明确跳过。"""

    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def test_dry_run_db_url_review_show_list_and_init_db_use_one_sqlite_bundle(tmp_path: Path) -> None:
    """验证 review→show→list→init-db 共享临时数据库且不需要模型 Key 或 Docker。"""

    source = _write_high_severity_file(tmp_path)
    database = tmp_path / "review.db"
    output_dir = tmp_path / "reports"

    initialized = _run_cli(tmp_path, "init-db", "--db-url", _db_url(database))
    reviewed = _run_cli(tmp_path, *_review_arguments(database, source, output_dir))

    assert initialized.returncode == 0, initialized.stderr
    assert reviewed.returncode == 0, reviewed.stderr
    payload = json.loads(reviewed.stdout)
    assert payload["status"] in {"completed", "completed_with_warnings"}
    assert payload["task_id"].startswith("review-")
    assert payload["entrypoint"] == "pipeline"
    assert payload["report_files"] == {
        "json": str((output_dir / "review_report.json").resolve()),
        "markdown": str((output_dir / "review_report.md").resolve()),
    }
    report = json.loads((output_dir / "review_report.json").read_text(encoding="utf-8"))
    assert report["metrics"]["tool_call_count"] == 0
    assert (output_dir / "review_report.json").is_file()
    assert (output_dir / "review_report.md").is_file()

    shown = _run_cli(tmp_path, "show", payload["task_id"], "--db-url", _db_url(database))
    listed = _run_cli(tmp_path, "list", "--db-url", _db_url(database))

    assert shown.returncode == 0, shown.stderr
    shown_bundle = json.loads(shown.stdout)
    assert shown_bundle["task"]["id"] == payload["task_id"]
    assert len(shown_bundle["sandbox_runs"]) == 1
    assert len(shown_bundle["filter_events"]) == 1
    assert shown_bundle["report"] is not None
    assert listed.returncode == 0, listed.stderr
    assert payload["task_id"] in {task["id"] for task in json.loads(listed.stdout)["tasks"]}
    assert not (tmp_path / "out" / "review.db").exists()


def test_user_query_runs_sdk_skill_entry_and_returns_the_shared_report_paths(tmp_path: Path) -> None:
    """验证显式 Agent 入口加载 SkillToolSet 后仍返回唯一 pipeline 产生的报告。"""

    database = tmp_path / "agent-review.db"
    output_dir = tmp_path / "agent-reports"

    reviewed = _run_cli(
        tmp_path,
        "user-query",
        "review the approved security fixture",
        "--fixture",
        "02_security_simple",
        "--db-url",
        _db_url(database),
        "--output-dir",
        str(output_dir),
        "--sandbox",
        "local",
        "--dry-run",
    )

    assert reviewed.returncode == 0, reviewed.stderr
    payload = json.loads(reviewed.stdout)
    assert payload["entrypoint"] == "agent"
    assert payload["skill_tools"] == ["skill_load", "skill_run"]
    assert payload["task_id"].startswith("review-")
    assert payload["report_files"] == {
        "json": str((output_dir / "review_report.json").resolve()),
        "markdown": str((output_dir / "review_report.md").resolve()),
    }
    report = json.loads((output_dir / "review_report.json").read_text(encoding="utf-8"))
    assert report["metrics"]["tool_call_count"] == 2
    assert (output_dir / "review_report.json").is_file()
    assert (output_dir / "review_report.md").is_file()
    assert "[INFO] Review started: entrypoint=agent" in reviewed.stderr
    assert "[INFO] Agent tool call: skill_load" in reviewed.stderr
    assert "[INFO] Filter decision: action=ALLOW" in reviewed.stderr
    assert "review-request-" not in reviewed.stderr
    assert "shell=True" not in reviewed.stderr


def test_user_query_accepts_a_natural_language_intent_and_runs_the_skill_chain(tmp_path: Path) -> None:
    """验证自然语言 fixture 请求会解析为受控输入，并实际调用固定的 Skill 工具链。"""

    database = tmp_path / "ask-review.db"
    output_dir = tmp_path / "ask-reports"

    reviewed = _run_cli(
        tmp_path,
        "user-query",
        "请使用 code-review Skill 审查这个 fixture",
        "--fixture",
        "01_clean_simple",
        "--db-url",
        _db_url(database),
        "--output-dir",
        str(output_dir),
        "--sandbox",
        "local",
        "--dry-run",
    )

    assert reviewed.returncode == 0, reviewed.stderr
    payload = json.loads(reviewed.stdout)
    assert payload["entrypoint"] == "agent"
    assert payload["skill_tools"] == ["skill_load", "skill_run"]
    assert payload["task_id"].startswith("review-")
    report = json.loads((output_dir / "review_report.json").read_text(encoding="utf-8"))
    assert report["input_summary"]["source_kind"] == "fixture"
    assert report["metrics"]["finding_count"] == 0
    assert report["metrics"]["tool_call_count"] == 2
    assert database.is_file()


def test_user_query_supports_all_four_explicit_input_modes(tmp_path: Path) -> None:
    """验证 user-query 对 fixture、diff、files 和 repo 四类显式输入均走同一 Agent 工具链。"""

    source = _write_high_severity_file(tmp_path)
    diff_file = _write_valid_diff(tmp_path)
    repository = _create_changed_git_repository(tmp_path)
    scenarios = (
        ("fixture", ("--fixture", "01_clean_simple"), "fixture"),
        ("diff", ("--diff-file", str(diff_file)), "diff_file"),
        (
            "files",
            ("--files", str(source.relative_to(tmp_path)), "--input-root", str(tmp_path)),
            "files",
        ),
        ("repo", ("--repo-path", str(repository)), "repo_path"),
    )

    for name, source_arguments, expected_source_kind in scenarios:
        output_dir = tmp_path / f"{name}-reports"
        database = tmp_path / f"{name}.db"
        reviewed = _run_cli(
            tmp_path,
            "user-query",
            "请审查这个已明确指定的输入",
            *source_arguments,
            "--sandbox",
            "local",
            "--dry-run",
            "--db-url",
            _db_url(database),
            "--output-dir",
            str(output_dir),
        )

        assert reviewed.returncode == 0, reviewed.stderr
        payload = json.loads(reviewed.stdout)
        report = json.loads((output_dir / "review_report.json").read_text(encoding="utf-8"))
        assert payload["entrypoint"] == "agent"
        assert payload["skill_tools"] == ["skill_load", "skill_run"]
        assert report["input_summary"]["source_kind"] == expected_source_kind
        assert report["metrics"]["tool_call_count"] == 2


def test_user_query_rejects_malformed_diff_before_agent_or_sandbox(tmp_path: Path) -> None:
    """验证 user-query 在启动 Agent 前拒绝格式错误的 diff，且不产生报告。"""

    output_dir = tmp_path / "invalid-reports"
    diff_file = tmp_path / "invalid.diff"
    diff_file.write_text("this is not a unified diff\n", encoding="utf-8")
    invalid = _run_cli(
        tmp_path,
        "user-query",
        "请审查这个补丁",
        "--diff-file",
        str(diff_file),
        "--output-dir",
        str(output_dir),
        "--sandbox",
        "local",
        "--dry-run",
    )

    assert invalid.returncode == 2
    assert not (output_dir / "review_report.json").exists()


def test_user_query_trace_streams_sanitized_skill_and_pipeline_events(tmp_path: Path) -> None:
    """验证 trace 实时展示 Agent/Skill/Pipeline 流程且不泄露 query、代码或 request id。"""

    output_dir = tmp_path / "trace-reports"
    database = tmp_path / "trace-review.db"
    query = "请使用 code-review Skill 审查 02_security_simple fixture"

    reviewed = _run_cli(
        tmp_path,
        "user-query",
        query,
        "--fixture",
        "02_security_simple",
        "--trace",
        "--sandbox",
        "local",
        "--dry-run",
        "--output-dir",
        str(output_dir),
        "--db-url",
        _db_url(database),
    )

    assert reviewed.returncode == 0, reviewed.stderr
    assert json.loads(reviewed.stdout)["entrypoint"] == "agent"
    events = _trace_events(reviewed.stderr)
    event_names = [str(event["event"]) for event in events]
    assert event_names == [
        "user_query.request_received",
        "user_query.input_validated",
        "review.started",
        "agent.turn_started",
        "agent.tool_call",
        "agent.tool_response",
        "agent.tool_call",
        "skill_run.started",
        "pipeline.started",
        "pipeline.input_loaded",
        "pipeline.filter_decision",
        "pipeline.sandbox_started",
        "pipeline.sandbox_finished",
        "pipeline.report_persisted",
        "skill_run.completed",
        "agent.tool_response",
        "agent.turn_completed",
        "review.completed",
    ]
    assert [event.get("tool") for event in events if event["event"] == "agent.tool_call"] == [
        "skill_load",
        "skill_run",
    ]
    assert query not in reviewed.stderr
    assert "shell=True" not in reviewed.stderr
    assert "review-request-" not in reviewed.stderr


def test_direct_trace_streams_pipeline_events_without_agent_tools(tmp_path: Path) -> None:
    """验证 direct 入口也可追踪 Pipeline，但不会伪造 Agent 工具事件。"""

    source = _write_high_severity_file(tmp_path)
    output_dir = tmp_path / "direct-trace-reports"
    reviewed = _run_cli(
        tmp_path,
        *_review_arguments(tmp_path / "direct-trace.db", source, output_dir),
        "--trace",
    )

    assert reviewed.returncode == 0, reviewed.stderr
    assert json.loads(reviewed.stdout)["entrypoint"] == "pipeline"
    event_names = [str(event["event"]) for event in _trace_events(reviewed.stderr)]
    assert event_names == [
        "review.started",
        "pipeline.started",
        "pipeline.input_loaded",
        "pipeline.filter_decision",
        "pipeline.sandbox_started",
        "pipeline.sandbox_finished",
        "pipeline.report_persisted",
        "review.completed",
    ]
    assert not any(name.startswith("agent.") or name.startswith("skill_run.") for name in event_names)


def test_direct_and_user_query_fixture_reports_have_the_same_canonical_findings(tmp_path: Path) -> None:
    """验证 direct 与 Agent 入口对同一 fixture 生成完全一致的确定性 finding 桶。"""

    direct_dir = tmp_path / "direct"
    agent_dir = tmp_path / "agent"
    common = (
        "review",
        "--fixture",
        "02_security_simple",
        "--sandbox",
        "local",
        "--dry-run",
    )

    direct = _run_cli(
        tmp_path,
        *common,
        "--output-dir",
        str(direct_dir),
        "--db-url",
        _db_url(direct_dir / "review.db"),
    )
    user_query = _run_cli(
        tmp_path,
        "user-query",
        "review the approved security fixture",
        "--fixture",
        "02_security_simple",
        "--sandbox",
        "local",
        "--dry-run",
        "--output-dir",
        str(agent_dir),
        "--db-url",
        _db_url(agent_dir / "review.db"),
    )

    assert direct.returncode == 0, direct.stderr
    assert user_query.returncode == 0, user_query.stderr
    direct_report = json.loads((direct_dir / "review_report.json").read_text(encoding="utf-8"))
    agent_report = json.loads((agent_dir / "review_report.json").read_text(encoding="utf-8"))
    for bucket in ("findings", "needs_human_review", "suppressed"):
        assert agent_report[bucket] == direct_report[bucket]
    assert json.loads(user_query.stdout)["skill_tools"] == ["skill_load", "skill_run"]
    assert direct_report["metrics"]["tool_call_count"] == 0
    assert agent_report["metrics"]["tool_call_count"] == 2


def test_removed_via_agent_and_ask_commands_are_rejected(tmp_path: Path) -> None:
    """验证已移除的公开 Agent 兼容入口不能再绕过 user-query 契约。"""

    via_agent = _run_cli(
        tmp_path,
        "review",
        "--fixture",
        "01_clean_simple",
        "--via-agent",
    )
    ask = _run_cli(tmp_path, "ask", "请审查 01_clean_simple")

    assert via_agent.returncode == 2
    assert ask.returncode == 2


def test_fail_on_severity_returns_one_only_at_requested_boundary(tmp_path: Path) -> None:
    """验证高危 finding 在阈值命中时返回 1，而关闭阈值时保持成功退出。"""

    source = _write_high_severity_file(tmp_path)
    database = tmp_path / "review.db"
    base_arguments = _review_arguments(database, source, tmp_path / "reports")

    normal = _run_cli(tmp_path, *base_arguments)
    failing = _run_cli(tmp_path, *base_arguments, "--fail-on-severity", "high")

    assert normal.returncode == 0, normal.stderr
    assert failing.returncode == 1, failing.stderr


def test_invalid_requests_exit_two_without_container(tmp_path: Path) -> None:
    """验证互斥输入和禁止命令均在不依赖 Docker 的常规回归中返回退出码 2。"""

    source = _write_high_severity_file(tmp_path)
    database = tmp_path / "review.db"
    invalid = _run_cli(
        tmp_path,
        "review",
        "--files",
        str(source),
        "--repo-path",
        str(tmp_path),
        "--db-url",
        _db_url(database),
    )
    forbidden = _run_cli(tmp_path, "review", "--command", "whoami")
    assert invalid.returncode == 2
    assert forbidden.returncode == 2


@pytest.mark.container
def test_strict_container_runs_when_daemon_is_available(tmp_path: Path) -> None:
    """Docker 可用时验证严格 container 成功执行，且 CLI 不会静默回退到 local。"""

    if not _docker_daemon_available():
        pytest.skip("container_runtime_unavailable")

    source = _write_high_severity_file(tmp_path)
    database = tmp_path / "review.db"
    strict_container = _run_cli(
        tmp_path,
        "review",
        "--files",
        str(source.relative_to(tmp_path)),
        "--input-root",
        str(tmp_path),
        "--db-url",
        _db_url(database),
        "--sandbox",
        "container",
    )

    assert strict_container.returncode == 0, strict_container.stderr
    assert json.loads(strict_container.stdout)["sandbox"] == "container"
    assert re.search(r"\[INFO\] Container started: container_id=[0-9a-f]{64}", strict_container.stderr)
    assert "trpc_agent_sdk" not in strict_container.stderr
