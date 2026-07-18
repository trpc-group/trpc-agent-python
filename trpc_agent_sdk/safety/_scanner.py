# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SafetyScanner: orchestrates rules and aggregates findings into a report."""
from __future__ import annotations

import re
import time
from typing import Optional

from ._ast_utils import evidence_snippet
from ._ast_utils import extract_inline_payloads
from ._ast_utils import normalize_language
from ._policy import PolicyConfig
from ._rules import SafetyRule
from ._rules import default_rules
from ._rules import redact
from ._types import Decision
from ._types import RiskLevel
from ._types import SafetyFinding
from ._types import SafetyReport
from ._types import ScanInput
from ._types import max_risk_level

import threading

SCANNER_VERSION = "1.2.0"

_custom_rules: list[SafetyRule] = []
_custom_rules_lock = threading.RLock()


def register_custom_rule(rule: SafetyRule) -> None:
    """Register a custom rule included in new scanners by default.

    Existing SafetyScanner instances are unaffected. Registration is
    idempotent by rule_id. Prefer passing ``rules=`` to SafetyScanner for
    process-local isolation; the global registry is a convenience for plugins.
    """
    with _custom_rules_lock:
        global _custom_rules
        _custom_rules = [r for r in _custom_rules if r.rule_id != rule.rule_id]
        _custom_rules.append(rule)


def register_rule(cls: type | None = None):
    """Class decorator that registers a SafetyRule subclass instance.

    Usage::

        @register_rule
        class MyRule(SafetyRule):
            rule_id = "CUSTOM_001"
            ...
    """

    def _decorate(rule_cls: type) -> type:
        instance = rule_cls()
        register_custom_rule(instance)
        return rule_cls

    if cls is None:
        return _decorate
    return _decorate(cls)


def unregister_custom_rule(rule_id: str) -> bool:
    """Remove a previously registered custom rule. Returns True if removed."""
    with _custom_rules_lock:
        global _custom_rules
        before = len(_custom_rules)
        _custom_rules = [r for r in _custom_rules if r.rule_id != rule_id]
        return len(_custom_rules) < before


def clear_custom_rules() -> None:
    """Remove all custom rules from the registry."""
    with _custom_rules_lock:
        global _custom_rules
        _custom_rules = []


def _snapshot_custom_rules() -> list[SafetyRule]:
    with _custom_rules_lock:
        return list(_custom_rules)


class SafetyScanner:
    """Runs registered rules against a script and produces a SafetyReport."""

    def __init__(
        self,
        policy: PolicyConfig,
        rules: Optional[list[SafetyRule]] = None,
    ):
        self.policy = policy
        # Prefer explicit rules= for isolation; global registry is a snapshot.
        self.rules = rules if rules is not None else default_rules() + _snapshot_custom_rules()

    def scan(self, scan_input: ScanInput) -> SafetyReport:
        """Scan *scan_input* and return a structured SafetyReport."""
        start = time.perf_counter()
        language = normalize_language(scan_input)

        findings: list[SafetyFinding] = []
        disabled = set(self.policy.disabled_rules)

        # Primary script
        findings.extend(self._run_rules(scan_input, language, disabled))

        # Secondary: rescan python/bash -c payloads embedded in the script
        for payload_lang, payload in extract_inline_payloads(scan_input.script or ""):
            nested = ScanInput(
                script=payload,
                language=payload_lang,
                args=scan_input.args,
                workdir=scan_input.workdir,
                env=scan_input.env,
                tool_name=scan_input.tool_name,
                tool_description=scan_input.tool_description,
            )
            findings.extend(self._run_rules(nested, payload_lang, disabled))

        # Also scan command-line args if provided (including nested -c payloads).
        if scan_input.args:
            joined = " ".join(str(a) for a in scan_input.args)
            if joined.strip():
                arg_input = ScanInput(
                    script=joined,
                    language="bash",
                    tool_name=scan_input.tool_name,
                )
                findings.extend(self._run_rules(arg_input, "bash", disabled))
                for payload_lang, payload in extract_inline_payloads(joined):
                    nested = ScanInput(
                        script=payload,
                        language=payload_lang,
                        tool_name=scan_input.tool_name,
                    )
                    findings.extend(self._run_rules(nested, payload_lang, disabled))

        for f in findings:
            f.evidence = _redact_evidence(f.evidence)
            # metadata.message may embed original script lines; redact too so
            # SafetyReport.to_dict() / report JSON never leaks secrets.
            if isinstance(f.metadata, dict) and "message" in f.metadata:
                f.metadata["message"] = _redact_evidence(str(f.metadata["message"]))

        elapsed_ms = (time.perf_counter() - start) * 1000
        agg_level = max_risk_level([f.risk_level for f in findings])
        decision = self.policy.decision_for(agg_level)
        # Deduplicate rule ids while preserving order
        seen: set[str] = set()
        rule_ids: list[str] = []
        for f in findings:
            if f.rule_id not in seen:
                seen.add(f.rule_id)
                rule_ids.append(f.rule_id)

        # blocked means "would be intercepted under this policy": DENY always,
        # and NEEDS_HUMAN_REVIEW when policy.block_on_review is enabled.
        blocked = decision == Decision.DENY or (decision == Decision.NEEDS_HUMAN_REVIEW and self.policy.block_on_review)
        return SafetyReport(
            decision=decision,
            risk_level=agg_level,
            findings=findings,
            rule_ids=rule_ids,
            scanner_version=SCANNER_VERSION,
            scan_duration_ms=elapsed_ms,
            sanitized=True,
            blocked=blocked,
            tool_name=scan_input.tool_name,
            language=language,
        )

    def _run_rules(
        self,
        scan_input: ScanInput,
        language: str,
        disabled: set[str],
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for rule in self.rules:
            if rule.rule_id in disabled:
                continue
            if not rule.applies(language):
                continue
            try:
                findings.extend(rule.check(scan_input, self.policy))
            except Exception as ex:  # pylint: disable=broad-except
                findings.append(
                    SafetyFinding(
                        rule_id="SCANNER_ERROR",
                        rule_name="Scanner Rule Error",
                        risk_type="scanner",
                        risk_level=RiskLevel.LOW,
                        evidence=f"{rule.rule_id} raised {type(ex).__name__}: {ex}",
                        line=None,
                        recommendation="Fix the rule implementation.",
                        metadata={
                            "rule_id": rule.rule_id,
                            "error": str(ex)
                        },
                    ))
        return findings


def _redact_evidence(text: str) -> str:
    """Best-effort redaction of obvious secret tokens in evidence snippets."""
    redacted = evidence_snippet(text)
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "bearer ***", redacted)
    redacted = re.sub(r"AKIA[0-9A-Z]{16}", "AKIA***", redacted)
    redacted = re.sub(
        r"(api[_-]?key|token|secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}",
        lambda m: m.group(1) + "=***",
        redacted,
        flags=re.IGNORECASE,
    )
    # Also apply generic redact for long tokens
    if len(redacted) > 80 and re.search(r"[A-Za-z0-9_\-]{32,}", redacted):
        redacted = redact(redacted, keep=8)
    return redacted
