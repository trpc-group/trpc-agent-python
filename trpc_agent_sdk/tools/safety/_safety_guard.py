# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Main orchestrator for the Tool Script Safety Guard.

The :class:`SafetyGuard` is the single entry point for scanning a script
before it is executed by a Tool, MCP Tool, Skill or CodeExecutor.

Typical usage::

    from trpc_agent_sdk.tools.safety import SafetyGuard

    guard = SafetyGuard.from_yaml("tool_safety_policy.yaml")
    report = guard.scan(
        script="import os; os.system('rm -rf /')",
        tool_name="BashTool",
    )
    if report.decision == Decision.DENY:
        raise SafetyBlockedError(report)

The guard is deliberately framework-agnostic.  The Filter integration
(:mod:`trpc_agent_sdk.tools.safety._safety_filter`) wraps it for use
inside the Tool execution pipeline.
"""

from __future__ import annotations

import ast
import os
import re
import time
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional
from typing import Union

from ._audit import AuditLogger
from ._models import AuditEvent
from ._models import compute_script_hash
from ._models import Decision
from ._models import Finding
from ._models import RiskLevel
from ._models import SafetyReport
from ._models import ScriptType
from ._policy import SafetyPolicy
from ._rules import Rule
from ._rules import RuleRegistry
from ._rules import ScanContext
from ._rules import global_rule_registry
from ._telemetry import report_to_span


# ---------------------------------------------------------------------------
# Script-type detection
# ---------------------------------------------------------------------------

_PYTHON_INDICATORS = re.compile(
    r"^\s*(import\s+\w|from\s+\w+\s+import|def\s+\w+|class\s+\w+|"
    r"if\s+__name__|print\s*\(|#\s*!.*python)",
    re.MULTILINE,
)

_BASH_INDICATORS = re.compile(
    r"^\s*#!/[^\n]*(bash|sh|zsh)|"
    r"\b(echo|cd|ls|cat|grep|sed|awk|curl|wget|export|source|"
    r"rm|cp|mv|mkdir|chmod|chown|sudo|su\b|pip\d?|npm|apt|"
    r"kill|ps|tar|gzip|gunzip|base64|nc|netcat|telnet|"
    r"head|tail|wc|sort|uniq|tr|cut|tee|xargs|find|type)\s|"
    r"\$\w+|\$\{|\|\||&&|;\s*\w|"
    r"\brm\s+-[a-zA-Z]*r|"
    r"\bfor\s+\w+\s+in\s.*;\s*do\b|"
    r"\bwhile\s+.*;\s*do\b",
    re.IGNORECASE | re.MULTILINE,
)

# Python-specific patterns that take priority over bash heuristics.
_PYTHON_STRONG_INDICATORS = re.compile(
    r"^\s*(import\s+\w|from\s+\w+\s+import|def\s+\w+\s*\(|class\s+\w+|"
    r"if\s+__name__|while\s+\w.*:\s*$|for\s+\w+\s+in\s.*:\s*$|"
    r"try\s*:|except\s|with\s+\w+|raise\s|lambda\s|"
    r"\w+\s*=\s*\w+\(|print\s*\(|len\s*\(|range\s*\()",
    re.MULTILINE,
)


def detect_script_type(script: str, hint: Optional[str] = None) -> ScriptType:
    """Heuristically detect whether *script* is Python or Bash.

    Args:
        script: The script content.
        hint: Optional explicit hint (``"python"``, ``"bash"``, or a
            filename like ``"script.py"``).

    Returns:
        The detected :class:`ScriptType`.
    """
    if hint:
        hint_lower = hint.lower()
        if hint_lower.endswith(".py") or "python" in hint_lower:
            return ScriptType.PYTHON
        if hint_lower.endswith(".sh") or hint_lower in ("bash", "sh", "shell"):
            return ScriptType.BASH

    # Shebang line is authoritative
    first_line = script.splitlines()[0] if script.strip() else ""
    if first_line.startswith("#!"):
        if "python" in first_line.lower():
            return ScriptType.PYTHON
        if re.search(r"\b(bash|sh|zsh|dash)\b", first_line.lower()):
            return ScriptType.BASH

    # If the script parses as valid Python, treat it as Python.  This is
    # the most reliable signal: bash commands almost never parse as valid
    # Python, and Python code always does.
    try:
        import ast as _ast
        _ast.parse(script)
        # Confirm it has at least one Python-ish construct to avoid
        # treating empty / trivial strings as Python.
        if _PYTHON_STRONG_INDICATORS.search(script) or _PYTHON_INDICATORS.search(script):
            return ScriptType.PYTHON
        # Even without strong indicators, if it parses as Python and
        # doesn't look like bash, treat it as Python.
        if not _BASH_INDICATORS.search(script):
            return ScriptType.PYTHON
    except (SyntaxError, ValueError):
        pass

    # Content heuristics
    py_strong = len(_PYTHON_STRONG_INDICATORS.findall(script))
    py_score = len(_PYTHON_INDICATORS.findall(script)) + py_strong
    bash_score = len(_BASH_INDICATORS.findall(script))

    # Python strong indicators (def, class, import, while…:) take priority.
    if py_strong > 0 and py_strong >= bash_score:
        return ScriptType.PYTHON
    if py_score > bash_score:
        return ScriptType.PYTHON
    if bash_score > 0:
        return ScriptType.BASH
    # If it looks like neither, treat single-line / short snippets as bash
    # (they are most likely shell commands) and multi-line as unknown.
    line_count = script.strip().count("\n") + 1
    if line_count <= 3 and script.strip():
        return ScriptType.BASH

    return ScriptType.UNKNOWN


# ---------------------------------------------------------------------------
# Decision aggregation
# ---------------------------------------------------------------------------

def _aggregate_decision(findings: list[Finding]) -> tuple[Decision, RiskLevel]:
    """Compute the overall decision and risk level from a list of findings.

    The worst finding wins:
    * Any ``DENY`` finding → ``DENY``.
    * Else any ``NEEDS_HUMAN_REVIEW`` finding → ``NEEDS_HUMAN_REVIEW``.
    * Else ``ALLOW``.

    Risk level is the maximum across all findings.
    """
    if not findings:
        return Decision.ALLOW, RiskLevel.NONE

    decisions = {f.decision for f in findings}
    if Decision.DENY in decisions:
        decision = Decision.DENY
    elif Decision.NEEDS_HUMAN_REVIEW in decisions:
        decision = Decision.NEEDS_HUMAN_REVIEW
    else:
        decision = Decision.ALLOW

    risk_level = RiskLevel.max(*[f.risk_level for f in findings])
    return decision, risk_level


def _build_summary(decision: Decision, risk_level: RiskLevel,
                   findings: list[Finding], script_type: ScriptType) -> str:
    """Build a short human-readable summary for the report."""
    if not findings:
        return f"No risks detected in {script_type.value} script; execution allowed."
    parts = [
        f"Decision: {decision.value} (risk: {risk_level.value}).",
        f"Detected {len(findings)} finding(s): "
        + ", ".join(sorted({f.category for f in findings})),
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# SafetyGuard
# ---------------------------------------------------------------------------

class SafetyGuard:
    """Orchestrates script safety scanning.

    Args:
        policy: The safety policy to use.  If ``None``, uses defaults.
        registry: Custom rule registry.  If ``None``, uses the global
            registry (which contains all built-in Python + Bash rules).
        audit_logger: Optional audit logger.  If ``None``, no audit
            events are written.
    """

    def __init__(
        self,
        policy: Optional[SafetyPolicy] = None,
        registry: Optional[RuleRegistry] = None,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self.policy = policy or SafetyPolicy.default()
        self.registry = registry or global_rule_registry
        self.audit_logger = audit_logger

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Union[str, os.PathLike],
                  audit_logger: Optional[AuditLogger] = None) -> "SafetyGuard":
        """Build a guard from a YAML policy file."""
        return cls(policy=SafetyPolicy.from_yaml(path), audit_logger=audit_logger)

    @classmethod
    def default(cls, audit_logger: Optional[AuditLogger] = None) -> "SafetyGuard":
        """Build a guard with built-in default policy and rules."""
        return cls(audit_logger=audit_logger)

    # ------------------------------------------------------------------
    # Core scan
    # ------------------------------------------------------------------

    def scan(
        self,
        script: str,
        tool_name: str = "unknown",
        *,
        args: Optional[dict[str, Any]] = None,
        cwd: str = "",
        env: Optional[dict[str, str]] = None,
        script_type_hint: Optional[str] = None,
        emit_audit: bool = True,
    ) -> SafetyReport:
        """Scan *script* and return a :class:`SafetyReport`.

        Args:
            script: The script content to scan.
            tool_name: Name of the tool that would execute the script.
            args: Command-line arguments that would be passed.
            cwd: Working directory for execution.
            env: Environment variables (only keys are inspected).
            script_type_hint: Optional hint for script-type detection.
            emit_audit: Whether to write an audit event (default True).

        Returns:
            A :class:`SafetyReport` with the final decision, findings,
            and metadata.
        """
        start = time.perf_counter()
        script_type = detect_script_type(script, script_type_hint)
        script_hash = compute_script_hash(script)

        # Parse AST once for all Python rules to share (major perf win:
        # avoids 6 separate ast.parse() calls for the same script).
        cached_tree = None
        if script_type == ScriptType.PYTHON:
            try:
                cached_tree = ast.parse(script)
            except (SyntaxError, ValueError):
                cached_tree = None

        # Pre-compile secret patterns once for both Python and Bash rules.
        compiled_secrets = [re.compile(p) for p in self.policy.secret_patterns]

        # Build the scan context
        ctx = ScanContext(
            script=script,
            script_type=script_type,
            args=args or {},
            cwd=cwd,
            env=env or {},
            tool_name=tool_name,
            policy=self.policy,
            cached_tree=cached_tree,
            cached_lines=script.splitlines(),
            compiled_secrets=compiled_secrets,
        )

        # Check script-length limit
        findings: list[Finding] = []
        line_count = script.count("\n") + 1
        if line_count > self.policy.max_script_lines:
            findings.append(Finding(
                rule_id="GUARD-SCRIPT-TOO-LONG",
                category="resource_abuse",
                risk_level=RiskLevel.MEDIUM,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                description=f"Script exceeds maximum line count "
                            f"({line_count} > {self.policy.max_script_lines})",
                evidence=f"{line_count} lines",
                recommendation="Split the script or increase max_script_lines in policy.",
            ))

        # Large-script review threshold
        if line_count > self.policy.large_script_threshold and line_count <= self.policy.max_script_lines:
            findings.append(Finding(
                rule_id="GUARD-LARGE-SCRIPT",
                category="resource_abuse",
                risk_level=RiskLevel.LOW,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                description=f"Script is large ({line_count} lines); review recommended.",
                evidence=f"{line_count} lines (threshold: {self.policy.large_script_threshold})",
                recommendation="Large scripts are harder to audit; consider splitting.",
            ))

        # Run all applicable rules
        if script_type != ScriptType.UNKNOWN:
            rules = self.registry.rules_for(script_type)
            for rule in rules:
                # Inject context + override cache into the rule
                rule._override = self.policy.get_rule_override(rule.rule_id)  # type: ignore[attr-defined]
                rule.ctx = ctx  # type: ignore[attr-defined]
                if not rule._override.enabled:  # type: ignore[attr-defined]
                    continue
                try:
                    rule_findings = rule.check(ctx)
                    findings.extend(rule_findings)
                except Exception as ex:  # pylint: disable=broad-except
                    # A buggy rule must never crash the guard.
                    findings.append(Finding(
                        rule_id=f"GUARD-RULE-ERROR-{rule.rule_id}",
                        category="process_system",
                        risk_level=RiskLevel.LOW,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        description=f"Rule {rule.rule_id} raised an error: {ex}",
                        evidence=str(ex)[:200],
                        recommendation="Fix or disable the failing rule in the policy.",
                    ))

        # Aggregate
        decision, risk_level = _aggregate_decision(findings)
        duration_ms = (time.perf_counter() - start) * 1000.0
        sanitized = self.policy.redact_secrets_in_evidence and any(
            f.category == "secret_leak" for f in findings
        )

        # Sort findings by risk level (highest first)
        risk_order = {
            RiskLevel.CRITICAL: 4,
            RiskLevel.HIGH: 3,
            RiskLevel.MEDIUM: 2,
            RiskLevel.LOW: 1,
            RiskLevel.NONE: 0,
        }
        findings.sort(key=lambda f: risk_order.get(f.risk_level, 0), reverse=True)

        report = SafetyReport(
            tool_name=tool_name,
            script_type=script_type,
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            scan_duration_ms=duration_ms,
            script_hash=script_hash,
            sanitized=sanitized,
            timestamp=datetime.now(timezone.utc).isoformat(),
            summary=_build_summary(decision, risk_level, findings, script_type),
        )

        # Telemetry
        blocked = decision in (Decision.DENY, Decision.NEEDS_HUMAN_REVIEW)
        report_to_span(report, blocked=blocked)

        # Audit
        if emit_audit and self.audit_logger:
            event = AuditEvent(
                timestamp=report.timestamp,
                tool_name=tool_name,
                decision=decision.value,
                risk_level=risk_level.value,
                rule_ids=[f.rule_id for f in findings],
                scan_duration_ms=round(duration_ms, 3),
                sanitized=sanitized,
                blocked=blocked,
                script_hash=script_hash,
                script_type=script_type.value,
            )
            self.audit_logger.log(event)

        return report

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def is_safe(self, script: str, tool_name: str = "unknown", **kwargs) -> bool:
        """Return ``True`` if the script's decision is ``ALLOW``."""
        return self.scan(script, tool_name, **kwargs).decision == Decision.ALLOW

    def should_block(self, script: str, tool_name: str = "unknown", **kwargs) -> bool:
        """Return ``True`` if the script must be blocked (DENY)."""
        return self.scan(script, tool_name, **kwargs).decision == Decision.DENY
