# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy model for the Tool Script Safety Guard.

Loads and validates tool_safety_policy.yaml configuration.
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from ._types import Decision
from ._types import RiskLevel
from ._types import RiskType


class PolicyRuleConfig(BaseModel):
    """Configuration for a single safety rule."""
    rule_id: str
    enabled: bool = True
    risk_type: RiskType
    severity: RiskLevel
    decision: Decision

    def effective_decision(self) -> Decision:
        """Return the effective decision considering enabled state."""
        if not self.enabled:
            return Decision.ALLOW
        return self.decision


class WhitelistConfig(BaseModel):
    """Whitelist configuration for trusted items."""
    domains: list[str] = []
    commands: list[str] = []
    paths: list[str] = []


class BlocklistConfig(BaseModel):
    """Blocklist configuration for forbidden items."""
    paths: list[str] = []
    commands: list[str] = []


class SafetyPolicy(BaseModel):
    """Root policy configuration for the Tool Script Safety Guard.

    Loaded from tool_safety_policy.yaml.
    """
    version: str = "1.0"
    max_script_size_bytes: int = 1_048_576
    max_scan_time_ms: int = 1000
    default_decision: Decision = Decision.DENY
    rules: list[PolicyRuleConfig] = []
    whitelist: WhitelistConfig = WhitelistConfig()
    blocklist: BlocklistConfig = BlocklistConfig()

    @classmethod
    def load(cls, path: str | Path) -> "SafetyPolicy":
        """Load policy from a YAML file path."""
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def get_enabled_rules(self) -> list[PolicyRuleConfig]:
        """Return only the rules that are currently enabled."""
        return [r for r in self.rules if r.enabled]
