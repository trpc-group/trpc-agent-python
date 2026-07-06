# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI (in-process) + report document/markdown structure (acceptance criterion 8)."""

import json
import os

import run_agent

from .helpers import run_fixture

MD_SECTIONS = (
    "## Findings 摘要",
    "## 严重级别统计 Severity Stats",
    "## 人工复核项 Needs Human Review",
    "## Filter 拦截摘要 Filter Blocks",
    "## 监控指标 Metrics",
    "## 沙箱执行摘要 Sandbox Runs",
    "## 修复建议 Recommendations",
)


async def test_report_sections_complete(tmp_path):
    run = await run_fixture("security_issue", tmp_path)
    try:
        for section in MD_SECTIONS:
            assert section in run.report_md, section
        # actionable recommendations present and non-empty
        assert run.report["recommendations"]
        for rec in run.report["recommendations"]:
            assert rec["recommendation"].strip()
            assert rec["file"] and rec["severity"]
        # severity stats cover every level
        assert set(run.report["severity_stats"]) == {"critical", "high", "medium", "low", "info"}
        # internal bookkeeping keys (e.g. _persisted) must not leak into the
        # public report document
        for event in run.report["filter_summary"]["events"]:
            assert not any(key.startswith("_") for key in event), event
    finally:
        await run.store.close()


def test_cli_review_show_list_init_db(tmp_path, capsys, monkeypatch):
    """Full CLI loop in-process: init-db → review → show → list."""
    monkeypatch.chdir(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cli.db"
    out_dir = str(tmp_path / "out")

    assert run_agent.main(["init-db", "--db-url", db_url]) == 0
    init_output = capsys.readouterr().out
    assert "cr_review_task" in init_output

    assert run_agent.main(["review", "--fixture", "duplicate_finding", "--dry-run",
                           "--db-url", db_url, "--out-dir", out_dir]) == 0
    review_output = capsys.readouterr().out
    assert "task id" in review_output
    task_id = review_output.split("task id     :")[1].splitlines()[0].strip()
    assert os.path.isfile(os.path.join(out_dir, "review_report.json"))
    assert os.path.isfile(os.path.join(out_dir, "review_report.md"))

    assert run_agent.main(["show", "--task-id", task_id, "--db-url", db_url]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["task"]["id"] == task_id
    assert bundle["report"]["report"]["task_id"] == task_id
    assert bundle["sandbox_runs"] and bundle["findings"]

    assert run_agent.main(["list", "--db-url", db_url]) == 0
    assert task_id in capsys.readouterr().out

    # unknown task id → clean nonzero exit
    assert run_agent.main(["show", "--task-id", "nope", "--db-url", db_url]) == 1


def test_cli_default_db_path_shared_across_subcommands(tmp_path, capsys, monkeypatch):
    """Fresh-checkout quick start: all subcommands share out/review.db by default.

    Regression: ``review`` defaulted its sqlite DB to out/review.db but never
    created the out dir (crash on a fresh checkout), while ``show``/``list``
    defaulted to ./review.db — so the documented review → show workflow
    reported "task not found" without an explicit --db-url."""
    monkeypatch.chdir(tmp_path)

    assert run_agent.main(["review", "--fixture", "security_issue", "--dry-run"]) == 0
    review_output = capsys.readouterr().out
    task_id = review_output.split("task id     :")[1].splitlines()[0].strip()
    assert os.path.isfile(tmp_path / "out" / "review.db")

    assert run_agent.main(["show", "--task-id", task_id]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["task"]["id"] == task_id

    assert run_agent.main(["list"]) == 0
    assert task_id in capsys.readouterr().out


def test_cli_inject_sandbox_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cli2.db"
    assert run_agent.main(["review", "--fixture", "sandbox_failure", "--dry-run",
                           "--inject-sandbox-failure", "--db-url", db_url,
                           "--out-dir", str(tmp_path / "out2")]) == 0
    output = capsys.readouterr().out
    assert "completed_with_errors" in output


def test_cli_diff_file_input(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    diff_path = tmp_path / "change.diff"
    diff_path.write_text(
        "diff --git a/svc.py b/svc.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/svc.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+os.system('reboot')\n",
        encoding="utf-8")
    assert run_agent.main(["review", "--diff-file", str(diff_path), "--dry-run",
                           "--db-url", f"sqlite+aiosqlite:///{tmp_path}/cli3.db",
                           "--out-dir", str(tmp_path / "out3")]) == 0
    with open(tmp_path / "out3" / "review_report.json", encoding="utf-8") as fh:
        report = json.load(fh)
    assert any(finding["category"] == "security_risk" for finding in report["findings"])


def test_cli_input_errors_exit_cleanly(tmp_path, capsys, monkeypatch):
    """Bad inputs → clean message on stderr + exit code 2 (no traceback)."""
    monkeypatch.chdir(tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path}/err.db"

    assert run_agent.main(["review", "--fixture", "no_such_fixture", "--dry-run",
                           "--db-url", db_url]) == 2
    err = capsys.readouterr().err
    assert "unknown fixture" in err and "available:" in err

    assert run_agent.main(["review", "--diff-file", "missing.diff", "--dry-run",
                           "--db-url", db_url]) == 2
    assert "input error" in capsys.readouterr().err

    assert run_agent.main(["review", "--repo-path", str(tmp_path), "--dry-run",
                           "--db-url", db_url]) == 2
    assert "not a git repository" in capsys.readouterr().err


async def test_report_json_is_valid_and_findings_sorted_by_severity(tmp_path):
    run = await run_fixture("security_issue", tmp_path)
    try:
        with open(run.result.report_paths["json"], encoding="utf-8") as fh:
            document = json.load(fh)
        ranks = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        rec_ranks = [ranks[rec["severity"]] for rec in document["recommendations"]]
        assert rec_ranks == sorted(rec_ranks, reverse=True)
    finally:
        await run.store.close()
