"""Data models for the Script Safety Guard module."""

from __future__ import annotations

import ast
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RiskCategory(str, Enum):
    """Risk categories detected by safety rules."""

    FILE_OPERATIONS = "file_operations"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRETS = "secrets"


class Severity(str, Enum):
    """Severity levels for findings, aligned with three-level decision output."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Decision(str, Enum):
    """Three-level decision output of safety checks."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class Language(str, Enum):
    """Supported script languages."""

    PYTHON = "python"
    BASH = "bash"


class ToolMetadata(BaseModel):
    """Metadata about the tool that triggered the safety check."""

    tool_name: str = Field(default="", description="Name of the tool being executed.")
    skill_name: str = Field(default="", description="Name of the skill (if applicable).")
    invocation_id: str = Field(default="", description="Unique invocation identifier for tracing.")
    agent_name: str = Field(default="", description="Name of the agent invoking the tool.")
    user_id: str = Field(default="", description="User identifier.")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Tool parameters passed by the caller.")


class Finding(BaseModel):
    """A single risk finding produced by a safety rule."""

    rule_id: str = Field(description="Unique rule identifier, e.g. 'FS-001'.")
    category: RiskCategory = Field(description="Risk category this finding belongs to.")
    severity: Severity = Field(description="Severity level of the finding.")
    decision: Decision = Field(description="Suggested decision for this finding.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score between 0 and 1.")
    evidence: str = Field(default="", description="Code snippet that triggered the finding.")
    line_number: int = Field(default=0, ge=0, description="Line number where the risk was found.")
    description: str = Field(default="", description="Human-readable description of the risk.")
    recommendation: str = Field(default="", description="Suggested remediation action.")


class SafetyCheckInput(BaseModel):
    """Input to the ScriptSafetyGuard.check() method."""

    script_content: str = Field(description="The script source code to be checked.")
    language: Language = Field(description="Script language: 'python' or 'bash'.")
    command_args: list[str] = Field(default_factory=list, description="Command-line arguments for execution.")
    working_directory: str = Field(default="", description="Working directory for script execution.")
    environment_variables: dict[str, str] = Field(default_factory=dict,
                                                  description="Environment variables passed to the script.")
    tool_metadata: ToolMetadata = Field(default_factory=ToolMetadata, description="Metadata about the invoking tool.")


class SafetyCheckResult(BaseModel):
    """Output of the ScriptSafetyGuard.check() method."""

    decision: Decision = Field(description="Final aggregated decision.")
    findings: list[Finding] = Field(default_factory=list, description="All findings from rule scans.")
    scan_duration_ms: float = Field(default=0.0, ge=0.0, description="Time spent scanning in milliseconds.")
    scanned_language: Language = Field(description="Language that was scanned.")
    tool_name: str = Field(default="", description="Tool name that triggered the check.")
    invocation_id: str = Field(default="", description="Invocation ID for correlation.")

    @property
    def max_severity(self) -> str:
        """Return the highest severity among all findings, or 'none' if empty."""
        if not self.findings:
            return "none"
        severity_order = [Severity.HIGH, Severity.MEDIUM, Severity.LOW]
        for sev in severity_order:
            if any(f.severity == sev for f in self.findings):
                return sev.value
        return "none"

    @property
    def is_blocked(self) -> bool:
        """Whether execution should be blocked based on the decision."""
        return self.decision == Decision.DENY

    def to_report_dict(self) -> dict:
        """Convert to a structured report dictionary suitable for JSON serialization."""
        return {
            "tool_name":
            self.tool_name,
            "invocation_id":
            self.invocation_id,
            "language":
            self.scanned_language.value,
            "decision":
            self.decision.value,
            "risk_level":
            self.max_severity,
            "is_blocked":
            self.is_blocked,
            "scan_duration_ms":
            self.scan_duration_ms,
            "findings_count":
            len(self.findings),
            "findings": [{
                "rule_id": f.rule_id,
                "risk_category": f.category.value,
                "severity": f.severity.value,
                "decision": f.decision.value,
                "confidence": f.confidence,
                "evidence": f.evidence,
                "line_number": f.line_number,
                "description": f.description,
                "recommendation": f.recommendation,
            } for f in self.findings],
        }

    def to_audit_dict(self) -> dict:
        """Convert to a compact audit event dictionary for JSONL logging.

        Contains at least: tool_name, decision, risk_level, rule_ids,
        duration_ms, is_desensitized, is_blocked.
        """
        return {
            "event": "safety_check",
            "tool_name": self.tool_name,
            "invocation_id": self.invocation_id,
            "decision": self.decision.value,
            "risk_level": self.max_severity,
            "rule_ids": list({f.rule_id
                              for f in self.findings}),
            "duration_ms": self.scan_duration_ms,
            "is_desensitized": True,  # Evidence is always sanitized in audit output
            "is_blocked": self.is_blocked,
            "findings_count": len(self.findings),
        }


class ScanContext(BaseModel):
    """Context object passed to each rule's scan() method."""

    source_code: str = Field(description="The raw script source code.")
    language: Language = Field(description="Script language.")
    ast_tree: Optional[Any] = Field(
        default=None,
        description="Parsed AST tree (ast.Module for Python, None for Bash or parse failure).",
    )
    lines: list[str] = Field(default_factory=list, description="Source code split into lines.")
    working_directory: str = Field(default="", description="Working directory for execution.")
    environment_variables: dict[str, str] = Field(default_factory=dict, description="Environment variables.")
    tool_metadata: ToolMetadata = Field(default_factory=ToolMetadata, description="Tool metadata.")

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_input(
        cls,
        check_input: SafetyCheckInput,
        ast_tree: Optional[ast.Module] = None,
    ) -> "ScanContext":
        """Create a ScanContext from a SafetyCheckInput and optional AST tree."""
        return cls(
            source_code=check_input.script_content,
            language=check_input.language,
            ast_tree=ast_tree,
            lines=check_input.script_content.splitlines(),
            working_directory=check_input.working_directory,
            environment_variables=check_input.environment_variables,
            tool_metadata=check_input.tool_metadata,
        )
