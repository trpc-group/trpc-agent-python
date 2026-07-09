"""ScriptSafetyGuard — Core coordination engine for script safety checks.

This module is the single entry point for all safety scanning. It orchestrates:
1. Code parsing (Python AST / Bash lines)
2. ScanContext construction
3. Rule execution (from RuleRegistry)
4. Decision aggregation
5. Audit logging (structured JSON)
6. OpenTelemetry span attributes + metrics

Usage:
    from trpc_agent_sdk.tools.safety.guard import ScriptSafetyGuard
    from trpc_agent_sdk.tools.safety.policy import load_policy

    guard = ScriptSafetyGuard(policy=load_policy())
    result = guard.check(SafetyCheckInput(script_content=code, language="python"))
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.tools.safety._metrics import (
    record_check,
    record_rule_hit,
    record_scan_duration,
)
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    SafetyCheckInput,
    SafetyCheckResult,
    ScanContext,
    Severity,
)
from trpc_agent_sdk.tools.safety.policy import PolicyConfig, load_policy

# Import rules package to trigger registration
from trpc_agent_sdk.tools.safety.rules import rule_registry  # noqa: F401
from trpc_agent_sdk.tools.safety.scanner.python_scanner import safe_parse

logger = logging.getLogger(__name__)

# Structured audit logger — separate from the module logger so it can be
# directed to a dedicated audit log sink (file, SIEM, etc.) via config.
_audit_logger = logging.getLogger("trpc_agent_sdk.tools.safety.audit")

# Maximum evidence length in audit log (for desensitization)
_MAX_EVIDENCE_LEN = 200


class ScriptSafetyGuard:
    """Facade that orchestrates script safety analysis.

    Typical lifecycle:
        1. Instantiate with a PolicyConfig (or use default).
        2. Call check() for each script execution request.
        3. Inspect SafetyCheckResult.decision to allow/deny/review.

    Thread-safety: instances are safe to share across threads — they hold
    no mutable state after __init__.
    """

    def __init__(self, policy: Optional[PolicyConfig] = None) -> None:
        """Initialize the guard with a policy configuration.

        Args:
            policy: Policy config. If None, built-in defaults are used.
        """
        self._policy: PolicyConfig = policy if policy is not None else load_policy()

    @property
    def policy(self) -> PolicyConfig:
        """Return the active policy configuration."""
        return self._policy

    def check(self, input: SafetyCheckInput) -> SafetyCheckResult:
        """Execute the full safety check pipeline.

        Steps:
            1. Parse source code (Python → AST, Bash → lines only)
            2. Construct ScanContext
            3. Retrieve applicable rules from registry
            4. Execute each rule's scan() method
            5. Aggregate findings into a final decision
            6. Record audit log entry
            7. Record OTel span attributes and metrics

        Args:
            input: The safety check input containing script content, language, etc.

        Returns:
            SafetyCheckResult with decision, findings, and timing information.
        """
        start_time = time.perf_counter()

        # --- Step 1: Parse ---
        ast_tree = None
        parse_findings: list[Finding] = []

        if input.language == Language.PYTHON:
            ast_tree = safe_parse(input.script_content)
            if ast_tree is None and input.script_content.strip():
                # AST parse failed on non-empty code — flag for human review
                parse_findings.append(
                    Finding(
                        rule_id="GUARD-001",
                        category=RiskCategory.PROCESS,
                        severity=Severity.LOW,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        confidence=0.8,
                        evidence=_truncate(input.script_content, 100),
                        description=("Python AST parsing failed. Script may contain syntax errors "
                                     "or use features unsupported by static analysis."),
                        recommendation="Manually review the script before execution.",
                    ))

        # --- Step 2: Build ScanContext ---
        ctx = ScanContext.from_input(input, ast_tree)

        # --- Step 3: Get applicable rules ---
        applicable_rules = rule_registry.get_by_language(ctx.language)

        # --- Step 4: Execute rules ---
        all_findings: list[Finding] = list(parse_findings)
        for rule in applicable_rules:
            try:
                findings = rule.scan(ctx, self._policy)
                all_findings.extend(findings)
            except Exception as e:
                # Rule execution error — log and continue; do not crash the guard
                logger.error(
                    "Rule %s raised an exception during scan: %s",
                    rule.rule_id,
                    e,
                    exc_info=True,
                )
                all_findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        severity=Severity.MEDIUM,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        confidence=0.5,
                        description=f"Rule execution error: {type(e).__name__}: {e}",
                        recommendation="Investigate rule failure; consider manual review.",
                    ))

        # --- Step 5: Aggregate decision ---
        final_decision = _aggregate_decision(all_findings)

        # --- Timing ---
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        # --- Build result ---
        result = SafetyCheckResult(
            decision=final_decision,
            findings=all_findings,
            scan_duration_ms=round(elapsed_ms, 3),
            scanned_language=input.language,
            tool_name=input.tool_metadata.tool_name,
            invocation_id=input.tool_metadata.invocation_id,
        )

        # --- Step 6: Audit log (Python logger) ---
        _emit_audit_log(input, result)

        # --- Step 7: OTel ---
        _record_otel(input, result)

        # --- Step 8: File-based report & audit output (config-driven) ---
        _write_report_and_audit(self._policy, input, result)

        return result


# ---------------------------------------------------------------------------
# Decision aggregation
# ---------------------------------------------------------------------------


def _aggregate_decision(findings: list[Finding]) -> Decision:
    """Aggregate multiple findings into a single final decision.

    Logic (strictest-wins):
    - Any finding with decision=DENY → final DENY
    - Any finding with decision=NEEDS_HUMAN_REVIEW → final NEEDS_HUMAN_REVIEW
    - Otherwise → ALLOW
    """
    if not findings:
        return Decision.ALLOW

    has_deny = any(f.decision == Decision.DENY for f in findings)
    if has_deny:
        return Decision.DENY

    has_review = any(f.decision == Decision.NEEDS_HUMAN_REVIEW for f in findings)
    if has_review:
        return Decision.NEEDS_HUMAN_REVIEW

    return Decision.ALLOW


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _emit_audit_log(input: SafetyCheckInput, result: SafetyCheckResult) -> None:
    """Emit a structured JSON audit log entry.

    The audit log contains:
    - Decision and finding summary (not full script content)
    - Desensitized evidence (truncated, secrets masked)
    - Tool and invocation metadata for correlation
    """
    findings_summary = [{
        "rule_id": f.rule_id,
        "category": f.category.value if hasattr(f.category, "value") else str(f.category),
        "severity": f.severity.value,
        "decision": f.decision.value,
        "confidence": f.confidence,
        "evidence": _sanitize_evidence(f.evidence),
        "line_number": f.line_number,
        "description": f.description,
    } for f in result.findings]

    audit_entry = {
        "event": "safety_check",
        "decision": result.decision.value,
        "language": result.scanned_language.value,
        "scan_duration_ms": result.scan_duration_ms,
        "findings_count": len(result.findings),
        "max_severity": result.max_severity,
        "tool_name": result.tool_name,
        "invocation_id": result.invocation_id,
        "agent_name": input.tool_metadata.agent_name,
        "user_id": input.tool_metadata.user_id,
        "script_length": len(input.script_content),
        "findings": findings_summary,
    }

    _audit_logger.info(json.dumps(audit_entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# OTel instrumentation
# ---------------------------------------------------------------------------


def _record_otel(input: SafetyCheckInput, result: SafetyCheckResult) -> None:
    """Record OTel span attributes and metrics.

    Span attributes are written to the current active span (if any).
    Metrics are recorded via the _metrics module.
    """
    # --- Span attributes ---
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            prefix = "trpc.python.agent.tool.safety"
            span.set_attribute(f"{prefix}.decision", result.decision.value)
            span.set_attribute(f"{prefix}.language", result.scanned_language.value)
            span.set_attribute(f"{prefix}.scan_duration_ms", result.scan_duration_ms)
            span.set_attribute(f"{prefix}.findings_count", len(result.findings))
            span.set_attribute(f"{prefix}.max_severity", result.max_severity)
            span.set_attribute(f"{prefix}.tool_name", result.tool_name)
            span.set_attribute(f"{prefix}.invocation_id", result.invocation_id)
            span.set_attribute(f"{prefix}.is_blocked", result.is_blocked)
    except ImportError:
        pass  # OTel not installed — skip silently

    # --- Metrics ---
    record_check(
        decision=result.decision.value,
        language=result.scanned_language.value,
        tool_name=result.tool_name,
    )
    record_scan_duration(
        duration_ms=result.scan_duration_ms,
        language=result.scanned_language.value,
        decision=result.decision.value,
    )
    for finding in result.findings:
        category_value = (finding.category.value if hasattr(finding.category, "value") else str(finding.category))
        record_rule_hit(
            rule_id=finding.rule_id,
            category=category_value,
            severity=finding.severity.value,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int = _MAX_EVIDENCE_LEN) -> str:
    """Truncate text to max_len, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _sanitize_evidence(evidence: str) -> str:
    """Sanitize evidence for audit log.

    - Truncate to _MAX_EVIDENCE_LEN characters
    - Mask potential secrets (simple heuristic)
    """
    truncated = _truncate(evidence, _MAX_EVIDENCE_LEN)
    # Basic secret masking: patterns like API keys, tokens, passwords
    # Only mask if it looks like a key=value or assignment with a long value
    import re

    # Mask values after common secret-like prefixes
    masked = re.sub(
        r'((?:key|token|secret|password|passwd|api_key|apikey|auth)\s*[=:]\s*)["\']?([^\s"\']{8,})["\']?',
        r"\1****",
        truncated,
        flags=re.IGNORECASE,
    )
    return masked


# ---------------------------------------------------------------------------
# File-based report & audit output (config-driven)
# ---------------------------------------------------------------------------


def _write_report_and_audit(
    policy: PolicyConfig,
    input: SafetyCheckInput,
    result: SafetyCheckResult,
) -> None:
    """Write structured report file and audit JSONL entry based on output config.

    This is the config-driven file output that runs in the normal check() pipeline.
    Failures are logged as warnings but never raise — they must not block the main flow.
    """
    output_cfg = policy.output

    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y%m%dT%H%M%SZ")

    # --- Structured Report ---
    if output_cfg.report.enabled:
        try:
            report_dir = Path(output_cfg.report.dir)
            report_dir.mkdir(parents=True, exist_ok=True)

            # Resolve filename from template
            tool_name_safe = (result.tool_name or "unknown").replace("/", "_").replace(" ", "_")
            filename = output_cfg.report.filename_template.format(
                tool_name=tool_name_safe,
                invocation_id=result.invocation_id or "no_id",
                timestamp=timestamp_str,
            )

            report_path = report_dir / filename
            report_data = result.to_report_dict()
            report_data["timestamp"] = now.isoformat()

            # Sanitize evidence in report output
            for finding in report_data.get("findings", []):
                finding["evidence"] = _sanitize_evidence(finding.get("evidence", ""))

            # Atomic write: write to temp file then rename
            fd, tmp_path = tempfile.mkstemp(dir=str(report_dir), suffix=".tmp", prefix=".report_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                    json.dump(report_data, tmp_f, indent=2, ensure_ascii=False)
                    tmp_f.write("\n")
                os.replace(tmp_path, str(report_path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            logger.debug("Safety report written to: %s", report_path)
        except Exception as e:
            logger.warning("Failed to write safety report: %s", e)

    # --- Audit JSONL ---
    if output_cfg.audit.enabled:
        try:
            audit_path = Path(output_cfg.audit.file)
            audit_path.parent.mkdir(parents=True, exist_ok=True)

            audit_entry = result.to_audit_dict()
            audit_entry["timestamp"] = now.isoformat()

            with audit_path.open("a", encoding="utf-8") as af:
                af.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")

            logger.debug("Audit log appended to: %s", audit_path)
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)
