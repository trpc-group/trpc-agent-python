# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Policy loading and validation for the tool script safety guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
import yaml

from ._models import Decision
from ._models import RiskLevel


class ToolSafetyPolicy(BaseModel):
    """Runtime-configurable limits and allow/deny lists."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=lambda: ["echo", "printf", "pwd"])
    denied_paths: list[str] = Field(default_factory=lambda: [
        "/etc",
        "/root",
        "/proc",
        "/sys",
        "~/.ssh",
        "~/.aws",
        "~/.config/gcloud",
        ".env",
        "credentials.json",
    ])
    max_timeout_seconds: float = Field(default=300, gt=0)
    max_output_bytes: int = Field(default=1_000_000, gt=0)
    max_script_bytes: int = Field(default=1_000_000, gt=0)
    max_file_write_bytes: int = Field(default=10_000_000, gt=0)
    max_sleep_seconds: float = Field(default=30, ge=0)
    max_concurrent_tasks: int = Field(default=100, gt=0)
    max_evidence_chars: int = Field(default=240, ge=40, le=2000)
    review_unknown_commands: bool = True
    disabled_rules: set[str] = Field(default_factory=set)
    rule_decisions: dict[str, Decision] = Field(default_factory=dict)
    rule_risk_levels: dict[str, RiskLevel] = Field(default_factory=dict)

    @field_validator("allowed_domains")
    @classmethod
    def _normalize_domains(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if "://" in domain or "/" in domain:
                raise ValueError(f"allowed domain must be a hostname pattern: {value}")
            if domain:
                normalized.append(domain)
        return normalized

    @field_validator("allowed_commands")
    @classmethod
    def _normalize_commands(cls, values: list[str]) -> list[str]:
        return sorted({Path(value.strip()).name for value in values if value.strip()})

    @field_validator("denied_paths")
    @classmethod
    def _normalize_paths(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ToolSafetyPolicy":
        """Load and strictly validate a YAML policy file."""
        policy_path = Path(path)
        with policy_path.open("r", encoding="utf-8") as file:
            data: Any = yaml.safe_load(file)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("tool safety policy must contain a YAML mapping")
        return cls.model_validate(data)

    def decision_for(self, rule_id: str, default: Decision) -> Decision:
        """Return a per-rule decision override when configured."""
        return self.rule_decisions.get(rule_id, default)

    def risk_level_for(self, rule_id: str, default: RiskLevel) -> RiskLevel:
        """Return a per-rule risk override when configured."""
        return self.rule_risk_levels.get(rule_id, default)
