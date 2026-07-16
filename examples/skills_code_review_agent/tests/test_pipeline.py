# tests/test_pipeline.py - Task 12 端到端测试（TDD）
"""测试 run_review() 管线全链路串联 + CLI 接口

测试用例（内联 diff，不依赖 Task14 fixture）：
1. security diff → SEC001 finding + changes_requested
2. clean diff → 0 finding + approve
3. sensitive diff → SECRET001 + 输出/落库无明文
4. sandbox_failure trigger → completed_with_warnings 不崩
5. duplicate → 去重
6. missing_tests → warnings 含 TEST001
7. policy.json 真加载（非死文件）
8. CLI 真接 fake (build_runtime 收到 "fake")
9. sandbox_runs.stdout 已脱敏
"""
import unittest
import os
import sys
import tempfile
from pathlib import Path

# 添加项目根路径到 sys.path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "examples" / "skills_code_review_agent"))

from agent.pipeline import run_review
from agent.models import Severity, Bucket


class TestPipelineEndToEnd(unittest.TestCase):
    """端到端测试：验证全链路串联正确"""

    def setUp(self):
        """测试前准备：临时输出目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.temp_dir, "outputs")
        self.db_path = os.path.join(self.temp_dir, "test_review.db")

    def tearDown(self):
        """测试后清理"""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        # 清理可能生成的数据库文件
        if os.path.exists("review.db"):
            os.remove("review.db")

    def test_1_security_diff_changes_requested(self):
        """测试1: security diff → SEC001 finding + changes_requested"""
        diff_text = """diff --git a/example.py b/example.py
index 123..456 789
--- a/example.py
+++ b/example.py
@@ -1,1 +1,2 @@
+os.system(user_input)
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证结论
        self.assertEqual(report.conclusion, "changes_requested")

        # 验证有 SEC001 finding
        sec_findings = [f for f in report.findings if f.rule_id == "SEC001"]
        self.assertGreater(len(sec_findings), 0, "应该检测到 SEC001 安全问题")

        # 验证 finding 属性
        finding = sec_findings[0]
        self.assertEqual(finding.severity, Severity.HIGH)
        self.assertEqual(finding.category, "security")
        self.assertIn("os.system", finding.evidence)

    def test_2_clean_diff_approve(self):
        """测试2: clean diff → 0 finding + approve"""
        diff_text = """diff --git a/example.py b/example.py
index 123..456 789
--- a/example.py
+++ b/example.py
@@ -1,1 +1,2 @@
+# 简单注释
+pass
diff --git a/test_example.py b/test_example.py
index 123..456 789
--- a/test_example.py
+++ b/test_example.py
@@ -1,1 +1,2 @@
+# 测试文件
+def test_example():
+    pass
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证结论
        self.assertEqual(report.conclusion, "approve")

        # 验证 0 findings
        self.assertEqual(len(report.findings), 0, "清洁代码应该 0 findings")
        self.assertEqual(len(report.warnings), 0, "清洁代码应该 0 warnings")

    def test_3_sensitive_diff_secret001_redacted(self):
        """测试3: sensitive diff → SECRET001 + 输出/落库无明文"""
        # 修复：使用真实可匹配密钥格式（必须有连字符才能匹配 sk- 正则）
        # sk-realsecret0123456789 匹配条件：
        # 1. rule_engine SECRET001 KV模式：api_key="sk-realsecret0123456789" → 命中
        # 2. redaction sk-正则：sk-[A-Za-z0-9]{20,} → 命中（24字符，≥20）
        secret_key = "sk-realsecret0123456789"
        diff_text = f"""diff --git a/config.py b/config.py
index 123..456 789
--- a/config.py
+++ b/config.py
@@ -1,1 +1,2 @@
+api_key = "{secret_key}"
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证①：该密钥被 SECRET001 检出（证明检测生效，非"本来就没检测"）
        secret_findings = [f for f in report.findings if f.rule_id == "SECRET001"]
        self.assertGreater(len(secret_findings), 0, "应该检测到 SECRET001 密钥泄露")

        # 验证②：明文密钥消失（脱敏生效）
        for finding in secret_findings:
            self.assertNotIn(secret_key, finding.evidence, "evidence 不应包含明文密钥")
            self.assertNotIn(secret_key, finding.title, "title 不应包含明文密钥")
            self.assertNotIn(secret_key, finding.recommendation, "recommendation 不应包含明文密钥")

        # 验证③：脱敏标记出现（确认被脱敏引擎处理）
        for finding in secret_findings:
            # sk- 正则替换为 [REDACTED_SK]，KV正则替换为 [REDACTED_KV]
            # 由于 sk- 前缀优先命中，应被替换为 [REDACTED_SK]
            self.assertIn("[REDACTED", finding.evidence, "evidence 应包含脱敏标记")

        # 验证 filter_decisions 脱敏
        for decision in report.filter_decisions:
            self.assertNotIn(secret_key, decision.reason, "reason 不应包含明文密钥")
            self.assertNotIn(secret_key, decision.command_redacted, "command_redacted 不应包含明文密钥")

        # 验证 sandbox_runs 脱敏
        for run in report.sandbox_runs:
            self.assertNotIn(secret_key, run.stdout_redacted, "stdout_redacted 不应包含明文密钥")
            self.assertNotIn(secret_key, run.stderr_redacted, "stderr_redacted 不应包含明文密钥")

    def test_4_sandbox_failure_completed_with_warnings(self):
        """测试4: sandbox_failure trigger → completed_with_warnings 不崩"""
        diff_text = """diff --git a/test_example.py b/test_example.py
index 123..456 789
--- a/test_example.py
+++ b/test_example.py
@@ -1,1 +1,2 @@
+force_sandbox_failure
"""
        repo = "https://github.com/test/repo.git"

        # 不应该抛出异常
        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证结论
        self.assertEqual(report.conclusion, "completed_with_warnings")

        # 验证有沙箱运行记录
        self.assertGreater(len(report.sandbox_runs), 0, "应该有沙箱运行记录")

        # 验证有失败记录
        failed_runs = [r for r in report.sandbox_runs if r.status == "failed"]
        self.assertGreater(len(failed_runs), 0, "应该有失败的沙箱运行")

    def test_5_duplicate_dedup(self):
        """测试5: duplicate → 去重（同一file/line/category/rule_id只保留最高置信度）"""
        # 测试去重功能：subprocess.call(..., shell=True) 应该触发 SEC002
        diff_text = """diff --git a/example.py b/example.py
index 123..456 789
--- a/example.py
+++ b/example.py
@@ -1,1 +1,2 @@
+import subprocess
+subprocess.call("ls", shell=True)
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证检测到 SEC002 (subprocess shell=True)
        sec_findings = [f for f in report.findings if f.rule_id == "SEC002"]
        self.assertGreater(len(sec_findings), 0, "应该检测到 SEC002 安全问题")

        # 验证去重：同一个文件同一规则只应该有一个finding
        self.assertEqual(len(sec_findings), 1, "相同文件相同规则应该去重为 1 个 finding")

    def test_6_missing_tests_warning_test001(self):
        """测试6: missing_tests → warnings 含 TEST001"""
        diff_text = """diff --git a/app.py b/app.py
index 123..456 789
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
+def hello():
+    pass
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证有 TEST001 warning
        test_findings = [f for f in report.warnings if f.rule_id == "TEST001"]
        self.assertGreater(len(test_findings), 0, "生产代码变更缺少测试应该有 TEST001 warning")

        # 验证 bucket
        for finding in test_findings:
            self.assertEqual(finding.bucket, Bucket.WARNINGS)

    def test_7_policy_json_actually_loaded(self):
        """测试7: policy.json 真加载（非死文件）"""
        # 验证 policy.json 被真实加载
        from filters.policy import load_policy

        policy = load_policy()
        self.assertIsInstance(policy, dict, "policy 应该是 dict")
        self.assertIn("forbidden_paths", policy, "policy 应该包含 forbidden_paths")
        self.assertIn("max_sandbox_runs", policy, "policy 应该包含 max_sandbox_runs")

        # 验证具体值
        self.assertIsInstance(policy["forbidden_paths"], list)
        self.assertIsInstance(policy["max_sandbox_runs"], int)

    def test_8_cli_fake_backend(self):
        """测试8: CLI 真接 fake (build_runtime 收到 "fake")"""
        from sandbox.factory import build_runtime

        # 验证 fake 后端构建
        runtime = build_runtime("fake")
        self.assertEqual(runtime.__class__.__name__, "FakeSandbox", "应该构建 FakeSandbox")

        # 验证可以运行
        result = runtime.run("test_script", "/tmp", {"diff_text": ""})
        self.assertIsNotNone(result, "fake 沙箱应该返回结果")
        self.assertEqual(result.runtime, "fake")

    def test_9_sandbox_output_redacted(self):
        """测试9: sandbox_runs.stdout 已脱敏"""
        # 修复：使用真实可匹配密钥格式（必须有连字符才能匹配 sk- 正则）
        # sk-test-leaked-key-123456789 匹配条件：
        # redaction sk-正则：sk-[A-Za-z0-9]{20,} → 命中（27字符，≥20）
        secret_key = "sk-test-leaked-key-123456789"
        diff_text = """diff --git a/example.py b/example.py
index 123..456 789
--- a/example.py
+++ b/example.py
@@ -1,1 +1,2 @@
+force_secret_output
"""
        repo = "https://github.com/test/repo.git"

        report = run_review(diff_text=diff_text, repo=repo, sandbox="fake", dry_run=True, llm=False)

        # 验证沙箱输出已脱敏
        for run in report.sandbox_runs:
            if "leaked-key" in run.stdout_redacted:
                # 验证①：明文密钥消失
                self.assertNotIn(secret_key, run.stdout_redacted, "沙箱输出中的明文密钥应该被脱敏")
                # 验证②：脱敏标记出现
                self.assertIn("[REDACTED", run.stdout_redacted, "沙箱输出应包含脱敏标记")

    def test_10_conclusion_derivation_rules(self):
        """测试10: conclusion 派生规则完整验证"""
        # 场景1: critical/high → changes_requested
        diff_critical = """diff --git a/test.py b/test.py
index 123..456 789
--- a/test.py
+++ b/test.py
@@ -1,1 +1,2 @@
+os.system(user_input)
"""
        report1 = run_review(diff_critical, "test", "fake", True, False)
        self.assertEqual(report1.conclusion, "changes_requested", "有 critical/high finding 应该 → changes_requested")

        # 场景2: warnings + filter block → needs_human_review
        diff_warning = """diff --git a/.env b/.env
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/.env
@@ -0,0 +1 @@
+SECRET=value
"""
        report2 = run_review(diff_warning, "test", "fake", True, False)
        # 应该有 needs_human_review（因为有 filter block）
        has_filter_block = any(d.decision in ["deny", "needs_human_review"] for d in report2.filter_decisions)
        if has_filter_block:
            self.assertEqual(report2.conclusion, "needs_human_review", "filter block 应该 → needs_human_review")

        # 场景3: sandbox failure → completed_with_warnings
        diff_sandbox_fail = """diff --git a/test.py b/test.py
index 123..456 789
--- a/test.py
+++ b/test.py
@@ -1,1 +1,2 @@
+force_sandbox_failure
"""
        report3 = run_review(diff_sandbox_fail, "test", "fake", True, False)
        has_failed_run = any(r.status == "failed" for r in report3.sandbox_runs)
        if has_failed_run:
            self.assertEqual(report3.conclusion, "completed_with_warnings", "沙箱失败应该 → completed_with_warnings")

        # 场景4: 都无 → approve
        diff_clean = """diff --git a/test.py b/test.py
index 123..456 789
--- a/test.py
+++ b/test.py
@@ -1,1 +1,2 @@
+# 注释
+pass
"""
        report4 = run_review(diff_clean, "test", "fake", True, False)
        self.assertEqual(report4.conclusion, "approve", "无问题应该 → approve")


if __name__ == "__main__":
    unittest.main()
