# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static Python and Bash safety scanner."""

from __future__ import annotations

import ast
import hashlib
import time
from typing import Iterable
from typing import Optional

from ._models import RISK_LEVEL_ORDER
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._models import highest_risk_level
from ._models import strictest_decision
from ._policy import ToolSafetyPolicy
from ._redaction import redact_text
from ._redaction import redact_value
from ._rules import BaseSafetyRule
from ._rules import DEFAULT_RULES
from ._rules import SafetyRule
from ._rules import SafetyRuleContext
from ._rules import annotate_python_bindings
from ._rules import parse_bash
from ._rules import shell_executable_text


class ToolSafetyScanner:
    """Run configurable, non-executing safety rules over one script request.

    Args:
        policy: Policy used for allowlists, limits, failure handling, and rule
            action overrides. Strict defaults are used when omitted.
        rules: Optional custom rules appended after the non-removable built-in
            policy and risk checks.
    """

    def __init__(
        self,
        policy: Optional[ToolSafetyPolicy] = None,
        rules: Optional[Iterable[SafetyRule]] = None,
    ) -> None:
        self.policy = policy or ToolSafetyPolicy()
        self.rules: tuple[SafetyRule, ...] = tuple(DEFAULT_RULES) + tuple(rules or ())
        for rule in self.rules:
            rule_id = getattr(rule, "rule_id", "")
            scan = getattr(rule, "scan", None)
            if not isinstance(rule_id, str) or not rule_id.strip() or not callable(scan):
                raise TypeError("safety rules must define a non-empty rule_id and callable scan(context, policy)")

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        """Statically scan one request and return a redacted structured report."""

        if not isinstance(request, SafetyScanRequest):
            raise TypeError("request must be a SafetyScanRequest")
        started = time.perf_counter()
        encoding_error = False
        try:
            encoded_script = request.script.encode("utf-8")
        except UnicodeEncodeError:
            encoding_error = True
            encoded_script = request.script.encode("utf-8", errors="replace")
        script_hash = hashlib.sha256(encoded_script).hexdigest()
        findings: list[SafetyFinding] = []
        if encoding_error and self.policy.fail_closed:
            findings.append(
                SafetyFinding(
                    rule_id="SCAN-ENCODING",
                    category=RiskCategory.SCAN_ERROR,
                    risk_level=RiskLevel.MEDIUM,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence="script contains text that is not valid UTF-8",
                    recommendation="Submit a valid UTF-8 script before execution.",
                ))

        # Oversized input is already denied by policy. Do not spend additional
        # CPU parsing every rule over an attacker-controlled multi-megabyte body.
        oversized = len(encoded_script) > self.policy.max_script_bytes
        if oversized:
            context = SafetyRuleContext(request=request)
        else:
            context, parse_findings = self._parse(request)
            findings.extend(parse_findings)
        for rule in self.rules:
            if oversized and getattr(rule, "rule_id", "") != "POLICY-LIMITS":
                continue
            try:
                produced = rule.scan(context, self.policy)
                if produced is None:
                    continue
                for finding in produced:
                    if not isinstance(finding, SafetyFinding):
                        raise TypeError(
                            f"rule {rule.rule_id} returned {type(finding).__name__}, expected SafetyFinding")
                    findings.append(finding)
            except Exception as exc:  # pylint: disable=broad-except
                if self.policy.fail_closed:
                    findings.append(
                        SafetyFinding(
                            rule_id="SCAN-RULE-ERROR",
                            category=RiskCategory.SCAN_ERROR,
                            risk_level=RiskLevel.MEDIUM,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=f"safety rule {rule.rule_id} failed with {type(exc).__name__}",
                            recommendation=("Review the request and repair or disable the failing rule before "
                                            "execution."),
                            metadata={
                                "failed_rule_id": rule.rule_id,
                                "error_type": type(exc).__name__
                            },
                        ))

        sanitized = self._normalize_findings(findings)
        decision = strictest_decision(sanitized)
        risk_level = highest_risk_level(sanitized)
        rule_ids = list(dict.fromkeys(finding.rule_id for finding in sanitized))
        primary_rule_id = self._primary_rule_id(sanitized, decision)
        blocked = decision == SafetyDecision.DENY or (decision == SafetyDecision.NEEDS_HUMAN_REVIEW
                                                      and self.policy.block_on_review)
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)

        report_data = {
            "tool_name": request.tool_name,
            "language": request.language,
            "languages": [request.language],
            "decision": decision,
            "risk_level": risk_level,
            "findings": sanitized,
            "rule_ids": rule_ids,
            "duration_ms": duration_ms,
            "script_sha256": script_hash,
            "policy_version": self.policy.version,
            "redacted": True,
            "blocked": blocked,
        }
        # ``rule_id`` was added after the initial report contract. Keeping this
        # conditional makes the scanner compatible with both during rolling
        # upgrades while always populating it when available.
        if "rule_id" in SafetyReport.model_fields:
            report_data["rule_id"] = primary_rule_id
        return SafetyReport.model_validate(report_data)

    def _parse(self, request: SafetyScanRequest) -> tuple[SafetyRuleContext, list[SafetyFinding]]:
        parse_findings: list[SafetyFinding] = []
        tree: Optional[ast.AST] = None
        shell_commands = ()
        aliases: dict[str, str] = {}
        instances: dict[str, str] = {}
        constants: dict[str, str] = {}
        executable_text = ""

        if not request.script.strip():
            if self.policy.fail_closed:
                parse_findings.append(
                    SafetyFinding(
                        rule_id="SCAN-EMPTY",
                        category=RiskCategory.SCAN_ERROR,
                        risk_level=RiskLevel.LOW,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence="script is empty and cannot be classified",
                        recommendation="Provide the exact script or command before requesting execution.",
                    ))
        elif request.language == ScriptLanguage.PYTHON:
            try:
                tree = ast.parse(request.script, mode="exec")
                aliases, instances, constants = annotate_python_bindings(tree)
            except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as exc:
                if self.policy.fail_closed:
                    parse_findings.append(
                        SafetyFinding(
                            rule_id="SCAN-SYNTAX",
                            category=RiskCategory.SCAN_ERROR,
                            risk_level=RiskLevel.MEDIUM,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=f"Python parsing failed with {type(exc).__name__}",
                            recommendation="Correct the syntax and resubmit the exact executable script.",
                            line_number=getattr(exc, "lineno", None),
                            column=max(0, (getattr(exc, "offset", 1) or 1) - 1),
                        ))
        elif request.language == ScriptLanguage.BASH:
            executable_text = shell_executable_text(request.script)
            try:
                shell_commands = parse_bash(request.script)
            except (ValueError, MemoryError) as exc:
                if self.policy.fail_closed:
                    parse_findings.append(
                        SafetyFinding(
                            rule_id="SCAN-SYNTAX",
                            category=RiskCategory.SCAN_ERROR,
                            risk_level=RiskLevel.MEDIUM,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=f"Bash parsing failed with {type(exc).__name__}",
                            recommendation="Correct quoting and resubmit the exact executable command.",
                        ))
        else:
            # Pydantic currently prevents this branch, but retain a defensive
            # failure mode for future enum extensions.
            if self.policy.fail_closed:
                parse_findings.append(
                    SafetyFinding(
                        rule_id="SCAN-SYNTAX",
                        category=RiskCategory.SCAN_ERROR,
                        risk_level=RiskLevel.MEDIUM,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence="unsupported script language",
                        recommendation="Use the Python or Bash scanner.",
                    ))

        return SafetyRuleContext(
            request=request,
            python_tree=tree,
            shell_commands=shell_commands,
            python_aliases=tuple(aliases.items()),
            python_instances=tuple(instances.items()),
            python_constants=tuple(constants.items()),
            shell_executable_text=executable_text,
        ), parse_findings

    def _normalize_findings(self, findings: Iterable[SafetyFinding]) -> list[SafetyFinding]:
        normalized: list[SafetyFinding] = []
        seen: set[tuple[object, ...]] = set()
        for finding in findings:
            invariant_rules = {
                "POLICY-CWD",
                "POLICY-OUTPUT-LIMIT",
                "POLICY-SCRIPT-SIZE",
                "POLICY-TIMEOUT",
                "SCAN-EMPTY",
                "SCAN-ENCODING",
                "SCAN-RULE-ERROR",
                "SCAN-SYNTAX",
            }
            decision = (finding.decision if finding.rule_id.upper() in invariant_rules else self.policy.action_for(
                finding.rule_id, finding.decision))
            evidence = redact_text(finding.evidence)
            recommendation = redact_text(finding.recommendation)
            metadata = redact_value(finding.metadata)
            rebuilt = SafetyFinding(
                rule_id=finding.rule_id.upper(),
                category=finding.category,
                risk_level=finding.risk_level,
                decision=decision,
                evidence=evidence,
                recommendation=recommendation,
                line_number=finding.line_number,
                column=finding.column,
                metadata=metadata,
            )
            key = (
                rebuilt.rule_id,
                rebuilt.category,
                rebuilt.risk_level,
                rebuilt.decision,
                rebuilt.evidence,
                rebuilt.line_number,
                rebuilt.column,
            )
            if key not in seen:
                seen.add(key)
                normalized.append(rebuilt)
        return normalized

    @staticmethod
    def _primary_rule_id(
        findings: list[SafetyFinding],
        decision: SafetyDecision,
    ) -> Optional[str]:
        candidates = [finding for finding in findings if finding.decision == decision]
        if not candidates:
            return None
        highest_risk = max((finding.risk_level for finding in candidates), key=RISK_LEVEL_ORDER.__getitem__)
        return next(finding.rule_id for finding in candidates if finding.risk_level == highest_risk)


__all__ = [
    "BaseSafetyRule",
    "SafetyRule",
    "SafetyRuleContext",
    "ToolSafetyScanner",
]
