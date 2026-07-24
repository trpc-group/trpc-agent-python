# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration loading for tool script safety policies."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic import Field


class ToolSafetyPolicy(BaseModel):
    """Policy values that can be changed without modifying scanner code."""

    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=lambda: ["echo", "pwd", "ls"])
    forbidden_paths: list[str] = Field(
        default_factory=lambda: ["~/.ssh", ".env", "credentials.json", "/etc/shadow", "/root"])
    max_timeout_seconds: int = 60
    max_output_bytes: int = 1_000_000
    deny_risk_levels: list[str] = Field(default_factory=lambda: ["critical", "high"])
    review_risk_levels: list[str] = Field(default_factory=lambda: ["medium"])

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ToolSafetyPolicy":
        """Load and validate a YAML policy file."""
        with Path(path).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return cls.model_validate(data)
