#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for canonical report rendering and persistence payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.report import (  # noqa: E402
    CanonicalReportWriter,
    ReportSecretLeakError,
    ReportWriteError,
)
from code_review.store import SqlReviewStore  # noqa: E402


def _finding(*, line: int, line_side: str, severity: str) -> dict[str, object]:
    """构造符合报告 schema 的确定性 finding 测试数据。"""

    return {
        "severity": severity,
        "category": "secrets",
        "file": "src/service.py",
        "line": line,
        "line_side": line_side,
        "title": "轮换已提交的凭据",
        "evidence": "credential=[REDACTED:github_pat]",
        "recommendation": "立即轮换凭据并清理历史记录。",
        "confidence": 0.95,
        "source": "rule-engine",
        "rule_id": "secrets.github-pat",
        "bucket": "findings",
        "dedup_key": f"src/service.py:{line}:secrets",
        "extra": {"also_matched": []},
    }


def _report_payload() -> dict[str, object]:
    """构造包含新旧侧位置和输入范围的最小完整报告。"""

    return {
        "schema_version": "1.0.0",
        "rule_pack_version": "1.0.0",
        "config_digest": "b" * 64,
        "input_sha256": "a" * 64,
        "task_id": "report-task-001",
        "status": "completed_with_warnings",
        "input_summary": {
            "source_kind": "diff_file",
            "file_count": 2,
            "hunk_count": 2,
            "additions": 3,
            "deletions": 1,
            "files": [
                {
                    "path": "src/service.py",
                    "status": "modified",
                    "review_scope": "changed_lines",
                },
                {
                    "path": "config/removed.env",
                    "status": "deleted",
                    "review_scope": "deleted_lines",
                },
            ],
            "parse_warnings": [],
        },
        "findings": [
            _finding(line=8, line_side="new", severity="high"),
            _finding(line=12, line_side="old", severity="medium"),
        ],
        "needs_human_review": [],
        "warnings": [
            {
                "code": "local_runtime",
                "message": "本地运行时无法强制网络隔离。",
                "stage": "sandbox",
            }
        ],
        "suppressed": {"count": 1, "reasons": {"low_confidence": 1}},
        "filter_summary": {
            "allow_count": 1,
            "deny_count": 0,
            "needs_human_review_count": 0,
            "events": [],
        },
        "sandbox_summary": {"runtime_type": "local", "run_count": 0, "runs": []},
        "metrics": {
            "total_duration_ms": 25,
            "sandbox_duration_ms": 0,
            "llm_duration_ms": 0,
            "tool_call_count": 1,
            "sandbox_run_count": 0,
            "filter_block_count": 0,
            "filter_review_count": 0,
            "finding_count": 2,
            "warning_count": 1,
            "needs_human_review_count": 0,
            "suppressed_count": 1,
            "severity_distribution": {"high": 1, "medium": 1},
            "category_distribution": {"secrets": 2},
            "error_type_distribution": {},
            "runtime_type": "local",
            "python_version": "3.12",
            "platform": "Windows",
        },
        "final_conclusion": {
            "summary": "发现需要处理的凭据风险。",
            "recommendations": ["优先轮换已泄漏凭据。"],
        },
    }


def _db_url(path: Path) -> str:
    """返回隔离测试 SQLite 文件的 SQLAlchemy URL。"""

    return f"sqlite+pysqlite:///{path.as_posix()}"


def test_writer_validates_canonical_json_and_renders_line_sides(tmp_path: Path) -> None:
    """验证报告写入器校验 JSON 并从同一对象渲染新旧侧位置。"""

    writer = CanonicalReportWriter()
    result = writer.write(_report_payload(), tmp_path)

    report = json.loads(result.json_path.read_text(encoding="utf-8"))
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert report["input_summary"]["source_kind"] == "diff_file"
    assert "审查范围：changed_lines" in markdown
    assert "新侧行 8" in markdown
    assert "旧侧行 12" in markdown
    assert all(f"## {number}." in markdown for number in range(1, 9))


def test_writer_stably_renders_json_markdown_and_store_payload(tmp_path: Path) -> None:
    """验证 JSON、Markdown 和 SQLite 统计均源自同一稳定报告对象。"""

    writer = CanonicalReportWriter()
    output_dir = tmp_path / "output"
    payload = _report_payload()
    first = writer.write(payload, output_dir)
    first_json = first.json_path.read_bytes()
    first_markdown = first.markdown_path.read_bytes()

    payload["findings"].reverse()
    payload["input_summary"]["files"].reverse()
    second = writer.write(payload, output_dir)

    database = tmp_path / "review.db"
    store = SqlReviewStore(_db_url(database))
    store.initialize()
    store.create_task(
        {
            "id": second.report["task_id"],
            "status": "running",
            "input_type": "diff_file",
            "input_ref": "changes.diff",
            "diff_summary": {},
            "config": {"schema_version": "1.0.0"},
        }
    )
    store.save_report(second.report["task_id"], writer.to_store_payload(payload))
    bundle = store.get_task_bundle(second.report["task_id"])
    store.close()

    assert second.json_path.read_bytes() == first_json
    assert second.markdown_path.read_bytes() == first_markdown
    assert bundle is not None
    stored_report = bundle["report"]["report"]
    assert stored_report == second.report
    assert bundle["report"]["severity_stats"] == {
        "schema_version": "1.0.0",
        "high": 1,
        "medium": 1,
    }
    assert "- high：1" in first_markdown.decode("utf-8")


def test_writer_renders_an_empty_finding_report(tmp_path: Path) -> None:
    """验证没有正式 finding 时仍生成完整且可读的报告。"""

    payload = _report_payload()
    payload["findings"] = []
    payload["needs_human_review"] = []
    payload["warnings"] = []
    payload["suppressed"] = {"count": 0, "reasons": {}}
    payload["metrics"]["finding_count"] = 0
    payload["metrics"]["warning_count"] = 0
    payload["metrics"]["suppressed_count"] = 0
    payload["metrics"]["severity_distribution"] = {}
    payload["metrics"]["category_distribution"] = {}
    payload["final_conclusion"] = {
        "summary": "未发现需要处理的问题。",
        "recommendations": [],
    }

    result = CanonicalReportWriter().write(payload, tmp_path)
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert result.report["findings"] == []
    assert result.report["suppressed"]["count"] == 0
    assert markdown.count("- 无。") >= 4
    assert "未发现需要处理的问题。" in markdown


def test_writer_blocks_plaintext_before_any_report_output(tmp_path: Path) -> None:
    """验证最终出口扫描阻止明文凭据写入 JSON 或 Markdown。"""

    plaintext = "ghp_" + "a" * 36
    payload = _report_payload()
    payload["findings"][0]["evidence"] = plaintext

    with pytest.raises(ReportSecretLeakError) as error:
        CanonicalReportWriter().write(payload, tmp_path)

    assert plaintext not in str(error.value)
    assert not (tmp_path / "review_report.json").exists()
    assert not (tmp_path / "review_report.md").exists()


def test_writer_failure_leaves_no_partial_target_file(tmp_path: Path) -> None:
    """验证原子写入器失败时不会把半写报告暴露给调用方。"""

    def fail_before_replace(path: Path, _payload: bytes) -> None:
        """模拟替换目标文件前的 I/O 错误。"""

        assert not path.exists()
        raise OSError("synthetic write failure")

    writer = CanonicalReportWriter(atomic_writer=fail_before_replace)

    with pytest.raises(ReportWriteError):
        writer.write(_report_payload(), tmp_path)

    assert not (tmp_path / "review_report.json").exists()
    assert not (tmp_path / "review_report.md").exists()


def test_markdown_renderer_escapes_untrusted_finding_text(
    tmp_path: Path,
) -> None:
    """验证 diff 与 finding 文本不能注入链接、图片或原始 HTML。"""

    payload = _report_payload()
    payload["input_summary"]["files"][0]["path"] = (
        "src/[label](https://example.test/a).py"
    )
    payload["findings"][0]["file"] = "src/`name`[x](https://example.test).py"
    payload["findings"][0]["title"] = "问题 ![pixel](https://example.test/p.png)"
    payload["findings"][0]["evidence"] = "<img src=https://example.test/p.png>"
    payload["findings"][0]["recommendation"] = "[click](https://example.test)"

    result = CanonicalReportWriter().write(payload, tmp_path)
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert "![pixel](" not in markdown
    assert "<img " not in markdown
    assert "[click](" not in markdown
    assert "[label](" not in markdown
