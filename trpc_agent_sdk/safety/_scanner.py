# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Language routing, nested budgets, aggregation, and observation fan-out."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional
from typing import Sequence

from ._models import RiskLevel
from ._models import SafetyCategory
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyObservation
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import observation_from_report
from ._monitor import MonitorDispatcher
from ._monitor import MonitorSink
from ._policy import PolicyLoader
from ._policy import SafetyPolicy
from ._python_scanner import build_python_context
from ._redaction import redact_text
from ._redaction import sanitize
from ._redaction import sha256_text
from ._rule import ContextFindingRule
from ._rule import DecisionAggregator
from ._rule import SafetyRule
from ._rule import ScanContext
from ._shell_scanner import build_shell_context


@dataclass
class _NestedState:
    total_bytes: int
    child_count: int
    hashes: set[str]


class SafetyScanner:
    """Deterministic static scanner using one policy snapshot per root scan."""

    def __init__(
            self,
            policy: SafetyPolicy | PolicyLoader | None = None,
            *,
            rules: Optional[Sequence[SafetyRule]] = None,
            audit_sink: Optional[MonitorSink] = None,
            monitor_sinks: Sequence[MonitorSink] = (),
            telemetry_sink: Optional[MonitorSink] = None,
            report_observers: Sequence[Callable[[SafetyReport], None]] = (),
    ):
        self._policy_source = policy or SafetyPolicy.default()
        self._rules = (ContextFindingRule(), ) + tuple(rules or ())
        sinks = list(monitor_sinks)
        if audit_sink is not None:
            sinks.insert(0, audit_sink)
        if telemetry_sink is not None:
            sinks.append(telemetry_sink)
        self._dispatcher = MonitorDispatcher(sinks)
        self._report_observers = tuple(report_observers)

    @property
    def policy(self) -> SafetyPolicy:
        """Return the current in-memory snapshot without reading disk."""
        if isinstance(self._policy_source, PolicyLoader):
            return self._policy_source.snapshot
        return self._policy_source

    @property
    def rules(self) -> tuple[SafetyRule, ...]:
        return self._rules

    def _failure_finding(
        self,
        rule_id: str,
        message: str,
        risk: RiskLevel = RiskLevel.MEDIUM,
    ) -> SafetyFinding:
        return SafetyFinding(
            rule_id=rule_id,
            category=SafetyCategory.ANALYSIS,
            risk_level=risk,
            message=message,
            evidence="<analysis diagnostic>",
            recommendation="Review the source and scanner health before execution.",
        )

    def _context(self, request: SafetyScanRequest, policy: SafetyPolicy) -> ScanContext:
        if request.language == "python":
            return build_python_context(request.script, policy)
        if request.language in {"shell", "argv"}:
            return build_shell_context(
                request.script,
                policy,
                structured_argv=request.language == "argv",
            )
        finding = self._failure_finding("CORE.ANALYSIS.UNSUPPORTED_LANGUAGE",
                                        "Language is not supported by the static scanner.")
        return ScanContext(
            language=request.language,
            source=request.script,
            candidate_findings=(finding, ),
            analysis_complete=False,
            failure_code="unsupported_language",
            parse_count=0,
        )

    def _forced_decision(self, context: ScanContext, policy: SafetyPolicy) -> Optional[SafetyDecision]:
        if context.failure_code == "python_parse_failure" or context.failure_code == "shell_parse_failure":
            return policy.failures.parse_failure
        if context.failure_code == "unsupported_language":
            return policy.failures.unsupported_language
        if context.failure_code in {"nested_budget_exceeded", "source_budget_exceeded", "finding_budget_exceeded"}:
            return policy.failures.budget_exceeded
        if context.failure_code == "scanner_internal_error":
            return policy.failures.scanner_internal_error
        return None

    def _evaluate_rules(
        self,
        context: ScanContext,
        policy: SafetyPolicy,
    ) -> tuple[list[SafetyFinding], Optional[str], Optional[SafetyDecision], int]:
        findings: list[SafetyFinding] = []
        evaluated = 0
        failure_code = context.failure_code
        forced = self._forced_decision(context, policy)
        for rule in sorted(self._rules, key=lambda item: item.rule_id):
            if "*" not in rule.languages and context.language not in rule.languages:
                continue
            rule_policy = policy.rule_policy(rule.rule_id)
            if (rule_policy is not None and not rule_policy.enabled and not isinstance(rule, ContextFindingRule)):
                continue
            evaluated += 1
            try:
                findings.extend(rule.evaluate(context, policy))
            except Exception:  # pylint: disable=broad-except
                failure_code = "scanner_internal_error"
                forced = policy.failures.scanner_internal_error
                findings.append(
                    self._failure_finding(
                        "CORE.ANALYSIS.RULE_FAILURE",
                        "A safety rule failed internally.",
                        RiskLevel.HIGH,
                    ))
        return findings, failure_code, forced, evaluated

    def _scan_core(
        self,
        request: SafetyScanRequest,
        policy: SafetyPolicy,
        state: _NestedState,
        *,
        depth: int,
    ) -> tuple[list[SafetyFinding], bool, Optional[str], Optional[SafetyDecision], int]:
        encoded_size = len(request.script.encode("utf-8", errors="replace"))
        if state.total_bytes + encoded_size > policy.nested.max_total_bytes:
            return (
                [self._failure_finding("CORE.ANALYSIS.SOURCE_BUDGET", "Source byte budget was exceeded.")],
                False,
                "source_budget_exceeded",
                policy.failures.budget_exceeded,
                0,
            )
        state.total_bytes += encoded_size
        context = self._context(request, policy)
        findings, failure_code, forced, evaluated = self._evaluate_rules(context, policy)
        analysis_complete = context.analysis_complete
        unknown_rules = {
            "PY.FILESYSTEM.DYNAMIC_PATH",
            "PY.FILESYSTEM.DYNAMIC_MODE",
            "PY.NETWORK.DYNAMIC_TARGET",
            "PY.PROCESS.DYNAMIC_COMMAND",
            "SH.FILESYSTEM.DYNAMIC_REDIRECTION",
            "SH.NETWORK.DYNAMIC_TARGET",
            "SH.DYNAMIC.COMMAND",
        }
        if any(item.rule_id in unknown_rules for item in findings):
            failure_code = failure_code or "unknown_static_value"
            forced = policy.failures.unknown
            analysis_complete = False

        for index, candidate in enumerate(context.nested_candidates):
            if depth >= policy.nested.max_depth or state.child_count >= policy.nested.max_children:
                failure_code = failure_code or "nested_budget_exceeded"
                forced = policy.failures.budget_exceeded
                analysis_complete = False
                findings.append(
                    self._failure_finding(
                        "CORE.NESTED.BUDGET_EXCEEDED",
                        "Nested scan depth or child budget was exceeded.",
                    ))
                break
            child_hash = sha256_text(candidate.language + "\0" + candidate.script)
            if child_hash in state.hashes:
                continue
            state.hashes.add(child_hash)
            state.child_count += 1
            child_request = SafetyScanRequest(
                script=candidate.script,
                language=candidate.language,
                tool_name=request.tool_name,
                source_type="nested_script",
                source_name=candidate.reason,
                invocation_id=request.invocation_id,
                session_id=request.session_id,
            )
            child_findings, child_complete, child_failure, child_forced, child_evaluated = self._scan_core(
                child_request,
                policy,
                state,
                depth=depth + 1,
            )
            evaluated += child_evaluated
            analysis_complete = analysis_complete and child_complete
            failure_code = failure_code or child_failure
            if child_forced is SafetyDecision.DENY:
                forced = SafetyDecision.DENY
            elif child_forced is SafetyDecision.NEEDS_HUMAN_REVIEW and forced is None:
                forced = child_forced
            for finding in child_findings:
                findings.append(
                    finding.model_copy(
                        update={
                            "nested_path": (index, ) + finding.nested_path,
                            "line_number": candidate.line_number or finding.line_number,
                        }))
        return findings, analysis_complete, failure_code, forced, evaluated

    def _safe_observation(self, report: SafetyReport, policy: SafetyPolicy) -> SafetyObservation:
        observation = observation_from_report(report)
        limits = policy.redaction
        cleaned = sanitize(
            observation,
            max_depth=limits.max_depth,
            max_items=limits.max_items,
            max_string=limits.max_string_length,
            max_fields=limits.max_fields,
        )
        if not isinstance(cleaned, dict):
            return observation
        try:
            return SafetyObservation.model_validate(cleaned)
        except Exception:
            return observation

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        """Scan one root request; never execute it or reload policy from disk."""
        started = time.perf_counter_ns()
        policy = self.policy
        root_hash = sha256_text(request.language + "\0" + request.script)
        state = _NestedState(total_bytes=0, child_count=0, hashes={root_hash})
        try:
            findings, complete, failure_code, forced, evaluated = self._scan_core(
                request,
                policy,
                state,
                depth=0,
            )
        except Exception:  # pylint: disable=broad-except
            findings = [
                self._failure_finding(
                    "CORE.ANALYSIS.SCANNER_FAILURE",
                    "The static scanner failed internally.",
                    RiskLevel.HIGH,
                )
            ]
            complete = False
            failure_code = "scanner_internal_error"
            forced = policy.failures.scanner_internal_error
            evaluated = 0

        if request.block_index is not None:
            findings = [
                item if item.block_index is not None else item.model_copy(update={"block_index": request.block_index})
                for item in findings
            ]

        findings = [
            item.model_copy(
                update={
                    "rule_id": redact_text(item.rule_id, max_length=96),
                    "message": redact_text(item.message, max_length=policy.max_evidence_length),
                    "evidence": redact_text(item.evidence, max_length=policy.max_evidence_length),
                    "recommendation": redact_text(item.recommendation, max_length=policy.max_evidence_length),
                    "redacted": True,
                }) for item in findings
        ]

        aggregated = DecisionAggregator().aggregate(
            findings,
            policy,
            forced_decision=forced,
            failure_code=failure_code,
        )
        duration_ms = (time.perf_counter_ns() - started) / 1_000_000
        report = SafetyReport(
            decision=aggregated.decision,
            risk_level=aggregated.risk_level,
            rule_ids=tuple(sorted({item.rule_id
                                   for item in aggregated.findings})),
            findings=aggregated.findings,
            decision_reason=redact_text(aggregated.reason, max_length=policy.max_evidence_length),
            scan_duration_ms=duration_ms,
            execution_blocked=aggregated.decision is not SafetyDecision.ALLOW,
            tool_name=(redact_text(request.tool_name, max_length=128) if request.tool_name else None),
            source_type=redact_text(request.source_type, max_length=64),
            language=redact_text(request.language, max_length=32),
            script_hash=sha256_text(request.script),
            policy_version=redact_text(policy.policy_version, max_length=128),
            policy_hash=policy.policy_hash,
            analysis_complete=complete and aggregated.failure_code is None,
            failure_code=aggregated.failure_code,
            rules_evaluated=evaluated,
            finding_count=len(aggregated.findings),
        )
        for observer in self._report_observers:
            try:
                observer(report)
            except Exception:  # pylint: disable=broad-except
                continue
        observation = self._safe_observation(report, policy)
        self._dispatcher.emit(observation)
        return report


def scan_script(
    script: str,
    language: str,
    *,
    policy: Optional[SafetyPolicy] = None,
    source_type: str = "script",
) -> SafetyReport:
    """Convenience static scan using an in-memory policy."""
    scanner = SafetyScanner(policy or SafetyPolicy.default())
    return scanner.scan(SafetyScanRequest(script=script, language=language, source_type=source_type))
