# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""沙箱执行后端基类。

定义 SandboxProvider 抽象基类，所有沙箱实现都需要继承此类。
"""

from abc import ABC, abstractmethod

from agent.models import SandboxRun


class SandboxProvider(ABC):
    """沙箱执行后端抽象基类。

    所有沙箱实现（Fake/Local/Container/Cube）都需要继承此类并实现 run 方法。
    沙箱永不抛：所有异常、超时、失败都应捕获并转换为 SandboxResult，
    返回 partial result 而不是中断调用方。

    典型的执行流程：
    1. 准备 workspace（挂载目录、复制文件等）
    2. 执行 script（带 timeout 限制）
    3. 捕获 stdout/stderr/exit_code
    4. 处理异常（超时、失败、崩溃等）
    5. 返回 SandboxRun（status ∈ {success, failed, timeout, blocked}）
    """

    @abstractmethod
    def run(
        self,
        script: str,
        workspace: str,
        inputs: dict,
        timeout: int = 30,
    ) -> SandboxRun:
        """在沙箱中执行脚本。

        Args:
            script: 要执行的脚本文件名（相对于 workspace）
            workspace: 工作目录路径（本地或远端）
            inputs: 输入参数字典（可能包含 diff_text 等）
            timeout: 超时时间（秒），默认 30

        Returns:
            SandboxRun: 执行结果，包含：
                - runtime: 沙箱类型（fake/local/container/cube）
                - script: 执行的脚本名
                - status: 状态（success/failed/timeout/blocked）
                - exit_code: 退出码（失败时为非 0）
                - stdout_redacted: 红色输出（可能截断/脱敏）
                - stderr_redacted: 错误输出（可能截断/脱敏）
                - truncated: 是否截断
                - error_type: 错误类型（TimeoutError/CalledProcessError/None）
                - duration_ms: 执行时长（毫秒）
        """
        pass

    def _sanitize_output(self, output: str, max_bytes: int = 7600) -> tuple[str, bool]:
        """截断输出到指定字节数。

        Args:
            output: 原始输出字符串
            max_bytes: 最大字节数（默认 7600）

        Returns:
            (截断后的输出, 是否截断)
        """
        if not output:
            return "", False

        encoded = output.encode('utf-8')
        if len(encoded) <= max_bytes:
            return output, False

        # 截断到 max_bytes，并尝试避免截断多字节字符中间
        truncated = encoded[:max_bytes].decode('utf-8', errors='ignore')
        return truncated, True

    def _redact_secrets(self, output: str) -> str:
        """简单脱敏：替换疑似密钥的 sk- 开头的内容。

        Args:
            output: 原始输出

        Returns:
            脱敏后的输出
        """
        import re

        # 简单替换 sk- 开头的内容为 sk-REDACTED
        pattern = r'sk-[a-zA-Z0-9]{20,}'
        return re.sub(pattern, 'sk-REDACTED', output)
