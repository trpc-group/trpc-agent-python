#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Public-fixture end-to-end contracts for the deterministic review pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from code_review.inputs import FixturePayload  # noqa: E402
from code_review.pipeline import ReviewPipeline  # noqa: E402
from code_review.redaction import contains_plaintext_secret  # noqa: E402
from code_review.store import SqlReviewStore  # noqa: E402
from run_checks import _findings  # noqa: E402


FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "diffs"
SIMPLE_FIXTURE_NAMES = (
    "01_clean_simple",
    "02_security_simple",
    "03_async_leak_simple",
    "04_db_lifecycle_simple",
    "05_missing_tests_simple",
    "06_duplicate_finding_simple",
    "07_sandbox_failure_simple",
    "08_secret_redaction_simple",
)
COMPLEX_FIXTURE_NAMES = tuple(
    f"{name.removesuffix('_simple')}_complex" for name in SIMPLE_FIXTURE_NAMES
)
FIXTURE_NAMES = (*SIMPLE_FIXTURE_NAMES, *COMPLEX_FIXTURE_NAMES)
LEGACY_FIXTURE_NAMES = tuple(name.removesuffix("_simple") for name in SIMPLE_FIXTURE_NAMES)


def _fixture_case(name: str) -> str:
    """返回 simple/complex 配对共享的八类场景标识。"""

    return name.removesuffix("_simple").removesuffix("_complex")


_CLI_FIXTURE_NAMES = tuple(
    name
    for name in FIXTURE_NAMES
    if _fixture_case(name) != "07_sandbox_failure"
)
_SYNTHETIC_SECRETS = (
    "AKIA" + "1234567890ABCDEF",
    "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789",
    "LongSyntheticPasswordValue123!",
)
_REALISTIC_EXPECTED_RESULTS = {
    "01_clean": set(),
    "02_security": {
        ("security.sql-fstring", 55, "findings"),
        ("security.subprocess-shell-true", 61, "findings"),
    },
    "03_async_leak": {
        ("resource.client-session-without-close", 41, "needs_human_review"),
        ("async.blocking-time-sleep", 42, "findings"),
        ("async.unawaited-coroutine", 48, "needs_human_review"),
    },
    "04_db_lifecycle": {
        ("db.connection-without-close", 43, "needs_human_review"),
        ("db.transaction-without-finalize", 44, "findings"),
    },
    "05_missing_tests": {("tests.missing-coverage", 1, "needs_human_review")},
    "06_duplicate_finding": {("security.dynamic-eval", 43, "findings")},
    "07_sandbox_failure": set(),
    "08_secret_redaction": {
        ("secrets.aws_access_key", 7, "findings"),
        ("secrets.github_token", 8, "findings"),
        ("secrets.password", 9, "findings"),
    },
}


class _AllowFixtureGovernance:
    """为公开 fixture 提供固定 allow 审计事件，确保测试仍经过 pipeline 的治理阶段。"""

    def decide(self, **_arguments: Any) -> dict[str, object]:
        """返回不含原始代码或凭据的受控 allow 决策。"""

        return {
            "action": "allow",
            "events": [
                {
                    "stage": "pre_execution",
                    "target": "run_checks",
                    "action": "allow",
                    "rule": "fixture_runtime",
                    "reasons": ["fixture_rule_pack"],
                }
            ],
            "warnings": [],
        }


class _FixtureRuleSandbox:
    """在 fake runtime 中调用同一份 Skill run_checks 规则，避免 fixture 套件依赖 Docker。"""

    runtime_type = "fake"

    def __init__(self, *, expose_secret_in_stdout: bool = False) -> None:
        """记录是否向 sandbox 摘要注入合成密钥，以验证宿主二次脱敏出口。"""

        self._expose_secret_in_stdout = expose_secret_in_stdout

    def execute(self, **arguments: Any) -> dict[str, object]:
        """使用 Skill 的真实确定性规则返回候选 finding，不复制规则实现。"""

        change_set = arguments["change_set"]
        return {
            "status": "ok",
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "stdout_excerpt": _SYNTHETIC_SECRETS[1] if self._expose_secret_in_stdout else "",
            "stderr_excerpt": "",
            "error_type": None,
            "duration_ms": 1,
            "findings": list(_findings(change_set)),
        }

    def cleanup(self, **_arguments: Any) -> None:
        """fake runtime 没有创建 workspace，因此 cleanup 保持无副作用。"""


class _FailedFixtureSandbox:
    """模拟受控 sandbox 非零失败，用于验证报告和数据库仍可交付。"""

    runtime_type = "fake"

    def execute(self, **_arguments: Any) -> dict[str, object]:
        """返回不携带原始命令或路径的非零运行摘要。"""

        return {
            "status": "failed",
            "exit_code": 9,
            "timed_out": False,
            "truncated": False,
            "stdout_excerpt": "",
            "stderr_excerpt": "synthetic_checker_failure",
            "error_type": "nonzero_exit",
            "duration_ms": 1,
            "findings": [],
        }

    def cleanup(self, **_arguments: Any) -> None:
        """fake failure runtime 不保留资源，保证 finally 路径可稳定验证。"""


def _db_url(path: Path) -> str:
    """构造每条 fixture 独占的临时 SQLite URL，避免写入业务数据库。"""

    return f"sqlite+pysqlite:///{path.as_posix()}"


def _fixture_payload(name: str) -> FixturePayload:
    """读取公开 unified diff 数据，保持 fixture 的 diff 载荷语义不变。"""

    return FixturePayload(
        payload_type="diff",
        diff_text=(FIXTURE_DIR / f"{name}.diff").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    "fixture_name",
    COMPLEX_FIXTURE_NAMES,
    ids=COMPLEX_FIXTURE_NAMES,
)
def test_complex_fixtures_have_multi_file_engineering_scale(fixture_name: str) -> None:
    """验证每条 complex diff 都有双文件及 60–150 行新增代码，而不是无意义短样例。"""

    fixture_path = FIXTURE_DIR / f"{fixture_name}.diff"
    assert fixture_path.is_file()
    diff_text = fixture_path.read_text(encoding="utf-8")
    added_code_lines = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    added_code_lines = [
        line
        for line in added_code_lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert 60 <= len(added_code_lines) <= 150
    assert diff_text.count("diff --git ") >= 2


def _run_fixture(name: str, tmp_path: Path) -> tuple[dict[str, Any], SqlReviewStore, Path]:
    """经唯一 ReviewPipeline 执行一条 fixture，并返回 canonical 报告、存储和输出目录。"""

    output_dir = tmp_path / "reports"
    store = SqlReviewStore(_db_url(tmp_path / "review.db"))
    fixture_case = _fixture_case(name)
    sandbox: Any
    if fixture_case == "07_sandbox_failure":
        sandbox = _FailedFixtureSandbox()
    else:
        sandbox = _FixtureRuleSandbox(
            expose_secret_in_stdout=fixture_case == "08_secret_redaction",
        )
    pipeline = ReviewPipeline(
        store=store,
        governance=_AllowFixtureGovernance(),
        sandbox=sandbox,
        output_dir=output_dir,
        task_id_factory=lambda: f"fixture-{name}",
    )
    result = pipeline.run(fixture=_fixture_payload(name))
    return result.report, store, output_dir


def _cli_environment() -> dict[str, str]:
    """构造不携带模型凭据的 CLI 子进程环境，避免测试触达真实模型配置。"""

    environment = os.environ.copy()
    for variable in tuple(environment):
        if "API_KEY" in variable or "TOKEN" in variable or "PASSWORD" in variable:
            environment.pop(variable)
    return environment


def _run_cli_fixture(name: str, tmp_path: Path) -> tuple[dict[str, Any], SqlReviewStore, Path]:
    """通过公开 CLI 运行真实 local Skill，并返回 canonical 报告、临时 SQLite store 和输出目录。"""

    output_dir = tmp_path / "reports"
    database = tmp_path / "review.db"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "run_agent.py"),
            "review",
            "--fixture",
            name,
            "--sandbox",
            "local",
            "--dry-run",
            "--db-url",
            _db_url(database),
            "--output-dir",
            str(output_dir),
        ],
        cwd=tmp_path,
        env=_cli_environment(),
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    report = json.loads((output_dir / "review_report.json").read_text(encoding="utf-8"))
    assert result["task_id"] == report["task_id"]
    store = SqlReviewStore(_db_url(database))
    store.initialize()
    return report, store, output_dir


@pytest.mark.parametrize("fixture_name", LEGACY_FIXTURE_NAMES, ids=LEGACY_FIXTURE_NAMES)
def test_legacy_fixture_names_are_rejected_by_cli(fixture_name: str, tmp_path: Path) -> None:
    """验证旧的无后缀 fixture 名称不能作为 CLI 别名继续使用。"""

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "run_agent.py"),
            "review",
            "--fixture",
            fixture_name,
            "--sandbox",
            "local",
            "--dry-run",
            "--db-url",
            _db_url(tmp_path / "review.db"),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
        cwd=tmp_path,
        env=_cli_environment(),
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=120,
    )

    assert completed.returncode == 2


def _assert_common_fixture_outputs(
    report: dict[str, Any],
    store: SqlReviewStore,
    output_dir: Path,
) -> dict[str, Any]:
    """断言每条 fixture 都生成同源 JSON、Markdown 与五域可查询数据库 bundle。"""

    json_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    bundle = store.get_task_bundle(report["task_id"])
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    assert report["task_id"] in markdown_path.read_text(encoding="utf-8")
    assert bundle is not None
    assert bundle["task"]["id"] == report["task_id"]
    assert len(bundle["sandbox_runs"]) == 1
    assert len(bundle["filter_events"]) == 1
    assert bundle["report"] is not None
    return bundle


def _assert_complex_results(report: dict[str, Any], fixture_name: str) -> None:
    """验证 complex 样例经任一公开链路后的规则、行号与分桶完全一致。"""

    fixture_case = _fixture_case(fixture_name)
    actual_results = {
        (finding["rule_id"], finding["line"], bucket)
        for bucket in ("findings", "needs_human_review")
        for finding in report[bucket]
    }
    assert actual_results == _REALISTIC_EXPECTED_RESULTS[fixture_case]
    if fixture_case == "06_duplicate_finding":
        assert report["findings"][0]["extra"]["also_matched"]


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES, ids=FIXTURE_NAMES)
def test_public_fixtures_generate_expected_reports_and_bundles(
    fixture_name: str,
    tmp_path: Path,
) -> None:
    """验证八组 simple/complex fixture 的类别、桶和完整交付契约。"""

    report, store, output_dir = _run_fixture(fixture_name, tmp_path)
    try:
        bundle = _assert_common_fixture_outputs(report, store, output_dir)
        fixture_case = _fixture_case(fixture_name)
        categories = {finding["category"] for finding in report["findings"]}
        reviewed_categories = categories | {
            finding["category"] for finding in report["needs_human_review"]
        }
        if fixture_name in COMPLEX_FIXTURE_NAMES:
            _assert_complex_results(report, fixture_name)

        if fixture_case == "01_clean":
            assert report["findings"] == []
            assert report["needs_human_review"] == []
        elif fixture_case == "02_security":
            security = [finding for finding in report["findings"] if finding["category"] == "security"]
            assert len(security) >= 2
            assert {finding["severity"] for finding in security} <= {"high", "critical"}
        elif fixture_case == "03_async_leak":
            assert {"async-errors", "resource-leak"} <= reviewed_categories
            assert "resource-leak" in {
                finding["category"] for finding in report["needs_human_review"]
            }
        elif fixture_case == "04_db_lifecycle":
            lifecycle_findings = [
                finding
                for finding in [*report["findings"], *report["needs_human_review"]]
                if finding["category"] == "db-lifecycle"
            ]
            assert "db-lifecycle" in reviewed_categories
            assert len(lifecycle_findings) >= 2
        elif fixture_case == "05_missing_tests":
            assert report["findings"] == []
            assert {finding["category"] for finding in report["needs_human_review"]} == {"missing-tests"}
        elif fixture_case == "06_duplicate_finding":
            security = [finding for finding in report["findings"] if finding["category"] == "security"]
            assert len(security) == 1
            assert security[0]["extra"]["also_matched"]
        elif fixture_case == "07_sandbox_failure":
            assert report["status"] == "completed_with_warnings"
            assert report["findings"] == []
            assert bundle["sandbox_runs"][0]["status"] == "failed"
            assert "sandbox_failed" in {warning["code"] for warning in report["warnings"]}
        elif fixture_case == "08_secret_redaction":
            secret_findings = [finding for finding in report["findings"] if finding["category"] == "secrets"]
            serialized_outputs = "\n".join(
                (
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    json.dumps(bundle, ensure_ascii=False, sort_keys=True),
                    (output_dir / "review_report.json").read_text(encoding="utf-8"),
                    (output_dir / "review_report.md").read_text(encoding="utf-8"),
                )
            )
            database_text = (tmp_path / "review.db").read_bytes().decode(
                "utf-8",
                errors="ignore",
            )
            assert len(secret_findings) >= 3
            assert all(finding["line"] not in {4, 5} for finding in secret_findings)
            assert not contains_plaintext_secret(serialized_outputs)
            assert all(secret not in serialized_outputs for secret in _SYNTHETIC_SECRETS)
            assert all(secret not in database_text for secret in _SYNTHETIC_SECRETS)
    finally:
        store.close()


@pytest.mark.parametrize("fixture_name", _CLI_FIXTURE_NAMES, ids=_CLI_FIXTURE_NAMES)
def test_public_fixtures_run_through_cli_with_real_local_skill(
    fixture_name: str,
    tmp_path: Path,
) -> None:
    """验证十四条非故障 fixture 从 CLI 到真实 Skill 与持久化的完整闭环。"""

    report, store, output_dir = _run_cli_fixture(fixture_name, tmp_path)
    try:
        bundle = _assert_common_fixture_outputs(report, store, output_dir)
        fixture_case = _fixture_case(fixture_name)
        all_categories = {
            finding["category"]
            for finding in [*report["findings"], *report["needs_human_review"]]
        }

        assert report["status"] in {"completed", "completed_with_warnings"}
        if fixture_name in COMPLEX_FIXTURE_NAMES:
            _assert_complex_results(report, fixture_name)
        if fixture_case == "01_clean":
            assert not report["findings"]
        elif fixture_case == "02_security":
            assert sum(finding["category"] == "security" for finding in report["findings"]) >= 2
        elif fixture_case == "03_async_leak":
            assert {"async-errors", "resource-leak"} <= all_categories
        elif fixture_case == "04_db_lifecycle":
            assert "db-lifecycle" in all_categories
        elif fixture_case == "05_missing_tests":
            assert "missing-tests" in all_categories
        elif fixture_case == "06_duplicate_finding":
            security = [finding for finding in report["findings"] if finding["category"] == "security"]
            assert len(security) == 1
        elif fixture_case == "08_secret_redaction":
            assert "secrets" in all_categories
            assert not contains_plaintext_secret(json.dumps(bundle, ensure_ascii=False, sort_keys=True))
    finally:
        store.close()
