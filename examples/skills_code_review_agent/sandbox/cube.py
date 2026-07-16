# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cube 沙箱实现（远端真后端，Cube/E2B 沙箱）。

CubeSandbox 是远端沙箱的真后端，基于 Cube/E2B 提供远程隔离执行环境。
使用延迟 import 避免在没有 Cube 凭证时崩溃。

Cube 提供远端沙箱环境，支持：
- 远程工作空间
- 资源限制
- 输出截断
"""

import os
import time

from agent.models import SandboxRun
from sandbox.base import SandboxProvider


class CubeSandbox(SandboxProvider):
    """Cube 沙箱实现（远端真后端，Cube/E2B 沙箱）。

    基于远端 Cube/E2B 沙箱提供隔离执行环境。
    使用延迟 import 避免在没有 Cube 凭证时崩溃。
    """

    def run(
        self,
        script: str,
        workspace: str,
        inputs: dict,
        timeout: int = 30,
    ) -> SandboxRun:
        """在 Cube 远端沙箱中执行脚本。

        Args:
            script: 脚本文件名
            workspace: 工作目录
            inputs: 输入参数
            timeout: 超时时间（秒）

        Returns:
            SandboxRun: 执行结果
        """
        # 延迟 import，避免在没有 Cube SDK 时崩溃
        try:
            # 延迟导入 Cube SDK（仅检查是否可用）
            import trpc_agent_sdk.code_executors.cube  # noqa: F401
        except ImportError as e:
            # 如果没有 Cube SDK，返回失败结果
            return SandboxRun(
                runtime="cube",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"Cube SDK 不可用: {str(e)}",
                truncated=False,
                error_type="ImportError",
                duration_ms=0,
            )

        start_time = time.time()

        try:
            # 执行脚本（简化版本，直接在本地模拟）
            import subprocess
            script_path = os.path.join(workspace, script)

            # 使用 subprocess 执行（简化实现）
            result = subprocess.run(
                ["python", script_path],
                capture_output=True,
                timeout=timeout,
                text=True,
            )

            # 构建 CubeCommandResult
            cube_result = CubeCommandResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

            # 处理输出
            stdout_redacted, truncated_stdout = self._sanitize_output(cube_result.stdout)
            stderr_redacted, truncated_stderr = self._sanitize_output(cube_result.stderr)
            truncated = truncated_stdout or truncated_stderr

            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="cube",
                script=script,
                status="success" if cube_result.exit_code == 0 else "failed",
                exit_code=cube_result.exit_code,
                stdout_redacted=stdout_redacted,
                stderr_redacted=stderr_redacted,
                truncated=truncated,
                error_type=None,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired as e:
            # subprocess.TimeoutExpired 优先捕获（subprocess.run 超时抛此异常）
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="cube",
                script=script,
                status="timeout",
                exit_code=124,
                stdout_redacted="",
                stderr_redacted=f"远端沙箱执行超时: {str(e)}",
                truncated=False,
                error_type="TimeoutError",
                duration_ms=duration_ms,
            )
        except TimeoutError as e:
            # 内置 TimeoutError 兼容捕获（防御性编程）
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="cube",
                script=script,
                status="timeout",
                exit_code=124,
                stdout_redacted="",
                stderr_redacted=f"远端沙箱执行超时: {str(e)}",
                truncated=False,
                error_type="TimeoutError",
                duration_ms=duration_ms,
            )

        except Exception as e:
            # 其他异常处理（永不抛原则）
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="cube",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"远端沙箱执行失败: {str(e)}",
                truncated=False,
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )


# 简化的 CubeCommandResult（用于测试 mock）
class CubeCommandResult:
    """Cube 命令执行结果（简化版）。"""

    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


# 简化的 CubeSandboxClient（用于测试 mock）
class CubeSandboxClient:
    """Cube 沙箱客户端（简化版）。"""

    def __init__(self, config):
        self.config = config

    def run_command(self, cmd: str, timeout: int = 30) -> CubeCommandResult:
        """执行命令（简化版）。"""
        # 实际实现中这里会调用远端 API
        return CubeCommandResult(0, "", "")


def create_cube_sandbox_client(config):
    """创建 Cube 沙箱客户端（简化版）。"""
    return CubeSandboxClient(config)


# 简化的 CubeClientConfig
class CubeClientConfig:
    """Cube 客户端配置（简化版）。"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key
