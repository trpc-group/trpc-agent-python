"""Pydantic models for the safety guard.

Invariants
----------
* Reports never serialize ``script``, raw ``argv``, ``cwd``, or environment
  values. Use :class:`Evidence` for bounded, redacted snippets.
* ``rule_ids`` are sorted and de-duplicated for stable output.
* Aggregate decision precedence: ``deny > needs_human_review > allow``.
* Aggregate risk precedence: ``critical > high > medium > low > info``.
* Policy hash is SHA-256 over canonical JSON after validation.
"""

from __future__ import annotations

import enum
import hashlib
from typing import Annotated, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer


class ToolKind(str, enum.Enum):
    """Where the scanned input originates."""

    TOOL = "tool"
    MCP = "mcp"
    SKILL = "skill"
    CODE_EXECUTOR = "code_executor"
    UNKNOWN = "unknown"


class ScriptLanguage(str, enum.Enum):
    """Script languages the scanner understands."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


class SafetyDecision(str, enum.Enum):
    """Three-state decision emitted by the guard."""

    ALLOW = "allow"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    DENY = "deny"


class RiskLevel(int, enum.Enum):
    """Severity ranking used for stable ordering of findings."""

    INFO = 10
    LOW = 20
    MEDIUM = 30
    HIGH = 40
    CRITICAL = 50

    def label(self) -> str:
        return self.name.lower()


# Annotated alias so Pydantic v2 serializes risk levels as lowercase
# labels (``"high"``, ``"critical"``) instead of opaque integers. The
# core value remains comparable as an int for sorting / aggregation.
RiskLevelLabel = Annotated[
    RiskLevel,
    PlainSerializer(
        lambda v: v.label(),
        return_type=str,
        when_used="always",
    ),
]


class RiskCategory(str, enum.Enum):
    """Top-level risk category for grouping findings."""

    FILE = "file"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRET = "secret"
    ANALYSIS = "analysis"
    SAFE = "safe"


# Stable rule id emitted by allow-path reports when no finding matches.
SAFE_RULE_ID = "SAFE000"

# Hard cap on evidence snippet length after redaction.
EVIDENCE_MAX_CHARS = 240


class Evidence(BaseModel):
    """Bounded, redacted proof that a rule fired.

    ``snippet`` is always redacted before being placed here. ``location``
    uses 1-based line numbers when available; ``column`` is 1-based when
    known and ``0`` when not applicable.
    """

    model_config = ConfigDict(extra="forbid")

    snippet: str = Field(default="", description="Redacted source slice")
    line: int = Field(default=0, ge=0)
    column: int = Field(default=0, ge=0)
    language: ScriptLanguage = Field(default=ScriptLanguage.UNKNOWN)
    extras: Mapping[str, str] = Field(default_factory=dict)

    def model_dump_json(self, **kwargs: Any) -> str:  # type: ignore[override]
        kwargs.setdefault("exclude_none", True)
        return super().model_dump_json(**kwargs)


class SafetyScanRequest(BaseModel):
    """Normalized input handed to the guard.

    ``script`` and ``env`` are marked ``repr=False`` so logs never echo
    raw content even if someone prints the model instance.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    tool_kind: ToolKind = ToolKind.UNKNOWN
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    script: str = Field(default="", repr=False)
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = Field(default_factory=dict, repr=False)
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    requested_timeout_seconds: float | None = None
    requested_output_bytes: int | None = None


class SafetyFinding(BaseModel):
    """A single rule match."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: RiskCategory
    risk_level: RiskLevelLabel
    decision: SafetyDecision
    evidence: Evidence
    recommendation: str


class SafetyReport(BaseModel):
    """Aggregated scan result for one request.

    Reports are immutable by design. They never carry raw scripts or env
    values; downstream code can safely serialize them to JSON.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    decision: SafetyDecision
    risk_level: RiskLevelLabel
    rule_ids: tuple[str, ...]
    findings: tuple[SafetyFinding, ...]
    recommendation: str
    policy_hash: str
    policy_version: str
    script_sha256: str
    scan_duration_ms: float
    redacted: bool

    def model_dump_json(self, **kwargs: Any) -> str:  # type: ignore[override]
        kwargs.setdefault("exclude_none", True)
        return super().model_dump_json(**kwargs)

    @classmethod
    def combine(
        cls,
        reports: list[SafetyReport],
        *,
        report_id: str,
        policy_hash: str,
        policy_version: str,
        scan_duration_ms: float,
    ) -> SafetyReport:
        """Combine multiple reports into one decision.

        Used by the CodeExecutor wrapper when several code blocks share a
        single execution attempt.
        """

        findings: list[SafetyFinding] = []
        script_hashes: list[str] = []
        any_redacted = False
        for report in reports:
            findings.extend(report.findings)
            script_hashes.append(report.script_sha256)
            any_redacted = any_redacted or report.redacted
        decision = _aggregate_decision(findings)
        risk_level = _aggregate_risk(findings)
        rule_ids = _stable_rule_ids(findings)
        recommendation = _aggregate_recommendation(findings, decision)
        if not findings:
            return cls(
                report_id=report_id,
                decision=SafetyDecision.ALLOW,
                risk_level=RiskLevel.INFO,
                rule_ids=(SAFE_RULE_ID, ),
                findings=(),
                recommendation="No safety rules matched.",
                policy_hash=policy_hash,
                policy_version=policy_version,
                script_sha256=hashlib.sha256("\n".join(script_hashes).encode("utf-8", errors="ignore")).hexdigest(),
                scan_duration_ms=scan_duration_ms,
                redacted=False,
            )
        return cls(
            report_id=report_id,
            decision=decision,
            risk_level=risk_level,
            rule_ids=rule_ids,
            findings=tuple(findings),
            recommendation=recommendation,
            policy_hash=policy_hash,
            policy_version=policy_version,
            script_sha256=hashlib.sha256("\n".join(script_hashes).encode("utf-8", errors="ignore")).hexdigest(),
            scan_duration_ms=scan_duration_ms,
            redacted=any_redacted,
        )


class SafetyAuditEvent(BaseModel):
    """One line in an audit log per scan attempt.

    Audit events deliberately exclude raw scripts, arguments, environment
    values, cwd, and unredacted evidence. They carry enough information to
    answer "who, what, when, why, blocked?" and nothing more.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    timestamp: str
    report_id: str
    tool_name: str
    tool_kind: ToolKind
    decision: SafetyDecision
    risk_level: RiskLevelLabel
    rule_ids: tuple[str, ...]
    duration_ms: float
    redacted: bool
    execution_blocked: bool
    policy_hash: str
    policy_version: str
    script_sha256: str
    scanner_version: str = "1.0.0"
    invocation_id: str | None = None


# --------------------------------------------------------------------------- #
# Aggregation helpers
# --------------------------------------------------------------------------- #

_DECISION_RANK: dict[SafetyDecision, int] = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}


def _aggregate_decision(findings: list[SafetyFinding]) -> SafetyDecision:
    if not findings:
        return SafetyDecision.ALLOW
    worst = max(findings, key=lambda f: _DECISION_RANK[f.decision])
    return worst.decision


def _aggregate_risk(findings: list[SafetyFinding]) -> RiskLevel:
    if not findings:
        return RiskLevel.INFO
    return max(f.risk_level for f in findings)


def _stable_rule_ids(findings: list[SafetyFinding]) -> tuple[str, ...]:
    return tuple(sorted({f.rule_id for f in findings}))


def _aggregate_recommendation(findings: list[SafetyFinding], decision: SafetyDecision) -> str:
    if not findings:
        return "No safety rules matched."
    if decision == SafetyDecision.DENY:
        return "Block execution and request a human-approved path."
    if decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return "Pause for human review before executing."
    return "Proceed with sandbox and runtime limits."
