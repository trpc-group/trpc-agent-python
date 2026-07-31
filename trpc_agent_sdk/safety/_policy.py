# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Strict YAML policy loading, hashing, and last-known-good reload."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from typing import Literal
from typing import Optional

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver
from yaml.tokens import AliasToken
from yaml.tokens import AnchorToken

from ._models import RiskLevel
from ._models import SafetyDecision
from ._redaction import sha256_text


class RulePolicy(BaseModel):
    """Per-rule enable and severity/decision override."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1, max_length=96)
    enabled: bool = True
    risk_override: Optional[RiskLevel] = None
    decision_override: Optional[SafetyDecision] = None


class FailurePolicy(BaseModel):
    """Fail-closed outcomes for incomplete or failed analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parse_failure: SafetyDecision = SafetyDecision.NEEDS_HUMAN_REVIEW
    unsupported_language: SafetyDecision = SafetyDecision.NEEDS_HUMAN_REVIEW
    unknown: SafetyDecision = SafetyDecision.NEEDS_HUMAN_REVIEW
    budget_exceeded: SafetyDecision = SafetyDecision.NEEDS_HUMAN_REVIEW
    scanner_internal_error: SafetyDecision = SafetyDecision.DENY

    @field_validator("parse_failure", "unsupported_language", "unknown", "budget_exceeded", "scanner_internal_error")
    @classmethod
    def prohibit_failure_allow(cls, value: SafetyDecision) -> SafetyDecision:
        if value is SafetyDecision.ALLOW:
            raise ValueError("analysis failure modes cannot be configured to allow")
        return value


class NestedLimits(BaseModel):
    """Budgets shared by one root and all nested scans."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_depth: int = Field(default=3, ge=0, le=8)
    max_total_bytes: int = Field(default=512_000, ge=1_024, le=5_000_000)
    max_base64_decode_bytes: int = Field(default=64_000, ge=0, le=1_000_000)
    max_children: int = Field(default=32, ge=0, le=256)


class RedactionPolicy(BaseModel):
    """Limits applied before any observation fan-out."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: Literal[True] = True
    max_depth: int = Field(default=6, ge=1, le=16)
    max_items: int = Field(default=64, ge=1, le=512)
    max_string_length: int = Field(default=512, ge=32, le=8_192)
    max_fields: int = Field(default=64, ge=1, le=512)


class AuditPolicy(BaseModel):
    """Audit declaration; a sink path is still explicitly supplied by callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False


class RuntimeLimits(BaseModel):
    """Runtime-only declarations not enforced by the static scanner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enforcement: Literal["declaration_only"] = "declaration_only"
    timeout_seconds: Optional[float] = Field(default=None, gt=0)
    cpu_percent: Optional[int] = Field(default=None, ge=1, le=100)
    memory_mb: Optional[int] = Field(default=None, ge=1)
    max_pids: Optional[int] = Field(default=None, ge=1)
    network_allowed: Optional[bool] = None


class SafetyPolicy(BaseModel):
    """A normalized, deeply immutable effective safety policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"]
    policy_version: str = Field(default="default-v1", min_length=1, max_length=128)
    whitelisted_domains: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    forbidden_paths: tuple[str, ...] = ("/", "/etc", "/root", "/boot", "/dev", "/proc", "/sys")
    sensitive_paths: tuple[str, ...] = (
        "/home/<user>/.ssh",
        "/home/<user>/.aws",
        "/home/<user>/.config/gcloud",
        ".env",
        "credentials",
    )
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ("echo", "printf", "pwd", "ls")
    denied_commands: tuple[str, ...] = ("mkfs", "shutdown", "reboot")
    rules: tuple[RulePolicy, ...] = ()
    deny_threshold: RiskLevel = RiskLevel.HIGH
    review_threshold: RiskLevel = RiskLevel.MEDIUM
    failures: FailurePolicy = Field(default_factory=FailurePolicy)
    block_on_review: Literal[True] = True
    nested: NestedLimits = Field(default_factory=NestedLimits)
    max_findings: int = Field(default=128, ge=1, le=2_048)
    max_evidence_length: int = Field(default=512, ge=32, le=8_192)
    redaction: RedactionPolicy = Field(default_factory=RedactionPolicy)
    audit: AuditPolicy = Field(default_factory=AuditPolicy)
    runtime_limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    policy_hash: str = Field(default="", min_length=0, max_length=64)

    @field_validator(
        "whitelisted_domains",
        "forbidden_paths",
        "sensitive_paths",
        "allowed_paths",
        "allowed_commands",
        "denied_commands",
        mode="before",
    )
    @classmethod
    def normalize_string_tuple(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("policy list fields must be YAML sequences")
        normalized = {str(item).strip() for item in value if str(item).strip()}
        return tuple(sorted(normalized))

    @field_validator("rules", mode="before")
    @classmethod
    def normalize_rules(cls, value: Any) -> tuple[dict[str, Any], ...]:
        if value is None:
            return ()
        if isinstance(value, dict):
            result = []
            for rule_id, configuration in sorted(value.items()):
                if configuration is None:
                    configuration = {}
                if not isinstance(configuration, dict):
                    raise ValueError("each rule configuration must be a mapping")
                result.append({"rule_id": rule_id, **configuration})
            return tuple(result)
        if isinstance(value, (list, tuple)):
            return tuple(value)
        raise ValueError("rules must be a mapping or sequence")

    @model_validator(mode="after")
    def validate_thresholds_and_hash(self) -> "SafetyPolicy":
        rank = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }
        if rank[self.review_threshold] > rank[self.deny_threshold]:
            raise ValueError("review_threshold must not exceed deny_threshold")
        rule_ids = [item.rule_id for item in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("duplicate rule_id in rules")
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "policy_hash", sha256_text(canonical))
        return self

    def rule_policy(self, rule_id: str) -> Optional[RulePolicy]:
        """Return a read-only per-rule override."""
        return next((item for item in self.rules if item.rule_id == rule_id), None)

    @classmethod
    def default(cls) -> "SafetyPolicy":
        """Create the built-in deterministic policy without disk I/O."""
        return cls(schema_version="1")


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate and merge keys."""


def _construct_unique_mapping(loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<":
            raise ConstructorError("while constructing a mapping", node.start_mark, "YAML merge keys are not allowed",
                                   key_node.start_mark)
        if key in mapping:
            raise ConstructorError("while constructing a mapping", node.start_mark, f"duplicate key: {key}",
                                   key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _load_policy_text(text: str) -> SafetyPolicy:
    for token in yaml.scan(text):
        if isinstance(token, (AnchorToken, AliasToken)):
            raise ValueError("YAML anchors and aliases are not allowed in safety policies")
    raw = yaml.load(text, Loader=_StrictSafeLoader)
    if not isinstance(raw, dict):
        raise ValueError("safety policy root must be a mapping")
    return SafetyPolicy.model_validate(raw)


class PolicyLoader:
    """Load and explicitly reload one policy with last-known-good semantics."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._snapshot: Optional[SafetyPolicy] = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def snapshot(self) -> SafetyPolicy:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("policy has not been loaded")
            return self._snapshot

    def load(self) -> SafetyPolicy:
        """Perform the initial UTF-8 read and validation."""
        policy = _load_policy_text(self._path.read_text(encoding="utf-8"))
        with self._lock:
            self._snapshot = policy
        return policy

    def reload(self) -> SafetyPolicy:
        """Atomically replace the snapshot; validation errors retain the old one."""
        candidate = _load_policy_text(self._path.read_text(encoding="utf-8"))
        with self._lock:
            self._snapshot = candidate
            return candidate


def load_policy(path: str | Path) -> SafetyPolicy:
    """Convenience loader for callers that do not need reload."""
    return PolicyLoader(path).load()
