# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wrapper utilities for pre-execution tool safety checks."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._audit import write_audit_event
from ._scanner import ToolScriptSafetyScanner
from ._telemetry import record_safety_attributes
from ._types import Decision
from ._types import SafetyReport
from ._types import ToolScriptScanRequest


class ToolSafetyBlockedError(PermissionError):
    """Raised when a script is blocked before execution."""

    def __init__(self, report: SafetyReport):
        self.report = report
        super().__init__(report.summary)


@dataclass
class GuardedExecutionResult:
    """Result returned by the safety wrapper."""

    report: SafetyReport
    result: Any = None
    blocked: bool = False


class ToolSafetyGuard:
    """Pre-execution wrapper that scans, audits, traces, and optionally blocks."""

    def __init__(
        self,
        scanner: ToolScriptSafetyScanner | None = None,
        audit_log_path: str | Path | None = None,
        block_on_review: bool = False,
    ):
        self.scanner = scanner or ToolScriptSafetyScanner()
        self.audit_log_path = audit_log_path
        self.block_on_review = block_on_review

    def check(self, request: ToolScriptScanRequest) -> SafetyReport:
        report = self.scanner.scan(request)
        report.set_blocked(self._should_block(report))
        self._record_trace(report)
        if self.audit_log_path:
            write_audit_event(self.audit_log_path, report)
        return report

    async def run(
        self,
        request: ToolScriptScanRequest,
        execute: Callable[[], Awaitable[Any]],
    ) -> GuardedExecutionResult:
        report = self.check(request)
        if report.blocked:
            return GuardedExecutionResult(report=report, blocked=True)
        return GuardedExecutionResult(report=report, result=await execute(), blocked=False)

    def assert_allowed(self, request: ToolScriptScanRequest) -> SafetyReport:
        report = self.check(request)
        if report.blocked:
            raise ToolSafetyBlockedError(report)
        return report

    def _should_block(self, report: SafetyReport) -> bool:
        return report.decision == Decision.DENY or (self.block_on_review
                                                    and report.decision == Decision.NEEDS_HUMAN_REVIEW)

    @staticmethod
    def _record_trace(report: SafetyReport) -> None:
        record_safety_attributes(report)
