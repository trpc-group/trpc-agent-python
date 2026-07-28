"""Load the small, review-specific governance policy set."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class GovernancePolicy(BaseModel):
    allowed_commands: set[str] = Field(default_factory=set)
    denied_commands: set[str] = Field(default_factory=set)
    dangerous_arguments: set[str] = Field(default_factory=set)
    protected_paths: set[str] = Field(default_factory=set)
    allowed_environment: set[str] = Field(default_factory=set)
    allowed_network_targets: set[str] = Field(default_factory=set)
    max_timeout_seconds: float = 300
    max_memory_mb: int = 1024


def load_policy(path: str | Path | None = None) -> GovernancePolicy:
    policy_path = Path(path) if path else Path(__file__).with_name("default_policy.yaml")
    return GovernancePolicy.model_validate(yaml.safe_load(policy_path.read_text(encoding="utf-8")))
