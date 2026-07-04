# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code executor wrapper for script safety scanning."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Outcome

from ._audit import SafetyAuditLogger
from ._audit import build_safety_audit_event
from ._audit import set_safety_span_attributes
from ._policy import SafetyPolicy
from ._policy import resolve_safety_policy
from ._rules import merge_findings
from ._rules import should_block_decision
from ._scanner import SafetyScanner
from ._types import RiskLevel
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanFinding
from ._types import ScanTarget
from ._types import ScriptLanguage

logger = logging.getLogger(__name__)

_TOOL_NAME = "code_executor"
_BLOCKED_HEADING = "Tool code execution blocked by safety policy"
_FAIL_CLOSED_EVIDENCE = "safety scan failed"
_EXECUTABLE_INPUT_FILE_SUFFIXES = {".py", ".sh", ".bash"}
_MIRRORED_EXECUTOR_FIELDS = (
    "optimize_data_file",
    "stateful",
    "error_retry_attempts",
    "execute_once_per_invocation",
    "code_block_delimiters",
    "execution_result_delimiters",
    "workspace_runtime",
    "ignore_codes",
)


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Wrap a code executor and scan code before delegating execution."""

    delegate: BaseCodeExecutor
    policy_path: str | Path | None = None
    policy: SafetyPolicy | None = None
    audit_logger: Any = None
    scanner: Any = None

    def model_post_init(self, __context: Any) -> None:
        """Resolve safety dependencies and mirror delegate executor fields."""

        resolved_policy = resolve_safety_policy(
            scanner=self.scanner,
            policy=self.policy,
            policy_path=self.policy_path,
        )
        resolved_scanner = self.scanner or SafetyScanner(resolved_policy)
        resolved_audit_logger = self.audit_logger or SafetyAuditLogger()

        object.__setattr__(self, "policy", resolved_policy)
        object.__setattr__(self, "scanner", resolved_scanner)
        object.__setattr__(self, "audit_logger", resolved_audit_logger)
        for field_name in _MIRRORED_EXECUTOR_FIELDS:
            object.__setattr__(self, field_name,
                               getattr(self.delegate, field_name))

    async def execute_code(
            self,
            invocation_context: InvocationContext,
            code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Scan code execution input before calling the wrapped executor."""

        targets = _build_scan_targets(code_execution_input)
        if not targets:
            return await self.delegate.execute_code(invocation_context,
                                                    code_execution_input)

        reports: list[SafetyReport] = []
        for target in targets:
            try:
                report = self.scanner.scan(target)
                if not isinstance(report, SafetyReport):
                    raise TypeError(
                        f"SafetyScanner.scan returned {type(report)!r}")
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning(
                    "Code executor safety scan failed: fail_closed=%s error_type=%s",
                    self.policy.fail_closed,
                    type(ex).__name__,
                )
                if self.policy.fail_closed:
                    report = _fail_closed_report(target, self.policy)
                    _record_report(invocation_context, self.audit_logger,
                                   report)
                    return _blocked_result(report)
                return await self.delegate.execute_code(
                    invocation_context, code_execution_input)
            reports.append(report)

        combined_report = _merge_reports(reports, self.policy)
        _record_report(invocation_context, self.audit_logger, combined_report)
        if combined_report.blocked:
            return _blocked_result(combined_report)

        return await self.delegate.execute_code(invocation_context,
                                                code_execution_input)


def _build_scan_targets(code_execution_input: CodeExecutionInput
                        ) -> list[ScanTarget]:
    targets: list[ScanTarget] = []
    if code_execution_input.code_blocks:
        for index, code_block in enumerate(code_execution_input.code_blocks):
            code = code_block.code or ""
            if not code.strip():
                continue
            targets.append(
                ScanTarget(
                    content=code,
                    language=_language_from_value(code_block.language),
                    tool_name=_TOOL_NAME,
                    tool_metadata={
                        "source": "code_block",
                        "index": index
                    },
                ))
    elif code_execution_input.code.strip():
        targets.append(
            ScanTarget(
                content=code_execution_input.code,
                language=ScriptLanguage.UNKNOWN,
                tool_name=_TOOL_NAME,
                tool_metadata={"source": "code"},
            ))

    for input_file in code_execution_input.input_files:
        name = input_file.name or ""
        suffix = Path(name).suffix.lower()
        if suffix not in _EXECUTABLE_INPUT_FILE_SUFFIXES:
            continue

        content = input_file.content or ""
        if not content.strip():
            continue

        targets.append(
            ScanTarget(
                content=content,
                language=_language_from_file_suffix(suffix),
                tool_name=_TOOL_NAME,
                tool_metadata={
                    "source": "input_file",
                    "file_name": name
                },
            ))

    return targets


def _language_from_value(value: object) -> ScriptLanguage:
    if isinstance(value, ScriptLanguage):
        return value
    if not isinstance(value, str):
        return ScriptLanguage.UNKNOWN

    normalized = value.strip().lower()
    if normalized in {"python", "python3", "py"}:
        return ScriptLanguage.PYTHON
    if normalized == "bash":
        return ScriptLanguage.BASH
    if normalized in {"sh", "shell"}:
        return ScriptLanguage.SHELL
    return ScriptLanguage.UNKNOWN


def _language_from_file_suffix(suffix: str) -> ScriptLanguage:
    if suffix == ".py":
        return ScriptLanguage.PYTHON
    if suffix == ".bash":
        return ScriptLanguage.BASH
    return ScriptLanguage.SHELL


def _merge_reports(reports: list[SafetyReport],
                   policy: SafetyPolicy) -> SafetyReport:
    if not reports:
        return SafetyReport(
            decision=SafetyDecision.ALLOW,
            risk_level=RiskLevel.LOW,
            findings=[],
            elapsed_ms=0.0,
            redacted=False,
            blocked=False,
            language=ScriptLanguage.UNKNOWN,
            policy_name=policy.name,
            metadata={"target_tool": _TOOL_NAME},
        )

    findings: list[ScanFinding] = []
    for report in reports:
        findings.extend(report.findings)

    decision, risk_level = merge_findings(findings)
    languages = {report.language for report in reports}
    language = next(
        iter(languages)) if len(languages) == 1 else ScriptLanguage.UNKNOWN
    parser_error = next((report.parser_error
                         for report in reports if report.parser_error), None)
    scanner_version = reports[0].scanner_version

    return SafetyReport(
        decision=decision,
        risk_level=risk_level,
        findings=findings,
        elapsed_ms=sum(report.elapsed_ms for report in reports),
        redacted=any(report.redacted for report in reports),
        blocked=should_block_decision(decision, policy),
        language=language,
        scanner_version=scanner_version,
        policy_name=policy.name,
        parser_error=parser_error,
        metadata={"target_tool": _TOOL_NAME},
    )


def _record_report(
        invocation_context: InvocationContext,
        audit_logger: Any,
        report: SafetyReport,
) -> None:
    event = build_safety_audit_event(
        report,
        tool_name=_TOOL_NAME,
        function_call_id=_context_value(invocation_context,
                                        "function_call_id"),
        agent_name=_context_value(invocation_context, "agent_name"),
    )
    audit_logger.emit(event)
    set_safety_span_attributes(report, tool_name=_TOOL_NAME)


def _context_value(invocation_context: InvocationContext, key: str) -> str:
    try:
        value = getattr(invocation_context, key, "")
    except Exception:  # pylint: disable=broad-except
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _fail_closed_report(target: ScanTarget,
                        policy: SafetyPolicy) -> SafetyReport:
    return SafetyReport(
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.HIGH,
        findings=[],
        elapsed_ms=0.0,
        redacted=False,
        blocked=True,
        language=target.language,
        policy_name=policy.name,
        metadata={"target_tool": _TOOL_NAME},
    )


def _blocked_result(report: SafetyReport) -> CodeExecutionResult:
    return CodeExecutionResult(
        outcome=Outcome.OUTCOME_FAILED,
        output=_blocked_text(report),
    )


def _blocked_text(report: SafetyReport) -> str:
    rules = _ordered_text(
        (finding.rule_id
         for finding in report.findings), separator=", ") or "none"
    evidence = _ordered_text(
        finding.evidence
        for finding in report.findings) or _FAIL_CLOSED_EVIDENCE
    recommendation = (_ordered_text(finding.recommendation
                                    for finding in report.findings)
                      or _fail_closed_recommendation())
    return "\n".join([
        _BLOCKED_HEADING,
        f"Decision: {report.decision.value}",
        f"Risk level: {report.risk_level.value}",
        f"Rules: {rules}",
        f"Evidence: {evidence}",
        f"Recommendation: {recommendation}",
    ])


def _ordered_text(values: Any, *, separator: str = "; ") -> str:
    ordered = [
        str(value) for value in dict.fromkeys(values) if str(value).strip()
    ]
    return separator.join(ordered)


def _fail_closed_recommendation() -> str:
    return "Retry after fixing the safety scanner configuration, or use fail_open only after review."
