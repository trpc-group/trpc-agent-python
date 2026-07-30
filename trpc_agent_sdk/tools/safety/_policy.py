#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Strict YAML policy loading for the tool script safety guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
import yaml

DEFAULT_FORBIDDEN_PATHS = [
    "~/.ssh",
    "~/.aws",
    "~/.config/gcloud",
    ".env",
    ".env.*",
    "/etc",
    "/root",
    "/var/run/docker.sock",
]

DEFAULT_PROTECTED_WRITE_PATHS = [
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/System",
    r"C:\Windows",
    r"C:\Program Files",
]


class ToolSafetyPolicy(BaseModel):
    """Configuration that changes decisions without changing scanner code."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(
        default_factory=lambda: ["echo", "printf", "pwd", "ls", "cat", "head", "tail", "grep", "sort", "wc"])
    forbidden_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_PATHS))
    protected_write_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_PROTECTED_WRITE_PATHS))
    max_timeout_seconds: float = Field(default=300, gt=0)
    max_output_bytes: int = Field(default=1_048_576, gt=0)
    max_file_write_bytes: int = Field(default=10_485_760, gt=0)
    max_sleep_seconds: float = Field(default=30, ge=0)
    max_script_lines: int = Field(default=2_000, gt=0)
    evidence_max_chars: int = Field(default=240, ge=32, le=2_000)
    disabled_rules: set[str] = Field(default_factory=set)

    @field_validator("allowed_domains", mode="after")
    @classmethod
    def _normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or "/" in domain or "://" in domain:
                raise ValueError(f"allowed domain must be a hostname, got {value!r}")
            normalized.append(domain)
        return list(dict.fromkeys(normalized))

    @field_validator("allowed_commands", mode="after")
    @classmethod
    def _normalize_commands(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or any(char.isspace() for char in value) for value in normalized):
            raise ValueError("allowed_commands entries must be individual command names")
        return list(dict.fromkeys(normalized))


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate keys."""


def _construct_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate policy key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_policy(path: str | Path) -> ToolSafetyPolicy:
    """Load and strictly validate a YAML policy."""

    policy_path = Path(path)
    try:
        raw = yaml.load(policy_path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid tool safety policy YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("tool safety policy must contain a mapping at the top level")
    return ToolSafetyPolicy.model_validate(raw)
