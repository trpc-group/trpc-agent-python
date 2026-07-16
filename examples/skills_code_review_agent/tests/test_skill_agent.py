# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TDD tests for code-review Skill 包 + SDK Agent 入口（Critical 2 修复）"""

import sys
import unittest
import asyncio
from pathlib import Path

# 添加 examples 目录到路径，以便导入 agent_sdk_entry
examples_dir = Path(__file__).parent.parent
sys.path.insert(0, str(examples_dir))

# 添加项目根目录到路径，以便导入 trpc_agent_sdk
# 从 examples/skills_code_review_agent/tests/ 向上两级到项目根目录
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))


class TestCodeReviewSkill(unittest.TestCase):
    """测试 code-review Skill 包（Critical 2 修复）"""

    def setUp(self):
        """测试前准备"""
        # code-review skill 在 examples/skills_code_review_agent/skills/code-review/
        self.skill_dir = examples_dir / "skills" / "code-review"
        self.skill_file = self.skill_dir / "SKILL.md"

    def test_skill_directory_exists(self):
        """测试 Skill 目录是否存在"""
        self.assertTrue(self.skill_dir.exists(), "skills/code-review/ 目录必须存在")
        self.assertTrue(self.skill_dir.is_dir(), "skills/code-review/ 必须是目录")

    def test_skill_file_exists(self):
        """测试 SKILL.md 是否存在"""
        self.assertTrue(self.skill_file.exists(), "skills/code-review/SKILL.md 必须存在")
        self.assertTrue(self.skill_file.is_file(), "SKILL.md 必须是文件")

    def test_skill_front_matter_parseable(self):
        """测试 SKILL.md front matter 可解析（Critical 2 修复）"""
        from trpc_agent_sdk.skills._repository import FsSkillRepository

        # 创建 repository 并尝试获取 skill
        repository = FsSkillRepository(str(examples_dir / "skills"))
        try:
            skill = repository.get("code-review")
            self.assertIsNotNone(skill, "code-review skill 必须能被加载")
            self.assertEqual(skill.summary.name, "code-review", "skill name 必须是 'code-review'")
            self.assertTrue(len(skill.summary.description) > 0, "skill 必须有 description")
        except Exception as e:
            self.fail(f"SKILL.md front matter 解析失败: {e}")

    def test_skill_load_via_sdk(self):
        """测试通过 SDK skill_load 发现/加载 skill（Critical 2 修复）"""
        from trpc_agent_sdk.skills._repository import FsSkillRepository

        # 测试 skill_list 包含 code-review
        repository = FsSkillRepository(str(examples_dir / "skills"))
        skill_names = repository.skill_list()
        self.assertIn("code-review", skill_names, "code-review 必须在 skill_list 中")

        # 测试 summaries 包含 code-review
        summaries = repository.summaries()
        code_review_summary = None
        for summary in summaries:
            if summary.name == "code-review":
                code_review_summary = summary
                break
        self.assertIsNotNone(code_review_summary, "code-review 必须在 summaries 中")
        self.assertTrue(len(code_review_summary.description) > 0, "code-review 必须有 description")

    def test_scripts_directory_exists(self):
        """测试 scripts/ 目录存在且包含约定脚本（Critical 2 修复）"""
        scripts_dir = self.skill_dir / "scripts"
        self.assertTrue(scripts_dir.exists(), "scripts/ 目录必须存在")
        self.assertTrue(scripts_dir.is_dir(), "scripts/ 必须是目录")

        # 检查约定脚本
        required_scripts = ["static_review.py", "diff_summary.py"]

        for script_file in required_scripts:
            script_path = scripts_dir / script_file
            self.assertTrue(script_path.exists(), f"scripts/{script_file} 必须存在")
            # 测试脚本可执行不崩（简单导入测试）
            try:
                # 验证 Python 脚本语法正确
                content = script_path.read_text(encoding="utf-8")
                compile(content, str(script_path), "exec")
            except SyntaxError as e:
                self.fail(f"scripts/{script_file} 语法错误: {e}")

    def test_static_review_script_executable(self):
        """测试 static_review.py 可执行（Critical 2 修复）"""
        import subprocess

        script_path = self.skill_dir / "scripts" / "static_review.py"

        # 准备测试输入（包含敏感信息的 diff）
        test_diff = """diff --git a/config.py b/config.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/config.py
@@ -0,0 +1,3 @@
+api_key = 'sk-1234567890abcdefghijklmnop'
+password = 'secret123'
+print('hello')
"""

        # 运行脚本
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=test_diff,
            capture_output=True,
            text=True,
            cwd=str(examples_dir),
        )

        self.assertEqual(result.returncode, 0, f"static_review.py 执行失败: {result.stderr}")

        # 验证输出包含 findings
        output = result.stdout
        self.assertIn('"findings"', output, "输出应包含 findings 字段")

    def test_diff_summary_script_executable(self):
        """测试 diff_summary.py 可执行（Critical 2 修复）"""
        import subprocess

        script_path = self.skill_dir / "scripts" / "diff_summary.py"

        # 准备测试输入
        test_diff = """diff --git a/main.py b/main.py
index 1234567..abcdefg 100644
--- a/main.py
+++ b/main.py
@@ -1,3 +1,5 @@
 def hello():
-    print('old')
+    print('new')
+    return 42
"""

        # 运行脚本
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=test_diff,
            capture_output=True,
            text=True,
            cwd=str(examples_dir),
        )

        self.assertEqual(result.returncode, 0, f"diff_summary.py 执行失败: {result.stderr}")

        # 验证输出包含统计信息
        output = result.stdout
        self.assertIn("文件变更", output, "输出应包含文件变更统计")
        self.assertIn("main.py", output, "输出应包含文件名")


class TestAgentSdkEntry(unittest.TestCase):
    """测试 SDK Agent 入口（Critical 2 修复）"""

    def test_agent_sdk_entry_file_exists(self):
        """测试 agent_sdk_entry.py 是否存在"""
        agent_file = examples_dir / "agent_sdk_entry.py"
        self.assertTrue(agent_file.exists(), "agent_sdk_entry.py 必须存在")

    def test_agent_sdk_entry_importable(self):
        """测试 agent_sdk_entry.py 可导入"""
        try:
            import agent_sdk_entry
            self.assertIsNotNone(agent_sdk_entry, "agent_sdk_entry 必须能被导入")
        except ImportError as e:
            self.fail(f"agent_sdk_entry.py 导入失败: {e}")

    def test_agent_sdk_entry_uses_skill_toolset(self):
        """测试 agent_sdk_entry.py 使用 SkillToolSet（Critical 2 修复）"""
        # 读取文件内容
        agent_file = examples_dir / "agent_sdk_entry.py"
        content = agent_file.read_text(encoding="utf-8")

        # 验证包含 SkillToolSet 导入
        self.assertIn("SkillToolSet", content, "agent_sdk_entry.py 必须导入 SkillToolSet")
        self.assertIn("create_default_skill_repository", content,
                      "agent_sdk_entry.py 必须导入 create_default_skill_repository")

        # 验证使用 skill_tool_set 和 skill_repository
        self.assertIn("skill_tool_set", content, "agent_sdk_entry.py 必须使用 skill_tool_set")
        self.assertIn("skill_repository", content, "agent_sdk_entry.py 必须使用 skill_repository")

    def test_create_skill_tool_set_function(self):
        """测试 _create_skill_tool_set 函数（Critical 2 修复）"""
        import agent_sdk_entry

        # 验证函数存在
        self.assertTrue(hasattr(agent_sdk_entry, "_create_skill_tool_set"),
                        "agent_sdk_entry.py 必须有 _create_skill_tool_set 函数")

        # 调用函数验证返回值
        tool_set, repository = agent_sdk_entry._create_skill_tool_set()

        self.assertIsNotNone(tool_set, "_create_skill_tool_set 必须返回 tool_set")
        self.assertIsNotNone(repository, "_create_skill_tool_set 必须返回 repository")

    def test_code_review_agent_uses_skills(self):
        """测试 CodeReviewAgent 使用 SkillToolSet（Critical 2 修复）"""
        import agent_sdk_entry

        # 验证 CodeReviewAgent 类存在
        self.assertTrue(hasattr(agent_sdk_entry, "CodeReviewAgent"),
                        "agent_sdk_entry.py 必须定义 CodeReviewAgent 类")

        # 验证 instruction 提及 skill_load 和 skill_run
        agent_file = examples_dir / "agent_sdk_entry.py"
        content = agent_file.read_text(encoding="utf-8")

        self.assertIn("skill_load", content, "Agent instruction 应提及 skill_load")
        self.assertIn("skill_run", content, "Agent instruction 应提及 skill_run")


class TestSkillRunIntegration(unittest.TestCase):
    """测试 skill_run 真实执行（Critical 2 修复）"""

    def test_skill_tool_set_has_skill_run(self):
        """测试 SkillToolSet 包含 skill_run 工具（Critical 2 修复）"""
        from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository

        # 创建 SkillToolSet
        skill_paths = [str(examples_dir / "skills")]
        repository = create_default_skill_repository(skill_paths)
        tool_set = SkillToolSet(repository=repository)

        # 异步测试
        async def run_test():
            # 获取工具列表
            tools = await tool_set.get_tools()

            # 验证包含 skill_load 和 skill_run
            tool_names = [t.name for t in tools]
            self.assertIn("skill_load", tool_names, "工具集应包含 skill_load")
            self.assertIn("skill_run", tool_names, "工具集应包含 skill_run")

            # 找到 skill_run 工具
            skill_run_tool = None
            for tool in tools:
                if tool.name == "skill_run":
                    skill_run_tool = tool
                    break

            self.assertIsNotNone(skill_run_tool, "必须能找到 skill_run 工具")
            self.assertTrue(
                hasattr(skill_run_tool, "_run_async_impl"),
                "skill_run 工具必须有 _run_async_impl 方法"
            )

        # 运行异步测试
        asyncio.run(run_test())


def run_tests():
    """运行测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试
    suite.addTests(loader.loadTestsFromTestCase(TestCodeReviewSkill))
    suite.addTests(loader.loadTestsFromTestCase(TestAgentSdkEntry))
    suite.addTests(loader.loadTestsFromTestCase(TestSkillRunIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 返回是否全部通过
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
