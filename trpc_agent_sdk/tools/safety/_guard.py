"""Decision engine that aggregates rule findings into one report.

The guard is intentionally stateless and synchronous. It does not perform
file writes, network access, process creation, or telemetry emission.
Execution-chain adapters (Filter, wrapper) own audit and tracing.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Iterable, Sequence

from trpc_agent_sdk.tools.safety._exceptions import SafetyGuardError, SafetyScannerError
from trpc_agent_sdk.tools.safety._models import (
    SAFE_RULE_ID,
    RiskLevel,
    SafetyDecision,
    SafetyFinding,
    SafetyReport,
    SafetyScanRequest,
)
from trpc_agent_sdk.tools.safety._policy import POLICY_VERSION, ToolSafetyPolicy
from trpc_agent_sdk.tools.safety._redaction import Redactor, evidence_was_redacted
from trpc_agent_sdk.tools.safety._rules import SafetyRule, default_rules

INTERNAL_ERROR_RULE_ID = "GUARD001_INTERNAL_ERROR"


class ToolSafetyGuard:
    """Stateless scanner that turns a request into a SafetyReport."""

    def __init__(
        self,
        policy: ToolSafetyPolicy,
        *,
        rules: Sequence[SafetyRule] | None = None,
    ) -> None:
        self.policy = policy
        self._rules: list[SafetyRule] = list(rules) if rules is not None \
            else default_rules()
        self._validate_rule_ids()

    @property
    def rules(self) -> list[SafetyRule]:
        return list(self._rules)

    @property
    def policy_hash(self) -> str:
        return self.policy.hash

    @property
    def policy_version(self) -> str:
        return POLICY_VERSION

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        started = time.perf_counter()
        size_error: Exception | None = None
        try:
            self._validate_request_size(request)
        except SafetyScannerError as exc:
            size_error = exc
        findings: list[SafetyFinding] = []
        redactor = Redactor(env_values=request.env.values())
        scan_error: Exception | None = size_error
        if scan_error is None:
            try:
                for rule in self._rules:
                    findings.extend(rule.scan(request, self.policy))
            except SafetyScannerError as exc:
                scan_error = exc
            except SafetyGuardError as exc:
                scan_error = exc
            except Exception as exc:  # pragma: no cover - defensive
                scan_error = exc
        if scan_error is not None:
            findings.append(self._internal_error_finding(scan_error, redactor,
                                                         request))
        findings = _deduplicate(findings)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return self._build_report(request, findings, elapsed_ms, redactor)

    def error_report(
        self,
        request: SafetyScanRequest,
        error: Exception,
    ) -> SafetyReport:
        """Create a fail-closed report when request normalization fails.

        The execution adapters use this instead of dropping an audit event
        when they cannot construct a complete scan request.
        """
        started = time.perf_counter()
        redactor = Redactor(env_values=request.env.values())
        finding = self._internal_error_finding(error, redactor, request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return self._build_report(request, [finding], elapsed_ms, redactor)

    # ----- internals ----- #

    def _validate_rule_ids(self) -> None:
        seen: set[str] = set()
        for rule in self._rules:
            rule_id = getattr(rule, "rule_id", "")
            if not rule_id:
                raise SafetyGuardError(
                    f"rule {rule!r} is missing a stable rule_id")
            if rule_id in seen:
                raise SafetyGuardError(f"duplicate rule_id {rule_id!r}")
            seen.add(rule_id)

    def _validate_request_size(self, request: SafetyScanRequest) -> None:
        limit = self.policy.limits.max_script_bytes
        if limit <= 0:
            return
        if len(request.script.encode("utf-8", errors="ignore")) > limit:
            # Build a finding via the rule catalog so the decision is
            # consistent. We add it through findings below by raising.
            raise SafetyScannerError(
                f"script exceeds max_script_bytes={limit}")

    def _internal_error_finding(
        self,
        exc: Exception,
        redactor: Redactor,
        request: SafetyScanRequest,
    ) -> SafetyFinding:
        # Map policy default for guard errors; "deny" by default.
        mapping = {
            "allow": SafetyDecision.ALLOW,
            "needs_human_review": SafetyDecision.NEEDS_HUMAN_REVIEW,
            "deny": SafetyDecision.DENY,
        }
        decision = mapping.get(self.policy.defaults.guard_error,
                               SafetyDecision.DENY)
        message = type(exc).__name__
        evidence = redactor.build_evidence(
            snippet=f"guard error: {message}",
            language=request.language,
            extras={"error_kind": message},
        )
        return SafetyFinding(
            rule_id=INTERNAL_ERROR_RULE_ID,
            category=RiskCategory.ANALYSIS,
            risk_level=RiskLevel.CRITICAL,
            decision=decision,
            evidence=evidence,
            recommendation=("Internal guard error; failing closed per "
                            "policy.defaults.guard_error."),
        )

    def _build_report(
        self,
        request: SafetyScanRequest,
        findings: list[SafetyFinding],
        elapsed_ms: float,
        redactor: Redactor,
    ) -> SafetyReport:
        script_sha = hashlib.sha256(
            request.script.encode("utf-8", errors="ignore")
        ).hexdigest()
        report_id = _new_report_id()
        if not findings:
            return SafetyReport(
                report_id=report_id,
                decision=SafetyDecision.ALLOW,
                risk_level=RiskLevel.INFO,
                rule_ids=(SAFE_RULE_ID,),
                findings=(),
                recommendation="No safety rules matched.",
                policy_hash=self.policy_hash,
                policy_version=self.policy_version,
                script_sha256=script_sha,
                scan_duration_ms=elapsed_ms,
                redacted=False,
            )
        decision = _aggregate_decision(findings)
        risk_level = _aggregate_risk(findings)
        rule_ids = _stable_rule_ids(findings)
        recommendation = _aggregate_recommendation(findings, decision)
        return SafetyReport(
            report_id=report_id,
            decision=decision,
            risk_level=risk_level,
            rule_ids=rule_ids,
            findings=tuple(findings),
            recommendation=recommendation,
            policy_hash=self.policy_hash,
            policy_version=self.policy_version,
            script_sha256=script_sha,
            scan_duration_ms=elapsed_ms,
            redacted=redactor.active or any(
                evidence_was_redacted(finding.evidence)
                for finding in findings
            ),
        )


# Imports here to avoid circular import at module load.
from trpc_agent_sdk.tools.safety._models import RiskCategory  # noqa: E402


# --------------------------------------------------------------------------- #
# Aggregation helpers (kept here so the module owns its decision surface)
# --------------------------------------------------------------------------- #

_DECISION_RANK: dict[SafetyDecision, int] = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}


def _aggregate_decision(findings: Iterable[SafetyFinding]) -> SafetyDecision:
    ranked = [_DECISION_RANK[f.decision] for f in findings]
    if not ranked:
        return SafetyDecision.ALLOW
    worst = max(ranked)
    for decision, rank in _DECISION_RANK.items():
        if rank == worst:
            return decision
    return SafetyDecision.DENY  # pragma: no cover


def _aggregate_risk(findings: Iterable[SafetyFinding]) -> RiskLevel:
    try:
        return max(f.risk_level for f in findings)
    except ValueError:
        return RiskLevel.INFO


def _stable_rule_ids(findings: Iterable[SafetyFinding]) -> tuple[str, ...]:
    return tuple(sorted({f.rule_id for f in findings}))


def _aggregate_recommendation(
    findings: list[SafetyFinding], decision: SafetyDecision
) -> str:
    if decision == SafetyDecision.DENY:
        return "Block execution and request a human-approved path."
    if decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return "Pause for human review before executing."
    return "Proceed with sandbox and runtime limits."


def _deduplicate(findings: list[SafetyFinding]) -> list[SafetyFinding]:
    """Stable de-duplication on (rule_id, decision, evidence.snippet, line).

    Sorting is rule_id -> risk_level desc -> line so output is stable.
    """

    seen: set[tuple[str, int, str, int]] = set()
    unique: list[SafetyFinding] = []
    for finding in findings:
        key = (finding.rule_id, finding.decision.value,  # type: ignore[union-attr]
               finding.evidence.snippet, finding.evidence.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    def _sort_key(f: SafetyFinding) -> tuple[str, int, int, int]:
        return (-f.risk_level, f.rule_id, f.evidence.line, f.evidence.column)

    return sorted(unique, key=_sort_key)


def _new_report_id() -> str:
    return "rep-" + uuid.uuid4().hex[:16]
