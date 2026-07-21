# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety-guarded wrapper for framework code executors."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional

from pydantic import PrivateAttr
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeBlockDelimiter
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext

from ._extractor import normalize_script_language
from ._guard import ToolSafetyGuard
from ._models import DECISION_ORDER
from ._models import RISK_LEVEL_ORDER
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage

ExecutionContextEnricher = Callable[
    [BaseCodeExecutor, InvocationContext, CodeExecutionInput],
    Mapping[str, Any],
]


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Scan every code block before delegating to another executor."""

    tool_name: str = "code_executor"
    _inner: BaseCodeExecutor = PrivateAttr()
    _guard: ToolSafetyGuard = PrivateAttr()
    _context_enricher: Optional[ExecutionContextEnricher] = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        inner: BaseCodeExecutor,
        guard: ToolSafetyGuard,
        tool_name: Optional[str] = None,
        context_enricher: Optional[ExecutionContextEnricher] = None,
    ) -> None:
        mirrored = {name: getattr(inner, name) for name in BaseCodeExecutor.model_fields}
        super().__init__(
            tool_name=tool_name or type(inner).__name__,
            **mirrored,
        )
        self._inner = inner
        self._guard = guard
        self._context_enricher = context_enricher

    @property
    def inner(self) -> BaseCodeExecutor:
        """Return the wrapped executor."""

        return self._inner

    @property
    def guard(self) -> ToolSafetyGuard:
        """Return the safety guard used at the delegation boundary."""

        return self._guard

    def __deepcopy__(self, memo=None) -> "SafetyGuardedCodeExecutor":
        """Copy wrapper configuration without copying locks or runtime clients."""

        memo = {} if memo is None else memo
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        copied = type(self)(
            inner=self.inner,
            guard=self.guard,
            tool_name=self.tool_name,
            context_enricher=self._context_enricher,
        )
        memo[id(self)] = copied
        for field_name in BaseCodeExecutor.model_fields:
            value = getattr(self, field_name)
            copied_value = value if field_name == "workspace_runtime" else copy.deepcopy(value, memo)
            setattr(copied, field_name, copied_value)
        return copied

    def __getattr__(self, name: str) -> Any:
        """Delegate executor-specific lifecycle helpers to the wrapped executor."""

        try:
            return super().__getattr__(name)
        except AttributeError:
            private = object.__getattribute__(self, "__pydantic_private__")
            inner = private.get("_inner") if private else None
            if inner is None:
                raise
            return getattr(inner, name)

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan all blocks, aggregate blocked decisions, then delegate once."""

        reports = []
        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language=ScriptLanguage.PYTHON.value, code=code_execution_input.code)]
        if not blocks:
            return await self.inner.execute_code(invocation_context, code_execution_input)

        try:
            execution_context = self._execution_context(invocation_context, code_execution_input)
        except Exception as exc:  # pylint: disable=broad-except
            report = self.guard.failure_report(
                tool_name=self.tool_name,
                error=exc,
                record=False,
            )
            self.guard.record(report)
            return self._blocked_result([(0, report)])
        for index, block in enumerate(blocks):
            try:
                language = normalize_script_language(block.language, default=ScriptLanguage.PYTHON)
            except ValueError:
                report = self._unsupported_language_report(block)
            else:
                request = None
                try:
                    request = SafetyScanRequest(
                        script=block.code,
                        language=language,
                        tool_name=self.tool_name,
                        cwd=execution_context.get("cwd"),
                        environment_keys=execution_context.get("environment_keys", []),
                        timeout_seconds=execution_context.get("timeout_seconds"),
                        output_limit_bytes=execution_context.get("output_limit_bytes"),
                        metadata={
                            "block_index": index,
                            "execution_id": code_execution_input.execution_id,
                        },
                    )
                    report = self.guard.scan(request, record=False)
                except Exception as exc:  # pylint: disable=broad-except
                    report = self.guard.failure_report(
                        tool_name=self.tool_name,
                        error=exc,
                        request=request,
                        record=False,
                    )
            reports.append((index, report))

        try:
            aggregate = self.guard.merge_reports(report for _, report in reports)
        except Exception as exc:  # pylint: disable=broad-except
            aggregate = self.guard.failure_report(
                tool_name=self.tool_name,
                error=exc,
                record=False,
            )
            reports = [(0, aggregate)]
        self.guard.record(aggregate)
        blocked = [(index, report) for index, report in reports if report.blocked]
        if aggregate.blocked:
            if not blocked:
                blocked = reports
            return self._blocked_result(blocked)
        return await self.inner.execute_code(invocation_context, code_execution_input)

    def _execution_context(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> dict[str, Any]:
        """Collect safe-to-retain execution metadata from built-in executors."""

        environment = getattr(self.inner, "environment", None) or getattr(self.inner, "env", None) or {}
        timeout = getattr(self.inner, "timeout", None)
        context: dict[str, Any] = {
            "cwd": getattr(self.inner, "work_dir", None) or None,
            "environment_keys": sorted(str(key) for key in environment) if isinstance(environment, Mapping) else [],
            "timeout_seconds": timeout,
            "output_limit_bytes": None,
        }
        if self._context_enricher is not None:
            enriched = self._context_enricher(self.inner, invocation_context, code_execution_input)
            if not isinstance(enriched, Mapping):
                raise TypeError("code executor safety context enricher must return a mapping")
            allowed_keys = {"cwd", "environment_keys", "timeout_seconds", "output_limit_bytes"}
            context.update({key: value for key, value in enriched.items() if key in allowed_keys})
        return context

    def code_block_delimiter(self) -> list[CodeBlockDelimiter]:
        """Mirror delimiter behavior, including subclass overrides."""

        return self.inner.code_block_delimiter()

    def _unsupported_language_report(self, block: CodeBlock) -> SafetyReport:
        rule_id = "LANGUAGE_UNSUPPORTED"
        decision = self.guard.policy.action_for(rule_id, SafetyDecision.DENY)
        finding = SafetyFinding(
            rule_id=rule_id,
            category=RiskCategory.POLICY_VIOLATION,
            risk_level=RiskLevel.HIGH,
            decision=decision,
            evidence=f"Unsupported code block language: {block.language or '<empty>'}",
            recommendation="Use an explicitly supported Python or Bash code block.",
        )
        return SafetyReport(
            tool_name=self.tool_name,
            language=ScriptLanguage.PYTHON,
            languages=[ScriptLanguage.PYTHON],
            decision=decision,
            risk_level=RiskLevel.HIGH,
            findings=[finding],
            rule_id=rule_id,
            rule_ids=[rule_id],
            duration_ms=0,
            script_sha256=hashlib.sha256(block.code.encode("utf-8", errors="replace")).hexdigest(),
            policy_version=self.guard.policy.version,
            redacted=True,
            blocked=decision is not SafetyDecision.ALLOW,
        )

    @staticmethod
    def _blocked_result(blocked: list[tuple[int, SafetyReport]]) -> CodeExecutionResult:
        strictest = max((report.decision for _, report in blocked), key=DECISION_ORDER.__getitem__)
        highest_risk = max((report.risk_level for _, report in blocked), key=RISK_LEVEL_ORDER.__getitem__)
        payload: dict[str, Any] = {
            "error":
            "tool_safety_blocked",
            "blocked":
            True,
            "decision":
            strictest.value,
            "risk_level":
            highest_risk.value,
            "reports": [{
                "block_index": index,
                "language": report.language.value,
                "decision": report.decision.value,
                "risk_level": report.risk_level.value,
                "rule_id": getattr(report, "rule_id", None) or (report.rule_ids[0] if report.rule_ids else None),
                "rule_ids": list(report.rule_ids),
                "script_sha256": report.script_sha256,
            } for index, report in blocked],
        }
        stderr = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return create_code_execution_result(stderr=stderr)
