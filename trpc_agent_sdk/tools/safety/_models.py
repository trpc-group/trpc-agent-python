# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data contracts for tool script safety scanning."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from pydantic import field_validator
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

POLICY_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_OUTPUT_BYTES = 1024 * 1024
DEFAULT_LONG_SLEEP_SECONDS = 60
DEFAULT_LARGE_WRITE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 32


class SafetyDecision(str, Enum):
    """Final action for a scan."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class RiskLevel(str, Enum):
    """Risk severity."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCategory(str, Enum):
    """Supported risk categories."""

    FILE = "file"
    NETWORK = "network"
    PROCESS = "process"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    SECRET = "secret"
    POLICY = "policy"


class ScriptLanguage(str, Enum):
    """Languages understood by the guard."""

    PYTHON = "python"
    BASH = "bash"


class ToolMetadata(BaseModel):
    """Metadata associated with an execution request."""

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ScriptPayload(BaseModel):
    """One script or command payload."""

    language: ScriptLanguage
    content: str
    source: str = "inline"
    argv: list[str] = Field(default_factory=list)
    stdin: str = ""


class ScriptScanRequest(BaseModel):
    """Normalized input passed to the scanner."""

    payloads: list[ScriptPayload] = Field(default_factory=list)
    cwd: str = ""
    execution_home: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    metadata: ToolMetadata
    requested_timeout_seconds: float | None = None
    effective_timeout_seconds: float
    timeout_arg_name: str | None = None
    max_output_bytes: int
    applicable: bool = True
    background: bool = False
    tty: bool = False


class SafetyFinding(BaseModel):
    """One matched safety rule."""

    category: RiskCategory
    risk_level: RiskLevel
    rule_id: str
    evidence: str
    recommendation: str
    decision: SafetyDecision


class SafetyReport(BaseModel):
    """Structured result returned by every scan."""

    decision: SafetyDecision
    risk_level: RiskLevel
    findings: list[SafetyFinding] = Field(default_factory=list)
    duration_ms: float
    redacted: bool
    summary: str
    applicable: bool = True
    effective_timeout_seconds: float | None = None
    max_output_bytes: int

    @property
    def rule_ids(self) -> list[str]:
        """Return stable unique rule ids."""
        return sorted({finding.rule_id for finding in self.findings})

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible report."""
        return self.model_dump(mode="json")


class SafetyAuditEvent(BaseModel):
    """Audit event emitted before execution."""

    timestamp: str
    tool_name: str
    decision: SafetyDecision
    risk_level: RiskLevel
    rule_ids: list[str]
    duration_ms: float
    redacted: bool
    execution_blocked: bool


DECISION_PRIORITY = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}

RISK_PRIORITY = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class _StrictPolicyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _StrictPolicyLoader, node: MappingNode, deep: bool = False) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate policy field: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictPolicyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class ToolSafetyPolicy(BaseModel):
    """Configurable safety policy."""

    model_config = ConfigDict(extra="forbid")

    version: int = POLICY_VERSION
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=lambda: ["~/.ssh", ".env", "/etc/shadow"])
    max_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_OUTPUT_BYTES
    long_sleep_seconds: int = DEFAULT_LONG_SLEEP_SECONDS
    large_write_bytes: int = DEFAULT_LARGE_WRITE_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value != POLICY_VERSION:
            raise ValueError(f"unsupported policy version: {value}")
        return value

    @field_validator(
        "max_timeout_seconds",
        "max_output_bytes",
        "long_sleep_seconds",
        "large_write_bytes",
        "max_concurrency",
    )
    @classmethod
    def _validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("policy limits must be greater than zero")
        return value

    @field_validator("allowed_domains", "allowed_commands", "forbidden_paths")
    @classmethod
    def _validate_entries(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("policy list entries must not be empty")
        return normalized

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ToolSafetyPolicy":
        """Load a policy from YAML."""
        policy_path = Path(path)
        try:
            raw = yaml.load(
                policy_path.read_text(encoding="utf-8"),
                Loader=_StrictPolicyLoader,
            )
        except (OSError, yaml.YAMLError) as error:
            raise ValueError(f"unable to load tool safety policy: {policy_path}") from error
        if not isinstance(raw, dict):
            raise ValueError("tool safety policy must be a YAML mapping")
        try:
            return cls.model_validate(raw)
        except ValidationError as error:
            fields = sorted(
                {".".join(str(item) for item in detail["loc"])
                 for detail in error.errors(include_input=False)})
            raise ValueError(f"invalid tool safety policy fields: {', '.join(fields)}") from error
