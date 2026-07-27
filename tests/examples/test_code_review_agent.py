"""Tests for the native Skill code-review Agent's deterministic boundaries."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent.policy import evaluate_command
from examples.skills_code_review_agent.agent.storage import ReviewStorage
from examples.skills_code_review_agent.agent.prompts import INSTRUCTION
from examples.skills_code_review_agent.agent.tools import REVIEW_SKILL_TOOL_NAMES, plan_review_actions, parse_review_input, save_review_report
from examples.skills_code_review_agent.run_review import create_task_id

FIXTURES = Path(__file__).parents[2] / "examples" / "skills_code_review_agent" / "tests" / "fixtures"


def test_parse_review_input_returns_redacted_hunks_and_changed_lines():
    result = parse_review_input(diff='+++ b/a.py\n@@ -0,0 +1 @@\n+token = "sk-secret"\n')
    assert result["changed_files"] == ["a.py"]
    assert "sk-secret" not in result["diff"]
    assert result["hunks"][0]["file"] == "a.py"


def test_transaction_rollback_rule_is_scoped_to_the_changed_file():
    from examples.skills_code_review_agent.agent.parser import ChangedLine
    from examples.skills_code_review_agent.agent.rules import scan

    findings = scan([
        ChangedLine("src/a.py", 10, "tx.begin()"),
        ChangedLine("src/b.py", 4, "transaction.rollback()"),
    ])

    assert any(item.file == "src/a.py" and item.title == "Transaction has no rollback path" for item in findings)


def test_rule_deduplication_preserves_identical_findings_on_different_lines():
    from examples.skills_code_review_agent.agent.parser import ChangedLine
    from examples.skills_code_review_agent.agent.rules import scan

    findings = scan([
        ChangedLine("src/client.py", 1, 'token = "same-secret"'),
        ChangedLine("src/client.py", 2, 'token = "same-secret"'),
    ])

    assert {item.line for item in findings if item.category == "secret"} == {1, 2}


def test_diff_no_newline_marker_does_not_advance_new_file_line_number():
    from examples.skills_code_review_agent.agent.parser import parse_unified_diff

    changed = parse_unified_diff(
        "+++ b/a.py\n@@ -1,2 +1,2 @@\n unchanged\n\\ No newline at end of file\n+added = True\n"
    )

    assert [(item.line, item.content) for item in changed] == [(2, "added = True")]


def test_task_id_is_timestamped_readable_and_collision_safe():
    task_id = create_task_id(
        diff_file=str(FIXTURES / "02_hardcoded_token" / "input.diff"),
        now=datetime(2026, 7, 23, 22, 45, 30), suffix="a1b2c3d4",
    )

    assert task_id == "cr-20260723-224530-hardcoded-token-a1b2c3d4"


@pytest.mark.asyncio
async def test_cli_fake_model_uses_runner_without_model_api_key(tmp_path, monkeypatch):
    from examples.skills_code_review_agent import run_review
    from examples.skills_code_review_agent.agent import agent as review_agent

    fake_model = object()
    captured: dict = {}

    async def fake_run(payload, runtime, model, workspace_inputs, task_id, output_dir):
        captured.update(payload=payload, runtime=runtime, model=model, workspace_inputs=workspace_inputs,
                        task_id=task_id, output_dir=output_dir)

    monkeypatch.setattr(review_agent, "create_fake_model", lambda: fake_model)
    monkeypatch.setattr(run_review, "_run_sdk_agent", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "run_review.py", "--diff-file", str(FIXTURES / "01_clean" / "input.diff"),
        "--fake-model", "--output-dir", str(tmp_path),
    ])

    await run_review.main()

    assert captured["model"] is fake_model
    assert captured["runtime"] == "docker"
    assert captured["payload"]["task_id"].startswith("cr-")
    assert captured["task_id"] == captured["payload"]["task_id"]
    assert all("src" not in item for item in captured["payload"]["workspace_inputs"])
    assert "output_dir" not in captured["payload"]


def test_cli_rejects_dry_run_and_fake_model_together():
    completed = subprocess.run([
        sys.executable, "examples/skills_code_review_agent/run_review.py",
        "--diff-file", str(FIXTURES / "01_clean" / "input.diff"),
        "--dry-run", "--fake-model",
    ], cwd=Path(__file__).parents[2], capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "not allowed with argument" in completed.stderr


def test_parse_review_input_stages_diff_and_changed_files(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "src" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    patch = tmp_path / "input.diff"
    patch.write_text("+++ b/src/example.py\n@@ -0,0 +1 @@\n+value = 1\n", encoding="utf-8")
    result = parse_review_input(
        diff_file=str(patch), repo_path=str(repo), staging_dir=str(tmp_path / "staging")
    )
    assert [item["dst"] for item in result["workspace_inputs"]] == ["work/inputs/input.diff", "work/inputs/src/example.py"]


def test_parse_review_input_skips_diff_paths_outside_repository_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 'outside'\n", encoding="utf-8")
    patch = tmp_path / "input.diff"
    patch.write_text("+++ b/../../outside.py\n@@ -0,0 +1 @@\n+secret = 'outside'\n", encoding="utf-8")

    result = parse_review_input(
        diff_file=str(patch),
        repo_path=str(repo),
    )

    assert all(item["dst"] != "work/inputs/outside.py" for item in result["workspace_inputs"])
    assert all("outside.py" not in item["src"] for item in result["_execution_workspace_inputs"])


def test_parse_review_input_exposes_only_sandbox_paths_to_model_payload(tmp_path):
    patch = tmp_path / "input.diff"
    patch.write_text("+++ b/a.py\n@@ -0,0 +1 @@\n+value = 1\n", encoding="utf-8")

    result = parse_review_input(diff_file=str(patch))

    assert all("src" not in item for item in result["workspace_inputs"])
    assert any(item["src"].startswith("host://") for item in result["_execution_workspace_inputs"])


def test_parse_review_input_stages_generated_repo_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    source = repo / "example.py"
    source.write_text("token = 'before'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "example.py"], check=True, capture_output=True)
    source.write_text('token = "sk-secret"\n', encoding="utf-8")
    result = parse_review_input(repo_path=str(repo), staging_dir=str(tmp_path / "staging"))

    staged_diff = tmp_path / "staging" / "input.diff"
    assert staged_diff.read_text(encoding="utf-8") == result["diff"]
    assert "sk-secret" not in staged_diff.read_text(encoding="utf-8")
    assert result["workspace_inputs"][0]["dst"] == "work/inputs/input.diff"


def test_save_review_report_moves_low_confidence_to_human_review(tmp_path):
    report = save_review_report(task_id="task-1", findings=[{
        "severity": "warning", "category": "semantic", "file": "a.py", "line": 1,
        "title": "Review", "evidence": "value = 1", "recommendation": "Add a test.",
        "confidence": 0.4, "source": "model",
    }], evidence={"changed_lines": [{"file": "a.py", "line": 1, "content": "value = 1"}]}, output_dir=str(tmp_path))
    assert not any(item["category"] == "semantic" for item in report["findings"])
    assert report["needs_human_review"][0]["file"] == "a.py"
    assert Path(report["json_path"]).exists()


@pytest.mark.parametrize("task_id", ["../escape", "nested/task", "nested\\task", "/absolute", ".."])
def test_save_review_report_rejects_unsafe_task_ids(tmp_path, task_id):
    with pytest.raises(ValueError, match="task_id"):
        save_review_report(
            task_id=task_id, findings=[], evidence={"changed_lines": []}, output_dir=str(tmp_path)
        )


def test_save_review_report_rejects_model_forged_task_or_output_dir(tmp_path):
    from types import SimpleNamespace

    context = SimpleNamespace(agent_context=SimpleNamespace(metadata={
        "code_review_task_id": "cr-trusted-task",
        "code_review_output_dir": str(tmp_path / "trusted-output"),
    }))

    with pytest.raises(ValueError, match="trusted task"):
        save_review_report(
            task_id="cr-forged-task", findings=[], evidence={"changed_lines": []},
            output_dir=str(tmp_path / "forged-output"), tool_context=context,
        )


def test_save_review_report_returns_safe_summary_to_model_tool_context(tmp_path):
    from types import SimpleNamespace

    output_dir = tmp_path / "trusted-output"
    context = SimpleNamespace(agent_context=SimpleNamespace(metadata={
        "code_review_task_id": "cr-trusted-task",
        "code_review_output_dir": str(output_dir),
        "code_review_changed_lines": [],
        "code_review_skill_runs": [],
        "code_review_model_runs": [],
        "code_review_filter_decisions": [],
    }))

    result = save_review_report(
        task_id="cr-trusted-task", findings=[], evidence={"changed_lines": []}, tool_context=context,
    )

    assert result == {
        "task_id": "cr-trusted-task",
        "status": "completed",
        "finding_count": 0,
        "needs_human_review_count": 0,
        "report_files": ["review_report.json", "review_report.md"],
    }
    assert str(output_dir) not in json.dumps(result)


@pytest.mark.parametrize("fixture_name,category", [
    ("01_clean", None), ("02_hardcoded_token", "secret"), ("03_async_leak", "async"),
    ("04_db_transaction_leak", "database"), ("05_missing_test", "tests"),
    ("06_duplicate_finding", "secret"), ("07_sandbox_failure", None), ("08_secret_redaction", "secret"),
])
def test_public_fixtures_generate_redacted_persisted_reports(tmp_path, fixture_name, category):
    payload = parse_review_input(diff=(FIXTURES / fixture_name / "input.diff").read_text(encoding="utf-8"))
    report = save_review_report(task_id=fixture_name, findings=[], evidence={"changed_lines": payload["changed_lines"]}, output_dir=str(tmp_path))
    assert Path(report["json_path"]).exists()
    if category:
        assert category in {item["category"] for item in report["findings"]}
    assert "sk-live" not in Path(report["json_path"]).read_text(encoding="utf-8")


def test_filter_denies_network_and_escalates_dynamic_python():
    assert evaluate_command(["curl", "https://example.invalid"], 30).decision == "deny"
    assert evaluate_command(["python", "-c", "import socket"], 30).decision == "needs_human_review"
    assert evaluate_command('python -c"import socket"', 30).decision == "needs_human_review"
    assert evaluate_command("python scripts/check.py; curl https://example.invalid", 30).decision == "deny"
    assert evaluate_command(["python", "check.py\npytest"], 30).decision == "deny"


@pytest.mark.skipif(sys.platform == "win32", reason="SDK skills import may load python-magic unsafely on Windows")
def test_sdk_agent_import_is_safe_without_loading_libmagic():
    from trpc_agent_sdk.agents import LlmAgent

    assert LlmAgent.__name__ == "LlmAgent"


async def test_agent_filter_uses_agent_context_metadata_not_missing_state():
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.filter import FilterResult
    from examples.skills_code_review_agent.agent.filter import CodeReviewAgentFilter

    context = AgentContext()
    await CodeReviewAgentFilter()._before(context, None, FilterResult())

    assert context.metadata["code_review_started"] is True


async def test_skill_run_filter_injects_workspace_inputs_from_context():
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.filter import FilterResult
    from examples.skills_code_review_agent.agent.filter import CodeReviewSkillRunFilter

    context = AgentContext()
    context.metadata["code_review_workspace_inputs"] = [{"src": "host://C:/input.diff", "dst": "work/inputs/input.diff", "mode": "copy"}]
    args = {"command": "python scripts/pr-analyzer.py --diff-file work/inputs/input.diff", "timeout": 30}
    await CodeReviewSkillRunFilter()._before(context, args, FilterResult())

    assert args["inputs"] == context.metadata["code_review_workspace_inputs"]


def test_model_audit_uses_trusted_inputs_without_persisting_host_paths():
    from types import SimpleNamespace
    from examples.skills_code_review_agent.agent.filter import before_model_audit

    host_path = "host://C:/private/repository/input.diff"
    agent = SimpleNamespace(
        model=SimpleNamespace(_model_name="test-model"),
        _code_review_workspace_inputs=[{"src": host_path, "dst": "work/inputs/input.diff", "mode": "copy"}],
    )
    context = SimpleNamespace(agent=agent, agent_context=SimpleNamespace(metadata={}))
    class Request:
        contents = [SimpleNamespace(parts=[SimpleNamespace(text=json.dumps({
            "workspace_inputs": [{"dst": "work/inputs/input.diff", "mode": "copy"}],
        }))])]

        def model_dump(self):
            return {"contents": [{"workspace_inputs": [{"dst": "work/inputs/input.diff", "mode": "copy"}]}]}

    request = Request()

    before_model_audit(context, request)

    assert context.agent_context.metadata["code_review_workspace_inputs"][0]["src"] == host_path
    assert host_path not in json.dumps(context.agent_context.metadata["code_review_model_pending"]["input"])


def test_model_audit_seeds_trusted_task_output_configuration():
    from types import SimpleNamespace
    from examples.skills_code_review_agent.agent.filter import before_model_audit

    agent = SimpleNamespace(
        model=SimpleNamespace(_model_name="test-model"),
        _code_review_workspace_inputs=[],
        _code_review_task_id="cr-trusted-task",
        _code_review_output_dir="C:/trusted/output",
    )
    context = SimpleNamespace(agent=agent, agent_context=SimpleNamespace(metadata={}))
    request = SimpleNamespace(contents=[])

    before_model_audit(context, request)

    assert context.agent_context.metadata["code_review_task_id"] == "cr-trusted-task"
    assert context.agent_context.metadata["code_review_output_dir"] == "C:/trusted/output"


async def test_skill_run_filter_rejects_interactive_stdin():
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.filter import FilterResult
    from examples.skills_code_review_agent.agent.filter import CodeReviewSkillRunFilter

    context = AgentContext()
    result = FilterResult()
    args = {"command": "python", "stdin": "print('unsafe')", "timeout": 30}
    await CodeReviewSkillRunFilter()._before(
        context,
        args,
        result,
    )

    assert "needs_human_review" in str(result.error)
    assert args["command"] == "python"
    assert args["stdin"] == "print('unsafe')"


async def test_skill_run_filter_blocks_non_integer_timeout_without_raising():
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.filter import FilterResult
    from examples.skills_code_review_agent.agent.filter import CodeReviewSkillRunFilter

    result = FilterResult()
    await CodeReviewSkillRunFilter()._before(
        AgentContext(), {"command": "python scripts/check.py", "timeout": "30s"}, result
    )

    assert "needs_human_review" in str(result.error)
    assert "timeout" in str(result.error)


async def test_skill_run_filter_escalates_ruff_without_staged_source():
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.filter import FilterResult
    from examples.skills_code_review_agent.agent.filter import CodeReviewSkillRunFilter

    context = AgentContext()
    context.metadata["code_review_workspace_inputs"] = [{"src": "host://C:/input.diff", "dst": "work/inputs/input.diff", "mode": "copy"}]
    result = FilterResult()
    await CodeReviewSkillRunFilter()._before(context, {"command": "ruff check demo/client.py", "timeout": 30}, result)

    assert "needs_human_review" in str(result.error)

def test_review_agent_only_exposes_noninteractive_skill_tools():
    assert REVIEW_SKILL_TOOL_NAMES == ["skill_load", "skill_select_docs", "skill_list_docs"]
    assert "never call\nparse_review_input" in INSTRUCTION
    assert "Never use skill_exec" in INSTRUCTION


def test_plan_review_actions_uses_skill_relative_analyzer_command_without_stdin():
    actions = plan_review_actions(["analyze_pr"], [{"dst": "work/inputs/input.diff"}])

    assert actions == [{
        "skill": "code-review",
        "command": "python scripts/pr-analyzer.py --diff-file ../../work/inputs/input.diff",
        "cwd": "",
        "stdin": "",
        "timeout": 30,
        "inputs": [{"dst": "work/inputs/input.diff"}],
    }]


def test_skill_plan_uses_workspace_relative_input_from_skill_cwd():
    skill = (FIXTURES.parents[1] / "skills" / "code-review" / "SKILL.md").read_text(encoding="utf-8")

    assert "../../work/inputs/input.diff" in skill


def test_sqlite_task_query_reads_report(tmp_path):
    report = save_review_report(task_id="audit", findings=[], evidence={"changed_lines": []}, output_dir=str(tmp_path))
    task = ReviewStorage(tmp_path / "reviews.sqlite").get_task("audit")
    assert task["task_id"] == report["task_id"]
    assert task["metrics"]["finding_count"] == 0


def test_sqlite_task_query_tolerates_missing_legacy_metrics_row(tmp_path):
    report = save_review_report(task_id="legacy-metrics", findings=[], evidence={"changed_lines": []}, output_dir=str(tmp_path))
    with sqlite3.connect(tmp_path / "reviews.sqlite") as db:
        db.execute("DELETE FROM review_metrics WHERE task_id = ?", (report["task_id"],))

    task = ReviewStorage(tmp_path / "reviews.sqlite").get_task(report["task_id"])

    assert task["metrics"] == {"finding_count": 0, "sandbox_run_count": 0, "blocked_count": 0}


def test_sqlite_connections_are_closed_after_each_storage_operation(tmp_path, monkeypatch):
    import examples.skills_code_review_agent.agent.storage as storage_module

    real_connect = sqlite3.connect
    connections = []

    class TrackingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def close(self):
            self.closed = True
            self.connection.close()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def connect(path):
        connection = TrackingConnection(real_connect(path))
        connections.append(connection)
        return connection

    monkeypatch.setattr(storage_module.sqlite3, "connect", connect)
    storage = ReviewStorage(tmp_path / "audit.sqlite")
    storage.save_native("task", {"status": "completed", "metrics": {"finding_count": 0, "sandbox_run_count": 0, "blocked_count": 0},
                                 "findings": [], "filter_decisions": [], "skill_runs": [], "model_runs": []}, "digest")
    storage.get_task("task")

    assert connections and all(connection.closed for connection in connections)


def test_report_persists_complete_skill_and_model_audit(tmp_path):
    report = save_review_report(
        task_id="audit-runs", findings=[], output_dir=str(tmp_path),
        evidence={
            "changed_lines": [],
            "skill_runs": [{"runtime": "docker", "command": "ruff check work/inputs/a.py", "stdout": "ok", "stderr": "", "exit_code": 0, "timed_out": False, "duration_seconds": 0.25, "output_files": [{"name": "out/result.txt", "content": "ok"}]}],
            "model_runs": [{"model": "test-model", "input": {"diff": "[REDACTED]"}, "output": {"findings": []}, "duration_seconds": 0.1, "exception": ""}],
            "filter_decisions": [{"decision": "allow", "reason": "allowed"}],
        },
    )
    task = ReviewStorage(tmp_path / "reviews.sqlite").get_task(report["task_id"])
    assert task["skill_runs"][0]["stderr"] == ""
    assert task["skill_runs"][0]["output_files"][0]["name"] == "out/result.txt"
    assert task["model_runs"][0]["model"] == "test-model"


def test_report_and_sqlite_audit_redact_host_workspace_paths(tmp_path):
    host_path = "host://C:/private/repository/input.diff"
    report = save_review_report(
        task_id="audit-host-path", findings=[], output_dir=str(tmp_path),
        evidence={
            "changed_lines": [],
            "skill_runs": [{"runtime": "docker", "command": "python check.py", "stdout": host_path, "stderr": "",
                            "exit_code": 0, "timed_out": False, "duration_seconds": 0, "output_files": []}],
            "model_runs": [{"model": "test-model", "input": {"input": host_path}, "output": {},
                            "duration_seconds": 0, "exception": host_path}],
            "filter_decisions": [],
        },
    )

    report_text = Path(report["json_path"]).read_text(encoding="utf-8")
    task_text = json.dumps(ReviewStorage(tmp_path / "reviews.sqlite").get_task("audit-host-path"))
    assert host_path not in report_text
    assert host_path not in task_text


def test_report_compacts_audit_and_emits_monitoring_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_REVIEW_TOOL_OUTPUT_MAX_KIB", "1")
    monkeypatch.setenv("CODE_REVIEW_MODEL_AUDIT_MAX_KIB", "1")
    monkeypatch.setenv("CODE_REVIEW_MODEL_RUN_MAX_COUNT", "1")
    report = save_review_report(
        task_id="compact-audit", findings=[], output_dir=str(tmp_path),
        evidence={
            "changed_lines": [],
            "skill_runs": [{"runtime": "docker", "command": "python check.py", "status": "failed", "stdout": "x" * 3000,
                            "stderr": "boom", "exit_code": 1, "timed_out": False, "duration_seconds": 0.25, "output_files": []}],
            "model_runs": [
                {"model": "first", "input": {"payload": "a" * 3000}, "output": {"text": "b" * 3000}, "duration_seconds": 0.1, "exception": ""},
                {"model": "second", "input": {}, "output": {}, "duration_seconds": 0.2, "exception": "RateLimitError"},
            ],
            "filter_decisions": [{"decision": "allow", "reason": "ok"}],
        },
    )
    run = report["skill_runs"][0]
    assert run["stdout_truncated"] is True
    assert len(report["model_runs"]) == 1
    assert report["model_runs"][0]["input_summary"]["truncated"] is True
    assert report["metrics"]["severity_distribution"] == {}
    assert report["metrics"]["sandbox_duration_seconds"] == 0.25
    assert report["metrics"]["exception_distribution"] == {"RateLimitError": 1}
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert "## 监控指标" in markdown
    assert "## 沙箱执行摘要" in markdown
    assert "## 模型调用摘要" in markdown


def test_cli_dry_run_writes_task_scoped_report(tmp_path):
    completed = subprocess.run([sys.executable, "examples/skills_code_review_agent/run_review.py", "--diff-file",
                                str(FIXTURES / "02_hardcoded_token" / "input.diff"), "--output-dir", str(tmp_path), "--dry-run"],
                               cwd=Path(__file__).parents[2], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    report_path = Path(completed.stdout.strip().split(": ", 1)[1])
    assert report_path.exists()
