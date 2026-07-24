# test_report.py — 报告与监控模块测试
import json
import tempfile
from pathlib import Path

from agent.models import (
    Bucket,
    Finding,
    FilterDecision,
    MonitoringSummary,
    ReviewReport,
    SandboxRun,
    Severity,
)
from agent.report import write_reports
from agent.telemetry import build_monitoring


class TestBuildMonitoring:
    """测试 build_monitoring 函数"""

    def test_build_monitoring_basic(self):
        """测试基本监控指标聚合"""
        findings = [
            Finding(
                severity=Severity.CRITICAL,
                category="security",
                file="test.py",
                line=10,
                title="Critical bug",
                evidence="evidence",
                recommendation="fix it",
                confidence=0.9,
                source="rule",
                rule_id="R001",
            ),
            Finding(
                severity=Severity.HIGH,
                category="performance",
                file="test.py",
                line=20,
                title="High issue",
                evidence="evidence",
                recommendation="optimize",
                confidence=0.8,
                source="llm",
                rule_id="R002",
            ),
        ]

        exceptions = [
            {
                "exception_type": "ValueError",
                "message": "test error"
            },
            {
                "exception_type": "TimeoutError",
                "message": "timeout"
            },
            {
                "exception_type": "ValueError",
                "message": "another error"
            },
        ]

        monitoring = build_monitoring(
            total_duration_ms=5000,
            sandbox_duration_ms=2000,
            tool_call_count=10,
            blocked_count=1,
            findings=findings,
            exceptions=exceptions,
        )

        # 验证 7 项指标
        assert monitoring.total_duration_ms == 5000
        assert monitoring.sandbox_duration_ms == 2000
        assert monitoring.tool_call_count == 10
        assert monitoring.blocked_count == 1
        assert monitoring.finding_count == 2

        # 验证 severity_distribution
        assert monitoring.severity_distribution == {
            "critical": 1,
            "high": 1,
            "medium": 0,
            "low": 0,
        }

        # 验证 exception_distribution
        assert monitoring.exception_distribution == {
            "ValueError": 2,
            "TimeoutError": 1,
        }

    def test_build_monitoring_empty(self):
        """测试空列表的监控指标"""
        monitoring = build_monitoring(
            total_duration_ms=0,
            sandbox_duration_ms=0,
            tool_call_count=0,
            blocked_count=0,
            findings=[],
            exceptions=[],
        )

        assert monitoring.finding_count == 0
        assert monitoring.severity_distribution == {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        }
        assert monitoring.exception_distribution == {}


class TestWriteReports:
    """测试 write_reports 函数"""

    def make_sample_report(self) -> ReviewReport:
        """创建示例报告数据"""
        findings = [
            Finding(
                severity=Severity.CRITICAL,
                category="security",
                file="auth.py",
                line=42,
                title="SQL注入漏洞",
                evidence='cursor.execute(f"SELECT * FROM users WHERE id={user_input}")',
                recommendation="使用参数化查询：cursor.execute("
                "\"SELECT * FROM users WHERE id=?\", (user_input,))",
                confidence=0.95,
                source="rule",
                rule_id="R001",
                bucket=Bucket.FINDINGS,
            ),
            Finding(
                severity=Severity.MEDIUM,
                category="code_quality",
                file="utils.py",
                line=15,
                title="未使用的变量",
                evidence="unused_var = 42",
                recommendation="删除未使用的变量或添加使用逻辑",
                confidence=0.8,
                source="ast",
                rule_id="R002",
                bucket=Bucket.FINDINGS,
            ),
        ]

        warnings = [
            Finding(
                severity=Severity.LOW,
                category="style",
                file="main.py",
                line=100,
                title="行长度超过120字符",
                evidence="very_long_line_that_exceeds_120_characters_"
                "limit_please_break_it_into_multiple_lines",
                recommendation="将长行拆分为多行",
                confidence=0.7,
                source="llm",
                rule_id="S001",
                bucket=Bucket.WARNINGS,
            )
        ]

        needs_review = [
            Finding(
                severity=Severity.HIGH,
                category="security",
                file="crypto.py",
                line=88,
                title="可疑的加密用法",
                evidence="custom_encrypt(data, key='hardcoded_key')",
                recommendation="请人工确认：是否为业务必需？考虑使用标准库",
                confidence=0.6,
                source="rule+llm",
                rule_id="R003",
                bucket=Bucket.NEEDS_REVIEW,
            )
        ]

        filter_decisions = [
            FilterDecision(
                stage="pre_review",
                decision="allow",
                reason="通过安全门禁检查",
                command_redacted="git diff HEAD~1",
            ),
            FilterDecision(
                stage="sandbox_execution",
                decision="deny",
                reason="检测到危险操作：文件系统写入",
                command_redacted="open('/etc/passwd', 'w')",
            ),
        ]

        sandbox_runs = [
            SandboxRun(
                runtime="python",
                script="print('test')",
                status="success",
                exit_code=0,
                stdout_redacted="test\n",
                stderr_redacted="",
                truncated=False,
                error_type=None,
                duration_ms=150,
            ),
            SandboxRun(
                runtime="python",
                script="import sys; sys.exit(1)",
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted="Error: script failed",
                truncated=False,
                error_type="RuntimeError",
                duration_ms=50,
            ),
        ]

        monitoring = MonitoringSummary(
            total_duration_ms=8500,
            sandbox_duration_ms=200,
            tool_call_count=15,
            blocked_count=1,
            finding_count=3,
            severity_distribution={
                "critical": 1,
                "high": 1,
                "medium": 1,
                "low": 1
            },
            exception_distribution={
                "RuntimeError": 1,
                "ValueError": 2
            },
        )

        return ReviewReport(
            task_id="TASK-123",
            status="completed",
            conclusion="changes_requested",
            findings=findings,
            warnings=warnings,
            needs_human_review=needs_review,
            filter_decisions=filter_decisions,
            sandbox_runs=sandbox_runs,
            monitoring=monitoring,
            repository="owner/repo",
            input_summary="Reviewing PR #42: 3 files changed, "
            "55 additions, 12 deletions",
        )

    def test_write_json_report(self):
        """测试 JSON 报告生成"""
        report = self.make_sample_report()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            json_path = Path(tmpdir) / "review_report.json"
            assert json_path.exists(), "JSON 报告文件应该存在"

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 验证 JSON 包含所有 section keys
            expected_keys = {
                "task_id",
                "status",
                "conclusion",
                "findings",
                "warnings",
                "needs_human_review",
                "filter_decisions",
                "sandbox_runs",
                "monitoring",
                "repository",
                "input_summary",
            }
            assert set(data.keys()) == expected_keys

            # 验证数据完整性
            assert data["task_id"] == "TASK-123"
            assert len(data["findings"]) == 2
            assert len(data["warnings"]) == 1
            assert len(data["needs_human_review"]) == 1
            assert len(data["filter_decisions"]) == 2
            assert len(data["sandbox_runs"]) == 2

    def test_write_markdown_report(self):
        """测试 Markdown 报告生成"""
        report = self.make_sample_report()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            md_path = Path(tmpdir) / "review_report.md"
            assert md_path.exists(), "Markdown 报告文件应该存在"

            content = md_path.read_text(encoding="utf-8")

            # 验证 MD 包含 7 个章节标题
            expected_sections = [
                "# Code Review Report",
                "## Findings",
                "## Warnings",
                "## Needs Human Review",
                "## Filter Decisions",
                "## Sandbox Runs",
                "## Monitoring",
                "## Conclusion",
            ]
            for section in expected_sections:
                assert section in content, f"缺少章节：{section}"

            # 验证每个 finding 都有 recommendation
            assert "Recommendation" in content or "建议" in content
            assert "SQL注入漏洞" in content
            assert "未使用的变量" in content

    def test_write_sarif_report(self):
        """测试 SARIF v2.1.0 报告生成"""
        report = self.make_sample_report()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            sarif_path = Path(tmpdir) / "review_report.sarif"
            assert sarif_path.exists(), "SARIF 报告文件应该存在"

            with open(sarif_path, "r", encoding="utf-8") as f:
                sarif = json.load(f)

            # 验证 SARIF v2.1.0 基本结构
            assert sarif["version"] == "2.1.0"
            assert "$schema" in sarif
            assert "runs" in sarif
            assert len(sarif["runs"]) > 0

            run = sarif["runs"][0]
            assert "results" in run
            assert len(run["results"]) > 0

            # 验证 result 结构
            result = run["results"][0]
            assert "level" in result
            assert "locations" in result
            assert len(result["locations"]) > 0

            location = result["locations"][0]
            assert "physicalLocation" in location

            # 验证 level 映射 severity
            # CRITICAL -> error, HIGH -> error, MEDIUM -> warning, LOW -> note
            critical_result = next((r for r in run["results"] if r.get("ruleId") == "R001"), None)
            assert critical_result is not None
            assert critical_result["level"] == "error"

    def test_severity_statistics(self):
        """测试严重级别统计正确性"""
        report = self.make_sample_report()

        # 验证 monitoring 中的 severity_distribution
        assert report.monitoring.severity_distribution == {
            "critical": 1,
            "high": 1,
            "medium": 1,
            "low": 1,
        }

        # 验证 finding count
        assert report.monitoring.finding_count == 3  # findings + needs_review
        assert len(report.findings) == 2
        assert len(report.needs_human_review) == 1

    def test_sarif_level_mapping(self):
        """测试 SARIF level 映射规则"""
        report = self.make_sample_report()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            sarif_path = Path(tmpdir) / "review_report.sarif"
            with open(sarif_path, "r", encoding="utf-8") as f:
                sarif = json.load(f)

            results = sarif["runs"][0]["results"]

            # 创建 rule_id -> level 的映射
            level_map = {r.get("ruleId"): r.get("level") for r in results}

            # R001: CRITICAL -> error
            assert level_map.get("R001") == "error"

            # R002: MEDIUM -> warning
            assert level_map.get("R002") == "warning"

            # S001: LOW -> note
            assert level_map.get("S001") == "note"

            # R003: HIGH -> error
            assert level_map.get("R003") == "error"


class TestReportRedaction:
    """测试报告脱敏功能（Critical 1 修复）"""

    # 使用符合正则模式的测试密钥（sk- 后需 20+ 字符）
    TEST_SECRET = "sk-1234567890abcdefghijklmnopqrstuvwxyz"
    TEST_GH_SECRET = "ghp_1234567890abcdefghijklmnopqrstuv"

    def make_report_with_secrets(self) -> ReviewReport:
        """创建包含明文密钥的报告"""
        findings = [
            Finding(
                severity=Severity.CRITICAL,
                category="security",
                file="config.py",
                line=10,
                title=f"密钥泄露: {self.TEST_SECRET}",
                evidence=f"api_key = '{self.TEST_SECRET}'",
                recommendation=f"删除硬编码密钥 {self.TEST_SECRET}",
                confidence=0.95,
                source="rule",
                rule_id="SECRET001",
            ),
        ]

        sandbox_runs = [
            SandboxRun(
                runtime="fake",
                script="static_review.py",
                status="success",
                exit_code=0,
                stdout_redacted=f"输出包含 {self.TEST_SECRET} 密钥",
                stderr_redacted=f"错误：{self.TEST_SECRET} 无效",
                truncated=False,
                error_type=None,
                duration_ms=100,
            ),
        ]

        filter_decisions = [
            FilterDecision(
                stage="sandbox",
                decision="allow",
                reason=f"命令安全：{self.TEST_SECRET}",
                command_redacted=f"python static_review.py {self.TEST_SECRET}",
            ),
        ]

        monitoring = MonitoringSummary(
            total_duration_ms=1000,
            sandbox_duration_ms=100,
            tool_call_count=1,
            blocked_count=0,
            finding_count=1,
            severity_distribution={"critical": 1, "high": 0, "medium": 0, "low": 0},
            exception_distribution={},
        )

        return ReviewReport(
            task_id="test-task",
            status="completed",
            conclusion="changes_requested",
            findings=findings,
            warnings=[],
            needs_human_review=[],
            filter_decisions=filter_decisions,
            sandbox_runs=sandbox_runs,
            monitoring=monitoring,
            repository="test/repo",
            input_summary=f"变更包含 {self.TEST_SECRET}",
        )

    def test_json_report_redacts_secrets(self):
        """测试 JSON 报告正确脱敏（Critical 1）"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            json_path = Path(tmpdir) / "review_report.json"
            with open(json_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 断言：明文密钥消失
            assert self.TEST_SECRET not in content, \
                "JSON 报告不应包含明文密钥"

            # 断言：脱敏标记出现
            assert "[REDACTED" in content, \
                "JSON 报告应包含脱敏标记"

    def test_markdown_report_redacts_secrets(self):
        """测试 Markdown 报告正确脱敏（Critical 1）"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            md_path = Path(tmpdir) / "review_report.md"
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 断言：明文密钥消失
            assert self.TEST_SECRET not in content, \
                "Markdown 报告不应包含明文密钥"

            # 断言：脱敏标记出现
            assert "[REDACTED" in content, \
                "Markdown 报告应包含脱敏标记"

    def test_sarif_report_redacts_secrets(self):
        """测试 SARIF 报告正确脱敏（Critical 1）"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            sarif_path = Path(tmpdir) / "review_report.sarif"
            with open(sarif_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 断言：明文密钥消失
            assert self.TEST_SECRET not in content, \
                "SARIF 报告不应包含明文密钥"

            # 断言：脱敏标记出现
            assert "[REDACTED" in content, \
                "SARIF 报告应包含脱敏标记"

    def test_sandbox_stdout_stderr_redacted(self):
        """测试 sandbox_run stdout/stderr 正确脱敏"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            md_path = Path(tmpdir) / "review_report.md"
            content = md_path.read_text(encoding="utf-8")

            # 检查 stdout/stderr 脱敏
            assert self.TEST_SECRET not in content, \
                "stdout/stderr 中的密钥应被脱敏"

    def test_finding_fields_redacted(self):
        """测试 finding 所有字段正确脱敏"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            json_path = Path(tmpdir) / "review_report.json"
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 检查 findings 列表
            for finding in data.get("findings", []):
                assert self.TEST_SECRET not in finding.get("title", ""), \
                    "finding.title 应被脱敏"
                assert self.TEST_SECRET not in finding.get("evidence", ""), \
                    "finding.evidence 应被脱敏"
                assert self.TEST_SECRET not in finding.get("recommendation", ""), \
                    "finding.recommendation 应被脱敏"

    def test_filter_decision_fields_redacted(self):
        """测试 filter_decision 字段正确脱敏"""
        report = self.make_report_with_secrets()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_reports(report, tmpdir)

            json_path = Path(tmpdir) / "review_report.json"
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 检查 filter_decisions 列表
            for decision in data.get("filter_decisions", []):
                assert self.TEST_SECRET not in decision.get("reason", ""), \
                    "decision.reason 应被脱敏"
                assert self.TEST_SECRET not in decision.get("command_redacted", ""), \
                    "decision.command_redacted 应被脱敏"
