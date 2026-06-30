# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Decision engine for the Tool Script Safety Guard.

The engine runs the appropriate scanner(s), redacts evidence, aggregates the
per-finding actions/levels into a single :class:`Decision` and returns a
:class:`SafetyReport`.

Aggregation (design doc section 4) -- each finding contributes a decision that
is the **more severe** of (a) the action the rule suggests and (b) the decision
its risk level maps to via ``policy.decision_thresholds``. The report decision
is the most severe finding decision, or ``ALLOW`` when there are no findings::

    finding with CRITICAL/HIGH and action=deny -> DENY
    any finding with action=deny               -> DENY
    any finding with MEDIUM or action=review   -> NEEDS_HUMAN_REVIEW
    otherwise                                  -> ALLOW

Because the three must-catch categories (secret read / dangerous delete /
non-allow-listed egress) are fixed at CRITICAL + DENY in ``rules.py``, they deny
under any reasonable threshold tuning.
"""

from __future__ import annotations

import time
from typing import Optional

from .models import Decision
from .models import Language
from .models import RiskFinding
from .models import RiskLevel
from .models import SafetyReport
from .models import ScanInput
from .policy import SafetyPolicy
from .policy import load_policy
from .scanners import BashScanner
from .scanners import PythonScanner
from .scanners.patterns import redact_text

# Severity ordering for decisions (higher wins during aggregation).
_DECISION_ORDER: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.NEEDS_HUMAN_REVIEW: 1,
    Decision.DENY: 2,
}

_ACTION_TO_DECISION = {
    "allow": Decision.ALLOW,
    "review": Decision.NEEDS_HUMAN_REVIEW,
    "deny": Decision.DENY,
}

_STRING_TO_DECISION = {
    "allow": Decision.ALLOW,
    "needs_human_review": Decision.NEEDS_HUMAN_REVIEW,
    "review": Decision.NEEDS_HUMAN_REVIEW,
    "deny": Decision.DENY,
}


def _more_severe(a: Decision, b: Decision) -> Decision:
    return a if _DECISION_ORDER[a] >= _DECISION_ORDER[b] else b


class SafetyEngine:
    """Runs scanners and aggregates findings into a :class:`SafetyReport`."""

    def __init__(self, policy: Optional[SafetyPolicy] = None) -> None:
        self.policy = policy or load_policy()
        self._python = PythonScanner()
        self._bash = BashScanner()

    # ------------------------------------------------------------------ #
    def scan(self, scan_input: ScanInput) -> SafetyReport:
        """Scan one payload and return a structured report. Never raises."""
        start = time.perf_counter()
        findings = self._run_scanners(scan_input)
        findings, redacted = self._redact(findings)
        decision = self._aggregate_decision(findings)
        risk_level = self._aggregate_risk_level(findings)
        duration_ms = (time.perf_counter() - start) * 1000.0
        return SafetyReport(
            tool_name=scan_input.tool_name,
            language=scan_input.language.value
            if isinstance(scan_input.language, Language) else str(scan_input.language),
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            redacted=redacted,
            scan_duration_ms=duration_ms,
        )

    def scan_script(self, script: str, language: Language = Language.UNKNOWN,
                    tool_name: str = "unknown", **kwargs) -> SafetyReport:
        """Convenience wrapper around :meth:`scan`."""
        return self.scan(ScanInput(script=script, language=language, tool_name=tool_name, **kwargs))

    # ------------------------------------------------------------------ #
    def _run_scanners(self, scan_input: ScanInput) -> list[RiskFinding]:
        # Enforce a hard input-size ceiling before any scanning (DoS guard).
        limit = self.policy.scan_limits.max_input_size
        if scan_input.script and len(scan_input.script) > limit:
            scan_input = ScanInput(
                script=scan_input.script[:limit],
                tool_name=scan_input.tool_name,
                language=scan_input.language,
                args=scan_input.args,
                cwd=scan_input.cwd,
                env=scan_input.env,
            )
        lang = scan_input.language
        if lang == Language.BASH:
            return self._bash.scan(scan_input, self.policy)
        # PYTHON and UNKNOWN both go through the Python scanner: it runs the
        # shared text scan unconditionally and degrades to a pure text scan on a
        # SyntaxError, so it safely covers shell one-liners too.
        return self._python.scan(scan_input, self.policy)

    def _redact(self, findings: list[RiskFinding]) -> tuple[list[RiskFinding], bool]:
        any_redacted = False
        for f in findings:
            masked, changed = redact_text(f.evidence.snippet, self.policy)
            if changed:
                any_redacted = True
                f.evidence.snippet = masked
        return findings, any_redacted

    def _finding_decision(self, finding: RiskFinding) -> Decision:
        action_decision = _ACTION_TO_DECISION.get(finding.suggested_action.value, Decision.ALLOW)
        threshold_str = self.policy.decision_thresholds.get(finding.risk_level.value, "allow")
        threshold_decision = _STRING_TO_DECISION.get(threshold_str, Decision.ALLOW)
        return _more_severe(action_decision, threshold_decision)

    def _aggregate_decision(self, findings: list[RiskFinding]) -> Decision:
        decision = Decision.ALLOW
        for f in findings:
            decision = _more_severe(decision, self._finding_decision(f))
        return decision

    @staticmethod
    def _aggregate_risk_level(findings: list[RiskFinding]) -> RiskLevel:
        if not findings:
            return RiskLevel.LOW
        return max((f.risk_level for f in findings), key=lambda r: r.order)
