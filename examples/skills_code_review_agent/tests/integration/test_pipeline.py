#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for the one-path review pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.inputs import FixturePayload  # noqa: E402
from code_review.pipeline import PipelineFatalError, ReviewPipeline  # noqa: E402
from code_review.report import CanonicalReportWriter  # noqa: E402
from code_review.store import SqlReviewStore  # noqa: E402


class _AllowGovernance:
    """提供允许执行且不包含敏感内容的最小治理端口替身。"""

    def decide(self, **_arguments: Any) -> dict[str, object]:
        """返回允许决定和一条可落库的治理事件。"""

        return {
            "action": "allow",
            "events": [
                {
                    "stage": "pre_execution",
                    "target": "run_checks",
                    "action": "allow",
                    "rule": "manifest",
                    "reasons": ["manifest_verified"],
                }
            ],
            "warnings": [],
        }


class _DenyGovernance:
    """提供拒绝决定，验证 pipeline 不会绕过 Filter 执行沙箱。"""

    def decide(self, **_arguments: Any) -> dict[str, object]:
        """返回包含安全原因代码的拒绝治理事件。"""

        return {
            "action": "deny",
            "events": [
                {
                    "stage": "pre_execution",
                    "target": "run_checks",
                    "action": "deny",
                    "rule": "network_policy",
                    "reasons": ["network_proof_missing"],
                }
            ],
            "warnings": [],
        }


class _SecretFindingSandbox:
    """模拟在隔离任务域中检出真实格式凭据的运行时端口。"""

    runtime_type = "fake"

    def __init__(self, secret: str) -> None:
        """保存仅用于模拟沙箱原始 finding 的合成凭据。"""

        self._secret = secret
        self.execute_calls = 0
        self.cleanup_calls = 0

    def execute(self, **_arguments: Any) -> dict[str, object]:
        """返回尚未经过宿主二次脱敏的 sandbox finding。"""

        self.execute_calls += 1
        return {
            "status": "ok",
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "stdout_excerpt": self._secret,
            "stderr_excerpt": "",
            "error_type": None,
            "duration_ms": 7,
            "findings": [
                {
                    "severity": "high",
                    "category": "secrets",
                    "file": "config/.env",
                    "line": 1,
                    "line_side": "new",
                    "title": "轮换已提交的凭据",
                    "evidence": f"TOKEN={self._secret}",
                    "recommendation": "轮换该凭据。",
                    "confidence": 0.99,
                    "source": "rule-engine",
                    "rule_id": "secrets.github-pat",
                }
            ],
        }

    def cleanup(self, **_arguments: Any) -> None:
        """记录 pipeline 在 finally 中释放了任务 workspace。"""

        self.cleanup_calls += 1


class _TimeoutCleanupFailSandbox:
    """模拟超时结果与 workspace 清理失败的隔离运行时端口。"""

    runtime_type = "fake"

    def __init__(self) -> None:
        """初始化可验证的执行和清理调用计数。"""

        self.execute_calls = 0
        self.cleanup_calls = 0

    def execute(self, **_arguments: Any) -> dict[str, object]:
        """返回超时数据，要求 pipeline 转为 warning 而非崩溃。"""

        self.execute_calls += 1
        return {
            "status": "timeout",
            "exit_code": None,
            "timed_out": True,
            "truncated": False,
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "error_type": "timeout",
            "duration_ms": 30,
            "findings": [],
        }

    def cleanup(self, **_arguments: Any) -> None:
        """模拟清理失败，异常文本不应进入任何持久化出口。"""

        self.cleanup_calls += 1
        raise OSError(r"C:\sensitive-workspace\cleanup-failed")


class _WarningSandbox:
    """返回可持久化的非致命 sandbox 失败结果，用于验证 pipeline 的失败即数据契约。"""

    runtime_type = "fake"

    def __init__(self, *, status: str, error_type: str, truncated: bool) -> None:
        """保存受控失败形态，不携带代码、路径或敏感原文。"""

        self._status = status
        self._error_type = error_type
        self._truncated = truncated
        self.execute_calls = 0

    def execute(self, **_arguments: Any) -> dict[str, object]:
        """返回非零或截断的结构化 run 摘要，供 pipeline 落库和生成 warning。"""

        self.execute_calls += 1
        return {
            "status": self._status,
            "exit_code": 9 if self._status == "failed" else 0,
            "timed_out": False,
            "truncated": self._truncated,
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "error_type": self._error_type,
            "duration_ms": 1,
            "findings": [],
        }

    def cleanup(self, **_arguments: Any) -> None:
        """fake sandbox 不创建 workspace，因此 cleanup 是无副作用操作。"""


class _LowConfidenceSandbox:
    """返回一条应进入 suppressed 摘要而不应逐条落库的候选。"""

    runtime_type = "fake"

    def execute(self, **_arguments: Any) -> dict[str, object]:
        """返回低置信候选，供 pipeline 验证隐私收敛后的持久化边界。"""

        return {
            "status": "ok",
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "error_type": None,
            "duration_ms": 1,
            "findings": [
                {
                    "severity": "low",
                    "category": "missing-tests",
                    "file": "src/service.py",
                    "line": 3,
                    "line_side": "new",
                    "title": "低置信候选",
                    "evidence": "production change",
                    "recommendation": "人工确认是否需要测试。",
                    "confidence": 0.30,
                    "source": "rule-engine",
                    "rule_id": "tests.low-confidence",
                }
            ],
        }

    def cleanup(self, **_arguments: Any) -> None:
        """fake sandbox 不创建 workspace，因此无需清理资源。"""


class _FailingEnhancer:
    """模拟模型或 Runner 异常，验证增强故障不会中断确定性评审交付。"""

    mode = "fake"

    def enhance(self, _report: dict[str, object]) -> dict[str, object]:
        """抛出不应离开 pipeline 的异常，也不携带原始输入内容。"""

        raise RuntimeError("fake_enhancement_failure")


def _db_url(path: Path) -> str:
    """返回隔离测试数据库的 SQLAlchemy URL。"""

    return f"sqlite+pysqlite:///{path.as_posix()}"


def test_pipeline_redacts_sandbox_output_and_persists_review_bundle(tmp_path: Path) -> None:
    """验证唯一 pipeline 链路输出报告、五类 DB 记录且不泄漏原始凭据。"""

    secret = "ghp_" + "a" * 36
    database = tmp_path / "review.db"
    sandbox = _SecretFindingSandbox(secret)
    store = SqlReviewStore(_db_url(database))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "pipeline-task-001",
    )

    result = pipeline.run(
        fixture=FixturePayload(
            payload_type="files",
            file_contents={"config/.env": f"TOKEN={secret}\n"},
        )
    )
    bundle = store.get_task_bundle(result.task_id)
    serialized_bundle = json.dumps(bundle, ensure_ascii=False, sort_keys=True)

    assert result.status == "completed"
    assert sandbox.execute_calls == 1
    assert sandbox.cleanup_calls == 1
    assert bundle is not None
    assert len(bundle["sandbox_runs"]) == 1
    assert len(bundle["filter_events"]) == 1
    assert len(bundle["findings"]) == 1
    assert bundle["report"]["report"] == result.report
    assert result.report["findings"][0]["category"] == "secrets"
    assert secret not in serialized_bundle
    assert secret.encode("utf-8") not in database.read_bytes()
    assert secret not in result.json_path.read_text(encoding="utf-8")
    assert secret not in result.markdown_path.read_text(encoding="utf-8")


def test_pipeline_converts_timeout_and_cleanup_failure_to_warnings(tmp_path: Path) -> None:
    """验证非致命沙箱失败仍生成报告，并在 finally 中记录无路径清理告警。"""

    sandbox = _TimeoutCleanupFailSandbox()
    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "pipeline-task-timeout",
    )

    result = pipeline.run(
        fixture=FixturePayload(
            payload_type="files",
            file_contents={"src/service.py": "def run():\n    return None\n"},
        )
    )
    bundle = store.get_task_bundle(result.task_id)
    warning_codes = {warning["code"] for warning in result.report["warnings"]}
    serialized_bundle = json.dumps(bundle, ensure_ascii=False, sort_keys=True)

    assert result.status == "completed_with_warnings"
    assert sandbox.execute_calls == 1
    assert sandbox.cleanup_calls == 1
    assert bundle is not None
    assert bundle["sandbox_runs"][0]["status"] == "timeout"
    assert bundle["sandbox_runs"][0]["timed_out"] is True
    assert {"sandbox_timeout", "workspace_cleanup_error"} <= warning_codes
    assert "C:\\sensitive-workspace" not in serialized_bundle
    assert result.json_path.exists()
    assert result.markdown_path.exists()


@pytest.mark.parametrize(
    ("status", "error_type", "truncated", "warning_code"),
    (
        ("failed", "nonzero_exit", False, "sandbox_failed"),
        ("error", "output_truncated", True, "sandbox_output_truncated"),
    ),
)
def test_pipeline_persists_nonzero_and_truncated_sandbox_warnings(
    tmp_path: Path,
    status: str,
    error_type: str,
    truncated: bool,
    warning_code: str,
) -> None:
    """验证非零与截断均落入 sandbox run、报告 warning 和 SQLite bundle，且任务仍可交付。"""

    sandbox = _WarningSandbox(status=status, error_type=error_type, truncated=truncated)
    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: f"pipeline-task-{error_type}",
    )
    try:
        result = pipeline.run(
            fixture=FixturePayload(
                payload_type="files",
                file_contents={"src/service.py": "def run():\n    return None\n"},
            )
        )
        bundle = store.get_task_bundle(result.task_id)

        assert result.status == "completed_with_warnings"
        assert sandbox.execute_calls == 1
        assert bundle is not None
        assert bundle["sandbox_runs"][0]["status"] == status
        assert bundle["sandbox_runs"][0]["error_type"] == error_type
        assert bundle["sandbox_runs"][0]["truncated"] is truncated
        assert warning_code in {warning["code"] for warning in result.report["warnings"]}
        assert result.json_path.is_file()
        assert result.markdown_path.is_file()
        assert "C:\\" not in json.dumps(bundle, ensure_ascii=False, sort_keys=True)
    finally:
        store.close()


def test_pipeline_short_circuits_denied_sandbox_execution(tmp_path: Path) -> None:
    """验证拒绝治理只留审计和 warning，不允许沙箱产生任何副作用。"""

    sandbox = _TimeoutCleanupFailSandbox()
    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_DenyGovernance(),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "pipeline-task-denied",
    )

    result = pipeline.run(
        fixture=FixturePayload(
            payload_type="files",
            file_contents={"src/service.py": "def run():\n    return None\n"},
        )
    )
    bundle = store.get_task_bundle(result.task_id)

    assert result.status == "completed_with_warnings"
    assert sandbox.execute_calls == 0
    assert sandbox.cleanup_calls == 1
    assert bundle is not None
    assert bundle["sandbox_runs"] == []
    assert bundle["filter_events"][0]["action"] == "deny"
    assert "filter_deny" in {warning["code"] for warning in result.report["warnings"]}


def test_pipeline_converts_llm_enhancement_failure_to_warning(tmp_path: Path) -> None:
    """验证模型配置、网络或 Runner 异常都降级为脱敏 warning 并保持数据库报告完整。"""

    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=_WarningSandbox(status="ok", error_type="", truncated=False),
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "pipeline-task-llm-failure",
        model_mode="fake",
        llm_enhancer=_FailingEnhancer(),
    )

    result = pipeline.run(
        fixture=FixturePayload(
            payload_type="files",
            file_contents={"src/service.py": "def run():\n    return None\n"},
        )
    )
    bundle = store.get_task_bundle(result.task_id)

    assert result.status == "completed_with_warnings"
    assert "llm_enhancement_failed" in {warning["code"] for warning in result.report["warnings"]}
    assert result.report["metrics"]["error_type_distribution"] == {"llm_enhancement_failed": 1}
    assert bundle is not None
    assert bundle["task"]["status"] == "completed_with_warnings"


def test_pipeline_persists_only_suppressed_summary(
    tmp_path: Path,
) -> None:
    """验证低置信候选只保留计数与原因，不逐条写入 cr_finding。"""

    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=_LowConfidenceSandbox(),
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "pipeline-task-suppressed",
    )

    result = pipeline.run(
        fixture=FixturePayload(
            payload_type="files",
            file_contents={"src/service.py": "def run():\n    return None\n"},
        )
    )
    bundle = store.get_task_bundle(result.task_id)

    assert result.report["suppressed"] == {
        "count": 1,
        "reasons": {"low_confidence": 1},
    }
    assert bundle is not None
    assert bundle["findings"] == []


def test_pipeline_marks_task_failed_when_report_write_fails(
    tmp_path: Path,
) -> None:
    """验证报告原子写入失败会成为致命错误并把任务状态更新为 failed。"""

    def _fail_write(_path: Path, _payload: bytes) -> None:
        """模拟不包含路径或输入内容的报告写入失败。"""

        raise OSError("synthetic_report_write_failure")

    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowGovernance(),
        sandbox=_WarningSandbox(
            status="ok",
            error_type="",
            truncated=False,
        ),
        output_dir=tmp_path / "reports",
        report_writer=CanonicalReportWriter(atomic_writer=_fail_write),
        task_id_factory=lambda: "pipeline-task-report-failure",
    )

    with pytest.raises(
        PipelineFatalError,
        match="pipeline_report_or_persistence_failed",
    ):
        pipeline.run(
            fixture=FixturePayload(
                payload_type="files",
                file_contents={"src/service.py": "def run():\n    return None\n"},
            )
        )

    bundle = store.get_task_bundle("pipeline-task-report-failure")
    assert bundle is not None
    assert bundle["task"]["status"] == "failed"
