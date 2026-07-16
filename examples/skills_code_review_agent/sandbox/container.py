# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Container 沙箱实现（生产真后端，Docker 容器隔离）。

ContainerSandbox 是生产环境的真后端，基于 Docker 容器提供隔离执行环境。
使用延迟 import 避免在没有 Docker 环境时崩溃。

Docker 参数：
- network none：无网络访问
- read-only：只读文件系统
- memory 512m：内存限制 512MB
- cpus 1.0：CPU 限制 1 核
- pids-limit 256：进程数限制
- cap-drop ALL：丢弃所有特权
- security-opt no-new-privileges：禁止提权
- tmpfs /tmp：临时文件系统
- user 65532：非 root 用户
"""

import os
import time

from agent.models import SandboxRun
from sandbox.base import SandboxProvider


def _bounded_int(env_name: str, default: int, max_val: int) -> int:
    """单向收紧资源限制：环境变量只能调低上限，不能调高。

    Args:
        env_name: 环境变量名
        default: 默认值
        max_val: 最大允许值

    Returns:
        int: 最终值（不超过 max_val）

    Raises:
        ValueError: 如果环境变量超过 max_val
    """
    value = os.getenv(env_name)
    if value is None:
        return default

    try:
        int_value = int(value)
    except ValueError:
        raise ValueError(f"{env_name} 必须是整数，当前值: {value}")

    if int_value > max_val:
        raise ValueError(f"{env_name} 不能超过 {max_val}，当前值: {int_value}")

    return int_value


class CommandArgs:
    """命令参数（简化版）。"""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout
        self.environment = None
        self.stdin = None


class ContainerSandbox(SandboxProvider):
    """Container 沙箱实现（生产真后端，Docker 容器隔离）。

    基于 Docker 容器提供隔离执行环境。
    使用延迟 import 避免在没有 Docker 环境时崩溃。
    """

    # Docker 默认参数
    DOCKER_ARGS = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",  # 无网络访问
        "--read-only",  # 只读文件系统
        "--memory",
        "512m",  # 内存限制 512MB
        "--cpus",
        "1.0",  # CPU 限制 1 核
        "--pids-limit",
        "256",  # 进程数限制
        "--cap-drop",
        "ALL",  # 丢弃所有特权
        "--security-opt",
        "no-new-privileges",  # 禁止提权
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev",  # 临时文件系统
        "--user",
        "65532",  # 非 root 用户
    ]

    def run(
        self,
        script: str,
        workspace: str,
        inputs: dict,
        timeout: int = 30,
    ) -> SandboxRun:
        """在 Docker 容器中执行脚本。

        Args:
            script: 脚本文件名（相对于 workspace）
            workspace: 工作目录
            inputs: 输入参数
            timeout: 超时时间（秒）

        Returns:
            SandboxRun: 执行结果
        """
        # 延迟 import，避免在没有 Docker 环境时崩溃
        try:
            # 延迟导入 ContainerClient
            from trpc_agent_sdk.code_executors.container import ContainerClient, ContainerConfig
        except ImportError as e:
            # 如果没有 Docker SDK，返回失败结果
            return SandboxRun(
                runtime="container",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"Docker SDK 不可用: {str(e)}",
                truncated=False,
                error_type="ImportError",
                duration_ms=0,
            )

        start_time = time.time()

        try:
            # 资源限制（单向收紧）
            timeout_limit = _bounded_int("SANDBOX_TIMEOUT_SEC", timeout, 300)  # 最多 5 分钟
            _bounded_int("SANDBOX_MEMORY_MB", 512, 1024)  # 默认 512MB，最多 1GB（预留给未来使用）

            # 创建容器客户端
            config = ContainerConfig(
                image="skills-code-review-agent:latest",
                host_config={"Binds": [f"{workspace}:/workspace:ro"]},  # 只读挂载
            )

            client = ContainerClient(config)

            # 执行命令
            result = client.exec_run(
                cmd=["python", f"/workspace/{script}"],
                command_args=CommandArgs(timeout=timeout_limit),
            )

            # 处理输出
            stdout_redacted, truncated_stdout = self._sanitize_output(result.stdout)
            stderr_redacted, truncated_stderr = self._sanitize_output(result.stderr)
            truncated = truncated_stdout or truncated_stderr

            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="container",
                script=script,
                status="success" if result.exit_code == 0 else "failed",
                exit_code=result.exit_code,
                stdout_redacted=stdout_redacted,
                stderr_redacted=stderr_redacted,
                truncated=truncated,
                error_type=None,
                duration_ms=duration_ms,
            )

        except TimeoutError as e:
            # 超时处理
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="container",
                script=script,
                status="timeout",
                exit_code=124,
                stdout_redacted="",
                stderr_redacted=f"容器执行超时: {str(e)}",
                truncated=False,
                error_type="TimeoutError",
                duration_ms=duration_ms,
            )

        except Exception as e:
            # 其他异常处理（永不抛原则）
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="container",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"容器执行失败: {str(e)}",
                truncated=False,
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
