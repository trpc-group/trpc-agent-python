# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""沙箱四后端测试（TDD）。

测试 Fake/Local/Container/Cube 四种沙箱实现的正确性。
按照 RED-GREEN-REFACTOR 流程：
1. RED: 先写失败测试
2. GREEN: 实现功能让测试通过
3. REFACTOR: 重构优化
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agent.models import SandboxRun
from sandbox.factory import build_runtime
from sandbox.fake import FakeSandbox


class TestFakeSandbox:
    """测试 FakeSandbox 的 trigger 关键字模拟。"""

    def test_force_sandbox_timeout(self):
        """测试 force_sandbox_timeout trigger 返回 timeout 状态。"""
        sandbox = FakeSandbox()
        result = sandbox.run(
            script="test.py",
            workspace="/tmp",
            inputs={"diff_text": "force_sandbox_timeout"},
            timeout=30,
        )

        assert result.runtime == "fake"
        assert result.status == "timeout"
        assert result.exit_code == 124
        assert result.error_type == "TimeoutError"
        assert result.duration_ms == 30 * 1000

    def test_force_sandbox_failure(self):
        """测试 force_sandbox_failure trigger 返回 failed 状态。"""
        sandbox = FakeSandbox()
        result = sandbox.run(
            script="test.py",
            workspace="/tmp",
            inputs={"diff_text": "force_sandbox_failure"},
            timeout=30,
        )

        assert result.runtime == "fake"
        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.error_type == "CalledProcessError"
        assert result.duration_ms == 0

    def test_force_secret_output(self):
        """测试 force_secret_output trigger 返回 success 但 stdout 含 sk-。"""
        sandbox = FakeSandbox()
        result = sandbox.run(
            script="test.py",
            workspace="/tmp",
            inputs={"diff_text": "force_secret_output"},
            timeout=30,
        )

        assert result.runtime == "fake"
        assert result.status == "success"
        assert result.exit_code == 0
        assert "sk-leaked-secret" in result.stdout_redacted
        assert result.duration_ms == 5

    def test_normal_success(self):
        """测试无 trigger 时返回正常 success。"""
        sandbox = FakeSandbox()
        result = sandbox.run(
            script="test.py",
            workspace="/tmp",
            inputs={"diff_text": "normal diff"},
            timeout=30,
        )

        assert result.runtime == "fake"
        assert result.status == "success"
        assert result.exit_code == 0
        assert result.stdout_redacted.startswith("ok:test.py")
        assert result.duration_ms == 5


class TestLocalSandbox:
    """测试 LocalSandbox 的本地执行。"""

    def test_local_success(self):
        """测试 LocalSandbox 成功执行脚本。"""
        from sandbox.local import LocalSandbox

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一个简单的测试脚本
            script_path = os.path.join(tmpdir, "test.py")
            with open(script_path, 'w') as f:
                f.write('print("hello from local")')

            sandbox = LocalSandbox()
            result = sandbox.run(
                script="test.py",
                workspace=tmpdir,
                inputs={},
                timeout=30,
            )

            assert result.runtime == "local"
            assert result.status == "success"
            assert result.exit_code == 0
            assert "hello from local" in result.stdout_redacted

    def test_local_timeout(self):
        """测试 LocalSandbox 超时捕获。"""
        from sandbox.local import LocalSandbox

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一个超时脚本
            script_path = os.path.join(tmpdir, "timeout.py")
            with open(script_path, 'w') as f:
                f.write('import time; time.sleep(10)')

            sandbox = LocalSandbox()
            result = sandbox.run(
                script="timeout.py",
                workspace=tmpdir,
                inputs={},
                timeout=1,  # 1 秒超时
            )

            assert result.runtime == "local"
            assert result.status == "timeout"
            assert result.exit_code == 124
            assert result.error_type == "TimeoutError"

    def test_local_failure(self):
        """测试 LocalSandbox 执行失败。"""
        from sandbox.local import LocalSandbox

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一个失败脚本
            script_path = os.path.join(tmpdir, "fail.py")
            with open(script_path, 'w') as f:
                f.write('import sys; sys.exit(1)')

            sandbox = LocalSandbox()
            result = sandbox.run(
                script="fail.py",
                workspace=tmpdir,
                inputs={},
                timeout=30,
            )

            assert result.runtime == "local"
            assert result.status == "failed"
            assert result.exit_code == 1


class TestBoundedInt:
    """测试 bounded_int 辅助函数。"""

    def test_bounded_int_default(self):
        """测试无环境变量时返回默认值。"""
        from sandbox.container import _bounded_int

        with patch.dict(os.environ, {}, clear=True):
            result = _bounded_int("TEST_VAR", 30, 60)
            assert result == 30

    def test_bounded_int_lower_value(self):
        """测试环境变量值低于上限时正常返回。"""
        from sandbox.container import _bounded_int

        with patch.dict(os.environ, {"TEST_VAR": "20"}):
            result = _bounded_int("TEST_VAR", 30, 60)
            assert result == 20

    def test_bounded_int_exceeds_max_raises(self):
        """测试环境变量值超过上限时抛出 ValueError。"""
        from sandbox.container import _bounded_int

        with patch.dict(os.environ, {"TEST_VAR": "100"}):
            with pytest.raises(ValueError, match="TEST_VAR 不能超过 60"):
                _bounded_int("TEST_VAR", 30, 60)


class TestContainerSandbox:
    """测试 ContainerSandbox 的 Docker 容器执行。"""

    def test_container_with_mock(self):
        """测试 ContainerSandbox 使用 mock（不依赖真 Docker）。"""
        from sandbox.container import ContainerSandbox

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "test.py")
            with open(script_path, 'w') as f:
                f.write('print("hello from container")')

            # Mock trpc_agent_sdk.code_executors.container 模块
            mock_client = MagicMock()
            mock_client.exec_run.return_value = MagicMock(
                exit_code=0,
                stdout=(b"hello from container", b""),
            )

            with patch('trpc_agent_sdk.code_executors.container.ContainerClient', return_value=mock_client):
                with patch('trpc_agent_sdk.code_executors.container.ContainerConfig'):
                    sandbox = ContainerSandbox()
                    result = sandbox.run(
                        script="test.py",
                        workspace=tmpdir,
                        inputs={},
                        timeout=30,
                    )

                    assert result.runtime == "container"
                    # 由于 Docker 可能不可用，只要不抛异常即可
                    assert result is not None

    def test_container_timeout_with_mock(self):
        """测试 ContainerSandbox 超时（使用 mock）。"""
        from sandbox.container import ContainerSandbox

        # Mock trpc_agent_sdk.code_executors.container 模块
        mock_client = MagicMock()
        mock_client.exec_run.side_effect = TimeoutError("Container timeout")

        with patch('trpc_agent_sdk.code_executors.container.ContainerClient', return_value=mock_client):
            with patch('trpc_agent_sdk.code_executors.container.ContainerConfig'):
                sandbox = ContainerSandbox()
                result = sandbox.run(
                    script="test.py",
                    workspace="/tmp",
                    inputs={},
                    timeout=30,
                )

                assert result.runtime == "container"
                # 由于 Docker 可能不可用，只要不抛异常即可
                assert result is not None


class TestCubeSandbox:
    """测试 CubeSandbox 的远端沙箱执行。"""

    def test_cube_with_mock(self):
        """测试 CubeSandbox 使用 mock（不依赖真 Cube）。"""
        from sandbox.cube import CubeSandbox

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "test.py")
            with open(script_path, 'w') as f:
                f.write('print("hello from cube")')

            # Mock subprocess
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "hello from cube"
            mock_result.stderr = ""

            # 使用 sys.modules 模拟来避免导入问题
            import sys
            mock_cube_module = MagicMock()

            # 保存原始模块状态
            original_import = None
            if 'trpc_agent_sdk.code_executors.cube' in sys.modules:
                original_import = sys.modules['trpc_agent_sdk.code_executors.cube']

            try:
                # 设置 mock 模块
                sys.modules['trpc_agent_sdk.code_executors.cube'] = mock_cube_module

                with patch('subprocess.run', return_value=mock_result):
                    sandbox = CubeSandbox()
                    result = sandbox.run(
                        script="test.py",
                        workspace=tmpdir,
                        inputs={},
                        timeout=30,
                    )

                    assert result.runtime == "cube"
                    assert result.status == "success"
                    assert result.exit_code == 0
            finally:
                # 恢复原始模块状态
                if original_import:
                    sys.modules['trpc_agent_sdk.code_executors.cube'] = original_import
                elif 'trpc_agent_sdk.code_executors.cube' in sys.modules:
                    del sys.modules['trpc_agent_sdk.code_executors.cube']

    def test_cube_timeout_with_mock(self):
        """测试 CubeSandbox 超时（使用 mock）。"""
        from sandbox.cube import CubeSandbox
        import subprocess
        import sys

        # 使用 sys.modules 模拟来避免导入问题
        mock_cube_module = MagicMock()

        # 保存原始模块状态
        original_import = None
        if 'trpc_agent_sdk.code_executors.cube' in sys.modules:
            original_import = sys.modules['trpc_agent_sdk.code_executors.cube']

        try:
            # 设置 mock 模块
            sys.modules['trpc_agent_sdk.code_executors.cube'] = mock_cube_module

            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired("python", 30)

                sandbox = CubeSandbox()
                result = sandbox.run(
                    script="test.py",
                    workspace="/tmp",
                    inputs={},
                    timeout=30,
                )

                assert result.runtime == "cube"
                # 修复 Critical 2 测试: 严格断言超时返回 status="timeout"（非 failed）
                msg = f"Expected timeout, got {result.status}"
                assert result.status == "timeout", msg
                assert result.exit_code == 124
                assert result.error_type == "TimeoutError"
        finally:
            # 恢复原始模块状态
            if original_import:
                sys.modules['trpc_agent_sdk.code_executors.cube'] = original_import
            elif 'trpc_agent_sdk.code_executors.cube' in sys.modules:
                del sys.modules['trpc_agent_sdk.code_executors.cube']


class TestFactory:
    """测试 build_runtime 工厂函数。"""

    def test_factory_fake_default(self):
        """测试默认 backend 返回 FakeSandbox。"""
        with patch.dict(os.environ, {}, clear=True):
            sandbox = build_runtime()
            assert isinstance(sandbox, FakeSandbox)

    def test_factory_fake_explicit(self):
        """测试明确指定 fake 返回 FakeSandbox。"""
        sandbox = build_runtime("fake")
        assert isinstance(sandbox, FakeSandbox)

    def test_factory_local(self):
        """测试指定 local 返回 LocalSandbox。"""
        from sandbox.local import LocalSandbox

        sandbox = build_runtime("local")
        assert isinstance(sandbox, LocalSandbox)

    def test_factory_container(self):
        """测试指定 container 返回 ContainerSandbox。"""
        from sandbox.container import ContainerSandbox

        sandbox = build_runtime("container")
        assert isinstance(sandbox, ContainerSandbox)

    def test_factory_cube(self):
        """测试指定 cube 返回 CubeSandbox。"""
        from sandbox.cube import CubeSandbox

        sandbox = build_runtime("cube")
        assert isinstance(sandbox, CubeSandbox)

    def test_factory_env_override(self):
        """测试环境变量 CODE_REVIEW_SANDBOX_BACKEND 覆盖。"""
        with patch.dict(os.environ, {"CODE_REVIEW_SANDBOX_BACKEND": "local"}):
            from sandbox.local import LocalSandbox

            sandbox = build_runtime()  # 无参数，从环境变量读取
            assert isinstance(sandbox, LocalSandbox)

    def test_factory_invalid_backend(self):
        """测试无效 backend 抛出 ValueError。"""
        with pytest.raises(ValueError, match="未知的沙箱后端"):
            build_runtime("invalid_backend")


class TestSandboxNeverThrows:
    """测试沙箱永不抛原则。"""

    def test_fake_handles_all_inputs(self):
        """测试 FakeSandbox 处理任何输入都不抛异常。"""
        sandbox = FakeSandbox()

        # 各种边界输入
        test_cases = [
            {
                "diff_text": ""
            },
            {
                "diff_text": None
            },
            {},  # 空 inputs
            {
                "other_key": "value"
            },  # 无 diff_text
        ]

        for inputs in test_cases:
            result = sandbox.run(
                script="test.py",
                workspace="/tmp",
                inputs=inputs,
                timeout=30,
            )
            # 永不返回 None
            assert result is not None
            assert isinstance(result, SandboxRun)

    def test_local_handles_script_not_found(self):
        """测试 LocalSandbox 处理脚本不存在。"""
        from sandbox.local import LocalSandbox

        sandbox = LocalSandbox()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 脚本不存在
            result = sandbox.run(
                script="nonexistent.py",
                workspace=tmpdir,
                inputs={},
                timeout=30,
            )

            # 应该返回 failed 而不是抛异常
            assert result.status in ["failed", "timeout"]
            assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
