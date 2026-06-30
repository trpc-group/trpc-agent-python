# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loading helpers for tool safety."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import Optional

import yaml

from ._types import SafetyPolicy

POLICY_ENV_VAR = "TRPC_AGENT_TOOL_SAFETY_POLICY"


def load_policy(policy_path: Optional[str] = None,
                data: Optional[dict[str, Any]] = None) -> SafetyPolicy:
    """Load a safety policy from explicit data, a YAML file, or defaults."""
    if data is not None:
        return SafetyPolicy.model_validate(data)

    path_value = policy_path or os.getenv(POLICY_ENV_VAR)
    if not path_value:
        return SafetyPolicy()

    path = Path(path_value).expanduser()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"tool safety policy must be a mapping: {path}")

    policy = SafetyPolicy.model_validate(raw)
    if policy.audit_log_path:
        audit_path = Path(policy.audit_log_path).expanduser()
        if not audit_path.is_absolute():
            policy.audit_log_path = str(path.parent / audit_path)
    return policy
