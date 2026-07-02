# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""SafetyScanner: orchestrates rules and aggregates findings into a report.

The scanner is the main entry point. It:
1. Normalizes the input language.
2. Runs every enabled rule (skipping those in policy.disabled_rules).
3. Aggregates findings into a SafetyReport with a final decision.
4. Redacts evidence snippets to avoid leaking secrets in reports.
"""
from __future__ import annotations

import time
from typing import Optional

from .policy import PolicyConfig
from .rules import SafetyRule
from .rules import default_rules
from .rules.base import evidence_snippet
from .rules.base import normalize_language
from .rules.secret_leak import _redact
from .types import Decision
from .types import max_risk_level
from .types import RiskLevel
from .types import SafetyFinding
from .types import SafetyReport
from .types import ScanInput

SCANNER_VERSION = "1.0.0"

# Module-level custom rule registry. Rules registered here are included in
# every new SafetyScanner that does not pass an explicit *rules* argument.
_custom_rules: list[SafetyRule] = []


def register_custom_rule(rule: SafetyRule) -> None:
    """Register a custom rule to be included in all new scanners by default.

    Existing SafetyScanner instances are unaffected; only scanners created
    after registration will include the new rule.
    """
    _custom_rules.append(rule)


class SafetyScanner:
    """Runs registered rules against a script and produces a SafetyReport."""

    def __init__(
        self,
        policy: PolicyConfig,
        rules: Optional[list[SafetyRule]] = None,
    ):
        self.policy = policy
        self.rules = rules if rules is not None else default_rules() + list(_custom_rules)

    def scan(self, scan_input: ScanInput) -> SafetyReport:
        """Scan *scan_input* and return a structured SafetyReport."""
        start = time.perf_counter()
        language = normalize_language(scan_input)

        findings: list[SafetyFinding] = []
        disabled = set(self.policy.disabled_rules)
        for rule in self.rules:
            if rule.rule_id in disabled:
                continue
            if not rule.applies(language):
                continue
            try:
                findings.extend(rule.check(scan_input, self.policy))
            except Exception as ex:  # pylint: disable=broad-expect
                # A rule crashing must not block scanning; record as low finding.
                findings.append(SafetyFinding(
                    rule_id="SCANNER_ERROR",
                    rule_name="Scanner Rule Error",
                    risk_type="scanner",
                    risk_level=RiskLevel.LOW,
                    evidence=f"{rule.rule_id} raised {type(ex).__name__}: {ex}",
                    line=None,
                    recommendation="Fix the rule implementation.",
                    metadata={"rule_id": rule.rule_id, "error": str(ex)},
                ))

        # Redact evidence that may itself contain secrets (always-on safety).
        for f in findings:
            f.evidence = _redact_evidence(f.evidence)

        elapsed_ms = (time.perf_counter() - start) * 1000
        agg_level = max_risk_level([f.risk_level for f in findings])
        decision = self.policy.decision_for(agg_level)

        return SafetyReport(
            decision=decision,
            risk_level=agg_level,
            findings=findings,
            rule_ids=[f.rule_id for f in findings],
            scanner_version=SCANNER_VERSION,
            scan_duration_ms=elapsed_ms,
            sanitized=True,
            blocked=(decision == Decision.DENY),
            tool_name=scan_input.tool_name,
            language=language,
        )


def _redact_evidence(text: str) -> str:
    """Best-effort redaction of obvious secret tokens in evidence snippets."""
    redacted = text
    # Trim long evidence first.
    redacted = evidence_snippet(redacted)
    # Redact bearer tokens and long hex/base64 runs.
    import re
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "bearer ***", redacted)
    redacted = re.sub(r"AKIA[0-9A-Z]{16}", "AKIA***", redacted)
    redacted = re.sub(r"(api[_-]?key|token|secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}",
                      lambda m: m.group(1) + "=***", redacted, flags=re.IGNORECASE)
    return redacted
