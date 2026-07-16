# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fake 沙箱实现（默认，无依赖）。

FakeSandbox 是默认的沙箱实现，不需要任何外部依赖（Docker/Cube）。
它通过 inputs["diff_text"] 中的 trigger 关键字来模拟各种边界情况，
用于本地开发和测试。

Trigger 关键字：
- force_sandbox_timeout：模拟超时
- force_sandbox_failure：模拟执行失败
- force_secret_output：模拟密钥泄露
- force_large_output：模拟输出过大

Critical 1 加固（纵深防御）：返回前对 stdout/stderr 脱敏。
"""

from agent.models import SandboxRun
from agent.redaction import redact_text
from sandbox.base import SandboxProvider


class FakeSandbox(SandboxProvider):
    """Fake 沙箱实现（默认，无依赖）。

    通过 trigger 关键字模拟边界情况，用于本地开发和测试。
    不需要 Docker/Cube 等外部依赖。
    """

    def run(
        self,
        script: str,
        workspace: str,
        inputs: dict,
        timeout: int = 30,
    ) -> SandboxRun:
        """在 Fake 沙箱中执行脚本（通过 trigger 模拟）。

        Args:
            script: 脚本文件名
            workspace: 工作目录（Fake 不使用）
            inputs: 输入参数（检查 diff_text 中的 trigger）
            timeout: 超时时间（秒）

        Returns:
            SandboxRun: 根据 trigger 返回相应的模拟结果
        """
        # 获取 diff_text 中的内容
        blob = inputs.get("diff_text", "")
        if blob is None:
            blob = ""

        # Trigger 1: force_sandbox_timeout → 模拟超时
        if "force_sandbox_timeout" in blob:
            return SandboxRun(
                runtime="fake",
                script=script,
                status="timeout",
                exit_code=124,
                stdout_redacted="",
                stderr_redacted="",
                truncated=False,
                error_type="TimeoutError",
                duration_ms=timeout * 1000,
            )

        # Trigger 2: force_sandbox_failure → 模拟执行失败
        if "force_sandbox_failure" in blob:
            return SandboxRun(
                runtime="fake",
                script=script,
                status="failed",
                exit_code=1,
                stdout_redacted="",
                stderr_redacted="Command failed",
                truncated=False,
                error_type="CalledProcessError",
                duration_ms=0,
            )

        # Trigger 3: force_secret_output → 模拟密钥泄露（Critical 1 加固：返回前脱敏）
        if "force_secret_output" in blob:
            # 模拟明文密钥输出，但在返回前脱敏（纵深防御）
            raw_stdout = "out sk-leaked-secret"
            stdout_redacted, _ = redact_text(raw_stdout)  # 应输出 "out [REDACTED_SK]"
            return SandboxRun(
                runtime="fake",
                script=script,
                status="success",
                exit_code=0,
                stdout_redacted=stdout_redacted,  # 返回脱敏后版本
                stderr_redacted="",
                truncated=False,
                error_type=None,
                duration_ms=5,
            )

        # Trigger 4: force_large_output → 模拟输出过大（截断）
        if "force_large_output" in blob:
            large_output = "x" * 10000  # 10KB 输出
            truncated_output, truncated = self._sanitize_output(large_output, max_bytes=7600)
            return SandboxRun(
                runtime="fake",
                script=script,
                status="success",
                exit_code=0,
                stdout_redacted=truncated_output,
                stderr_redacted="",
                truncated=truncated,
                error_type=None,
                duration_ms=5,
            )

        # 默认：成功执行
        return SandboxRun(
            runtime="fake",
            script=script,
            status="success",
            exit_code=0,
            stdout_redacted=f"ok:{script}",
            stderr_redacted="",
            truncated=False,
            error_type=None,
            duration_ms=5,
        )
