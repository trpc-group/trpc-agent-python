# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Local 沙箱实现（dev fallback，标注不隔离）。

LocalSandbox 是开发时的 fallback 后端，直接在本地执行脚本。
注意：此实现不提供隔离，仅用于本地开发和调试。
生产环境应使用 Container 或 Cube 后端。
"""

import os
import subprocess
import time

from agent.models import SandboxRun
from sandbox.base import SandboxProvider


class LocalSandbox(SandboxProvider):
    """Local 沙箱实现（dev fallback，标注不隔离）。

    直接在本地执行脚本，不提供隔离。
    仅用于本地开发和调试，生产环境应使用 Container 或 Cube 后端。
    """

    def run(
        self,
        script: str,
        workspace: str,
        inputs: dict,
        timeout: int = 30,
    ) -> SandboxRun:
        """在本地执行脚本（不隔离）。

        Args:
            script: 脚本文件名（相对于 workspace）
            workspace: 工作目录
            inputs: 输入参数（Local 不使用）
            timeout: 超时时间（秒）

        Returns:
            SandboxRun: 执行结果
        """
        start_time = time.time()

        script_path = os.path.join(workspace, script)

        # Critical 1 加固：校验脚本路径未逃逸出 workspace（防路径穿越）
        if not self._validate_script_path(workspace, script):
            return SandboxRun(
                runtime="local",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"脚本路径逃逸出 workspace，拒绝执行: {script}",
                truncated=False,
                error_type="PathEscapeError",
                duration_ms=0,
            )

        try:
            # 执行脚本
            result = subprocess.run(
                ["python", script_path],
                cwd=workspace,
                capture_output=True,
                timeout=timeout,
                text=True,
            )

            # 处理输出（截断）
            stdout_redacted, truncated_stdout = self._sanitize_output(result.stdout)
            stderr_redacted, truncated_stderr = self._sanitize_output(result.stderr)
            truncated = truncated_stdout or truncated_stderr

            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="local",
                script=script,
                status="success" if result.returncode == 0 else "failed",
                exit_code=result.returncode,
                stdout_redacted=stdout_redacted,
                stderr_redacted=stderr_redacted,
                truncated=truncated,
                error_type=None,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired as e:
            # 超时处理
            duration_ms = int((time.time() - start_time) * 1000)

            # 尝试获取部分输出（text=True 时 e.stdout/e.stderr 已是 str，不可再 .decode，Critical 2）
            if isinstance(e.stdout, str):
                stdout = e.stdout
            elif e.stdout:
                stdout = e.stdout.decode("utf-8", "ignore")
            else:
                stdout = ""
            if isinstance(e.stderr, str):
                stderr = e.stderr
            elif e.stderr:
                stderr = e.stderr.decode("utf-8", "ignore")
            else:
                stderr = ""
            stdout_redacted, _ = self._sanitize_output(stdout)
            stderr_redacted, _ = self._sanitize_output(stderr)

            return SandboxRun(
                runtime="local",
                script=script,
                status="timeout",
                exit_code=124,
                stdout_redacted=stdout_redacted,
                stderr_redacted=stderr_redacted,
                truncated=False,
                error_type="TimeoutError",
                duration_ms=duration_ms,
            )

        except Exception as e:
            # 其他异常处理（永不抛原则）
            duration_ms = int((time.time() - start_time) * 1000)

            return SandboxRun(
                runtime="local",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted=f"执行失败: {str(e)}",
                truncated=False,
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
