# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Lightweight wrappers for applying safety checks before execution."""

from __future__ import annotations

from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from .checker import SafetyChecker
from .models import SafetyDecision
from .models import SafetyResult
from .models import ToolExecutionRequest
from .policy import SafetyPolicy

ExecutionHandler = Callable[[], Awaitable[Any]]


class SafetyViolationError(RuntimeError):
    """Raised when a safety result blocks execution."""

    def __init__(self, result: SafetyResult):
        self.result = result
        super().__init__(f"tool execution blocked by safety policy: {result.decision.value}")


class SafetyExecutionWrapper:
    """Apply a checker before invoking an async execution handler."""

    def __init__(self, checker: Optional[SafetyChecker] = None, policy: Optional[SafetyPolicy] = None):
        self._checker = checker or SafetyChecker(policy=policy)
        self._policy = policy

    async def check(self, request: ToolExecutionRequest) -> SafetyResult:
        """Run the configured checker."""
        return await self._checker.check(request, self._policy)

    async def run(self, request: ToolExecutionRequest, handler: ExecutionHandler) -> Any:
        """Check a request and run the handler when allowed."""
        result = await self.check(request)
        if result.decision != SafetyDecision.ALLOW:
            raise SafetyViolationError(result)
        return await handler()
