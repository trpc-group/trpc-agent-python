# tests/test_storage.py - 存储层 TDD 测试（验收3+验收5 双命门）
import pytest
import os
import tempfile
from agent.models import (ReviewReport, Finding, SandboxRun, FilterDecision, MonitoringSummary, Severity, Bucket)


@pytest.fixture
def temp_db():
    """临时数据库文件 fixture"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def store(temp_db):
    """ReviewStore fixture"""
    from storage.store import ReviewStore
    db_url = f"sqlite:///{temp_db}"
    return ReviewStore(db_url=db_url)


def test_start_task(store):
    """测试 start_task 创建任务记录"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")
    assert task_id is not None
    assert len(task_id) > 0

    # 验证任务已创建
    details = store.get_task_details(task_id)
    assert details is not None
    assert details["task_id"] == task_id
    assert details["status"] == "running"
    assert details["repository"] == "https://github.com/test/repo"
    assert details["scope"] == "main"


def test_save_and_get_task_details(store):
    """测试 save→get_task_details 回环：七表都能查到（验收3 按task查）"""
    # 1. 启动任务
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    # 2. 构造完整 ReviewReport
    report = ReviewReport(task_id=task_id,
                          status="completed",
                          conclusion="approve",
                          findings=[
                              Finding(severity=Severity.HIGH,
                                      category="security",
                                      file="src/auth.py",
                                      line=42,
                                      title="Hardcoded API key",
                                      evidence="api_key = 'sk-test1234567890abcdef'",
                                      recommendation="Use environment variables",
                                      confidence=0.95,
                                      source="rule",
                                      rule_id="SECRET001",
                                      bucket=Bucket.FINDINGS)
                          ],
                          warnings=[
                              Finding(severity=Severity.MEDIUM,
                                      category="style",
                                      file="src/utils.py",
                                      line=10,
                                      title="Long line",
                                      evidence="x = 1  # very long comment that exceeds limit",
                                      recommendation="Break into multiple lines",
                                      confidence=0.8,
                                      source="rule",
                                      rule_id="STYLE001",
                                      bucket=Bucket.WARNINGS)
                          ],
                          needs_human_review=[],
                          filter_decisions=[
                              FilterDecision(stage="pre_commit",
                                             decision="allow",
                                             reason="No critical secrets found",
                                             command_redacted="grep -r 'sk-'")
                          ],
                          sandbox_runs=[
                              SandboxRun(runtime="python",
                                         script="print('test')",
                                         status="success",
                                         exit_code=0,
                                         stdout_redacted="test output with sk-abc123def456",
                                         stderr_redacted="",
                                         truncated=False,
                                         error_type=None,
                                         duration_ms=100)
                          ],
                          monitoring=MonitoringSummary(total_duration_ms=5000,
                                                       sandbox_duration_ms=100,
                                                       tool_call_count=10,
                                                       blocked_count=0,
                                                       finding_count=1,
                                                       severity_distribution={
                                                           "high": 1,
                                                           "medium": 1
                                                       },
                                                       exception_distribution={}),
                          repository="https://github.com/test/repo",
                          input_summary="2 files changed")

    # 3. 保存报告
    store.save(report)

    # 4. 验证七表都能查到
    details = store.get_task_details(task_id)

    # 验证 review_tasks 表
    assert details["task_id"] == task_id
    assert details["status"] == "completed"
    assert details["conclusion"] == "approve"
    assert details["total_duration_ms"] == 5000

    # 验证 findings 表（按 bucket 分离）
    assert len(details["findings"]) == 1  # Bucket.FINDINGS
    assert details["findings"][0]["category"] == "security"
    assert details["findings"][0]["bucket"] == "findings"
    assert len(details["warnings"]) == 1  # Bucket.WARNINGS
    assert details["warnings"][0]["bucket"] == "warnings"

    # 验证 sandbox_runs 表
    assert len(details["sandbox_runs"]) == 1
    assert details["sandbox_runs"][0]["runtime"] == "python"
    assert details["sandbox_runs"][0]["status"] == "success"

    # 验证 filter_decisions 表
    assert len(details["filter_decisions"]) == 1
    assert details["filter_decisions"][0]["stage"] == "pre_commit"
    assert details["filter_decisions"][0]["decision"] == "allow"

    # 验证 monitoring_summaries 表
    assert details["monitoring"]["total_duration_ms"] == 5000
    assert details["monitoring"]["sandbox_duration_ms"] == 100
    assert details["monitoring"]["finding_count"] == 1

    # 验证 review_reports 表
    assert "report_json" in details
    assert "report_md" in details
    assert "report_sarif" in details


def test_save_idempotent(store):
    """测试重复 save 幂等（UNIQUE 去重）"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    report = ReviewReport(task_id=task_id,
                          status="completed",
                          conclusion="approve",
                          findings=[
                              Finding(severity=Severity.HIGH,
                                      category="security",
                                      file="src/auth.py",
                                      line=42,
                                      title="Hardcoded API key",
                                      evidence="api_key = 'sk-test1234567890abcdef'",
                                      recommendation="Use environment variables",
                                      confidence=0.95,
                                      source="rule",
                                      rule_id="SECRET001",
                                      bucket=Bucket.FINDINGS)
                          ],
                          warnings=[],
                          needs_human_review=[],
                          filter_decisions=[],
                          sandbox_runs=[],
                          monitoring=MonitoringSummary(total_duration_ms=5000,
                                                       sandbox_duration_ms=0,
                                                       tool_call_count=5,
                                                       blocked_count=0,
                                                       finding_count=1,
                                                       severity_distribution={"high": 1},
                                                       exception_distribution={}),
                          repository="https://github.com/test/repo",
                          input_summary="1 file changed")

    # 第一次保存
    store.save(report)
    details1 = store.get_task_details(task_id)
    assert len(details1["findings"]) == 1

    # 第二次保存（相同 finding，应该幂等）
    store.save(report)
    details2 = store.get_task_details(task_id)
    assert len(details2["findings"]) == 1  # 不应该重复插入


def test_save_redacts_secrets(store):
    """测试落库前脱敏：stdout 含 sk-xxx 落库后为 [REDACTED_*]（验收5命门）"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    report = ReviewReport(
        task_id=task_id,
        status="completed",
        conclusion="approve",
        findings=[],
        warnings=[],
        needs_human_review=[],
        filter_decisions=[],
        sandbox_runs=[
            SandboxRun(
                runtime="python",
                script="print('test')",
                status="success",
                exit_code=0,
                # 含有未脱敏的 Stripe 密钥
                stdout_redacted="Output: sk-test1234567890abcdefghijklmnop",
                stderr_redacted="Error: ghp_test1234567890abcdefghijklmnop123456",
                truncated=False,
                error_type=None,
                duration_ms=100)
        ],
        monitoring=MonitoringSummary(total_duration_ms=5000,
                                     sandbox_duration_ms=100,
                                     tool_call_count=5,
                                     blocked_count=0,
                                     finding_count=0,
                                     severity_distribution={},
                                     exception_distribution={}),
        repository="https://github.com/test/repo",
        input_summary="1 file changed")

    # 保存报告
    store.save(report)

    # 直接查询数据库验证脱敏
    import sqlite3
    conn = sqlite3.connect(store.path)
    cursor = conn.cursor()
    cursor.execute("SELECT stdout_redacted, stderr_redacted FROM sandbox_runs WHERE task_id=?", (task_id, ))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    stdout, stderr = row

    # 验证脱敏：应该包含 [REDACTED_*] 而不是原始密钥
    assert "[REDACTED_" in stdout
    assert "sk-test1234567890abcdefghijklmnop" not in stdout
    assert "[REDACTED_" in stderr
    assert "ghp_test1234567890abcdefghijklmnop123456" not in stderr


def test_markdown_report_redacts_input_summary(store):
    """测试 Markdown 报告中的 input_summary 被脱敏（验收5 命门 - C1 修复）"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    # 构造包含密钥的 input_summary
    report = ReviewReport(
        task_id=task_id,
        status="completed",
        conclusion="approve",
        findings=[
            Finding(severity=Severity.HIGH,
                    category="security",
                    file="src/auth.py",
                    line=42,
                    title="Hardcoded API key",
                    evidence="api_key = 'sk-test1234567890abcdef'",
                    recommendation="Use environment variables",
                    confidence=0.95,
                    source="rule",
                    rule_id="SECRET001",
                    bucket=Bucket.FINDINGS)
        ],
        warnings=[],
        needs_human_review=[],
        filter_decisions=[],
        sandbox_runs=[],
        monitoring=MonitoringSummary(total_duration_ms=5000,
                                     sandbox_duration_ms=0,
                                     tool_call_count=5,
                                     blocked_count=0,
                                     finding_count=1,
                                     severity_distribution={"high": 1},
                                     exception_distribution={}),
        repository="https://github.com/test/repo",
        # input_summary 包含密钥（GitHub token 使用实际 40 字符格式：ghp_ + 36字符）
        input_summary="Modified files with API key sk-test1234567890abcdef and "
        "token ghp_1234567890abcdefghijklmnop1234567890")

    # 保存报告
    store.save(report)

    # 查询数据库中的 report_md 字段
    import sqlite3
    conn = sqlite3.connect(store.path)
    cursor = conn.cursor()
    cursor.execute("SELECT report_md FROM review_reports WHERE task_id=?", (task_id, ))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    report_md = row[0]

    # 验证脱敏：应该包含 [REDACTED_] 而不是原始密钥
    assert "[REDACTED_" in report_md, f"Markdown report should contain redacted marker, got: {report_md}"
    assert "sk-test1234567890abcdef" not in report_md, "原始 Stripe 密钥不应该出现在 Markdown 报告中"
    assert "ghp_1234567890abcdefghijklmnop1234567890" not in report_md, "原始 GitHub token 不应该出现在 Markdown 报告中"


def test_input_diffs_fields_completeness(store):
    """测试 input_diffs 表的 digest 和 files_json 字段完整性（验收3 - I1 修复）"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    report = ReviewReport(task_id=task_id,
                          status="completed",
                          conclusion="approve",
                          findings=[
                              Finding(severity=Severity.HIGH,
                                      category="security",
                                      file="src/auth.py",
                                      line=42,
                                      title="Hardcoded API key",
                                      evidence="api_key = 'sk-test1234567890abcdef'",
                                      recommendation="Use environment variables",
                                      confidence=0.95,
                                      source="rule",
                                      rule_id="SECRET001",
                                      bucket=Bucket.FINDINGS),
                              Finding(severity=Severity.MEDIUM,
                                      category="style",
                                      file="src/utils.py",
                                      line=10,
                                      title="Long line",
                                      evidence="x = 1  # very long comment that exceeds limit",
                                      recommendation="Break into multiple lines",
                                      confidence=0.8,
                                      source="rule",
                                      rule_id="STYLE001",
                                      bucket=Bucket.FINDINGS)
                          ],
                          warnings=[],
                          needs_human_review=[],
                          filter_decisions=[],
                          sandbox_runs=[],
                          monitoring=MonitoringSummary(total_duration_ms=5000,
                                                       sandbox_duration_ms=0,
                                                       tool_call_count=5,
                                                       blocked_count=0,
                                                       finding_count=2,
                                                       severity_distribution={
                                                           "high": 1,
                                                           "medium": 1
                                                       },
                                                       exception_distribution={}),
                          repository="https://github.com/test/repo",
                          input_summary="2 files changed, 10 insertions(+), 5 deletions(-)")

    # 保存报告
    store.save(report)

    # 查询 input_diffs 表验证 digest 和 files_json 字段
    import sqlite3
    conn = sqlite3.connect(store.path)
    cursor = conn.cursor()
    cursor.execute("SELECT digest, files_json, redacted_summary FROM input_diffs WHERE task_id=?", (task_id, ))
    row = cursor.fetchone()
    conn.close()

    assert row is not None, "input_diffs 表应该有记录"
    digest, files_json, redacted_summary = row

    # 验证 digest 不为空且为有效的十六进制字符串
    assert digest is not None, "digest 字段不应为 None"
    assert len(digest) == 64, f"SHA256 digest 应为 64 个字符，实际: {len(digest)}"
    assert all(c in '0123456789abcdef' for c in digest), f"digest 应为十六进制字符串: {digest}"

    # 验证 files_json 是有效的 JSON 数组，包含变更文件列表
    assert files_json is not None, "files_json 字段不应为 None"
    import json
    files_list = json.loads(files_json)
    assert isinstance(files_list, list), "files_json 应解析为列表"
    assert len(files_list) > 0, "应该有变更文件"
    # 应该包含 findings 中提到的文件
    assert "src/auth.py" in files_list, "应该包含 src/auth.py"
    assert "src/utils.py" in files_list, "应该包含 src/utils.py"

    # 验证 redacted_summary 不为空
    assert redacted_summary is not None, "redacted_summary 不应为 None"
    assert len(redacted_summary) > 0, "redacted_summary 不应为空字符串"

    # 验证 get_task_details 能查到这些字段
    details = store.get_task_details(task_id)
    assert "input_diffs" in details
    assert details["input_diffs"] is not None

    # 验证新字段存在且正确
    assert "digest" in details["input_diffs"]
    assert "files_json" in details["input_diffs"]
    assert details["input_diffs"]["digest"] == digest
    assert details["input_diffs"]["files_json"] == files_json


def test_mark_task_failed(store):
    """测试 mark_task_failed 标记任务失败"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    # 验证初始状态
    details = store.get_task_details(task_id)
    assert details["status"] == "running"

    # 标记失败
    error_msg = "Database connection failed"
    store.mark_task_failed(task_id, error_msg)

    # 验证状态更新
    details = store.get_task_details(task_id)
    assert details["status"] == "failed"
    assert details["conclusion"] == "failed"


def test_get_task_details_aggregates_all_tables(store):
    """测试 get_task_details 聚合七表（验收3 完整性）"""
    task_id = store.start_task(repo="https://github.com/test/repo", scope="main")

    report = ReviewReport(task_id=task_id,
                          status="completed",
                          conclusion="changes_requested",
                          findings=[
                              Finding(severity=Severity.CRITICAL,
                                      category="security",
                                      file="src/auth.py",
                                      line=10,
                                      title="SQL injection",
                                      evidence="query = f\"SELECT * FROM users WHERE id={user_input}\"",
                                      recommendation="Use parameterized queries",
                                      confidence=0.99,
                                      source="rule",
                                      rule_id="SQL001",
                                      bucket=Bucket.FINDINGS)
                          ],
                          warnings=[],
                          needs_human_review=[
                              Finding(severity=Severity.LOW,
                                      category="performance",
                                      file="src/api.py",
                                      line=50,
                                      title="N+1 query",
                                      evidence="for user in users: user.posts",
                                      recommendation="Use eager loading",
                                      confidence=0.7,
                                      source="llm",
                                      rule_id="PERF001",
                                      bucket=Bucket.NEEDS_REVIEW)
                          ],
                          filter_decisions=[
                              FilterDecision(stage="pre_commit",
                                             decision="deny",
                                             reason="Critical security issue found",
                                             command_redacted="security-scan --strict")
                          ],
                          sandbox_runs=[
                              SandboxRun(runtime="node",
                                         script="npm test",
                                         status="failed",
                                         exit_code=1,
                                         stdout_redacted="Tests passed",
                                         stderr_redacted="Error: timeout",
                                         truncated=False,
                                         error_type="TimeoutError",
                                         duration_ms=5000)
                          ],
                          monitoring=MonitoringSummary(total_duration_ms=10000,
                                                       sandbox_duration_ms=5000,
                                                       tool_call_count=20,
                                                       blocked_count=1,
                                                       finding_count=1,
                                                       severity_distribution={
                                                           "critical": 1,
                                                           "low": 1
                                                       },
                                                       exception_distribution={"TimeoutError": 1}),
                          repository="https://github.com/test/repo",
                          input_summary="3 files changed, 50 insertions(+), 10 deletions(-)")

    store.save(report)

    # 验证完整聚合
    details = store.get_task_details(task_id)

    # review_tasks 表
    assert details["task_id"] == task_id
    assert details["status"] == "completed"
    assert details["conclusion"] == "changes_requested"
    assert details["repository"] == "https://github.com/test/repo"
    assert details["scope"] == "main"
    assert details["total_duration_ms"] == 10000
    assert details["created_at"] is not None
    assert details["completed_at"] is not None

    # findings 表（按 bucket 分离）
    assert len(details["findings"]) == 1  # Bucket.FINDINGS
    assert details["findings"][0]["severity"] == "critical"
    assert len(details["warnings"]) == 0
    assert len(details["needs_human_review"]) == 1  # Bucket.NEEDS_REVIEW

    # sandbox_runs 表
    assert len(details["sandbox_runs"]) == 1
    assert details["sandbox_runs"][0]["error_type"] == "TimeoutError"

    # filter_decisions 表
    assert len(details["filter_decisions"]) == 1
    assert details["filter_decisions"][0]["decision"] == "deny"

    # monitoring_summaries 表
    assert details["monitoring"]["blocked_count"] == 1
    assert details["monitoring"]["exception_distribution"] == {"TimeoutError": 1}

    # review_reports 表
    assert "report_json" in details
    assert "report_md" in details
