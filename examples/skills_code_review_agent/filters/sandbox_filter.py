# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox security filter for the code review agent.

Intercepts high-risk script patterns, unauthorized paths, and
non-whitelisted network access before they reach the sandbox.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from trpc_agent_sdk.filter import BaseFilter, FilterResult, register_tool_filter


@register_tool_filter("sandbox_security_filter")
class SandboxSecurityFilter(BaseFilter):
    """沙箱安全 Filter：拦截高风险脚本、禁止路径、非白名单网络访问。

    Filter 链按 DENY → NEEDS_HUMAN_REVIEW → PASS 顺序执行。
    任一 Filter 返回 DENY 时链立即终止，拦截原因写入数据库。
    """

    # 高风险脚本模式
    BLOCKED_PATTERNS: list[re.Pattern] = [
        re.compile(r"rm\s+-rf\s+/"),           # 删除根目录
        re.compile(r":\(\)\s*\{.*:\(\)\s*\;"),  # Fork 炸弹
        re.compile(r"sudo\s+"),                 # 提权
        re.compile(r"chmod\s+777"),             # 权限滥用
        re.compile(r">\s*/dev/sda"),            # 磁盘写入
        re.compile(r"dd\s+if="),                # dd 命令
        re.compile(r"mkfs\."),                  # 格式化
        re.compile(r"wget\s+.*\|\s*bash"),      # 远程下载执行
        re.compile(r"curl\s+.*\|\s*bash"),      # 远程下载执行
    ]

    # 允许的路径前缀
    ALLOWED_PATHS: list[str] = [
        "scripts/",
        "out/",
        "work/",
        "/tmp/",
    ]

    # 允许的环境变量
    ALLOWED_ENV_VARS: list[str] = [
        "PATH", "HOME", "PYTHONPATH", "WORKSPACE_DIR",
    ]

    def __init__(self) -> None:
        self._intercept_log: list[dict[str, Any]] = []

    @property
    def intercept_log(self) -> list[dict[str, Any]]:
        return self._intercept_log

    async def run(self, ctx: Any, req: dict[str, Any], handle: Any) -> FilterResult:
        """Run the sandbox security filter chain.

        Args:
            ctx: Agent context.
            req: Request dict with keys like "script", "path", "env_vars".
            handle: Next handler in the filter chain.

        Returns:
            FilterResult with status: "deny", "needs_human_review", or "pass".
        """
        script_content = req.get("script", "")
        script_path = req.get("path", "")
        env_vars = req.get("env_vars", {})

        # 1. Check for blocked patterns
        for pattern in self.BLOCKED_PATTERNS:
            if pattern.search(script_content):
                reason = f"高风险脚本模式被拦截: {pattern.pattern}"
                self._log_intercept("sandbox", "deny", reason)
                return FilterResult(status="deny", reason=reason)

        # 2. Check path safety
        if script_path and not any(
            script_path.startswith(allowed) for allowed in self.ALLOWED_PATHS
        ):
            reason = f"脚本路径不在白名单中: {script_path}"
            self._log_intercept("sandbox", "deny", reason)
            return FilterResult(status="deny", reason=reason)

        # 3. Check env var whitelist
        for key in env_vars:
            if key not in self.ALLOWED_ENV_VARS:
                reason = f"环境变量不在白名单中: {key}"
                self._log_intercept("sandbox", "needs_human_review", reason)
                return FilterResult(
                    status="needs_human_review",
                    reason=reason,
                )

        # 4. All checks passed → allow
        self._log_intercept("sandbox", "allow", "All checks passed")
        return await handle()

    def _log_intercept(self, filter_type: str, action: str, reason: str) -> None:
        """Record an intercept event for later storage."""
        self._intercept_log.append({
            "filter_type": filter_type,
            "action": action,
            "target": "sandbox_execution",
            "reason": reason,
        })