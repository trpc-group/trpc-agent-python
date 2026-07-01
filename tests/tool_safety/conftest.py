# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Shared fixtures for tool safety tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from examples.tool_safety.safety import PolicyConfig
from examples.tool_safety.safety import SafetyScanner


POLICY_PATH = _REPO_ROOT / "examples" / "tool_safety" / "tool_safety_policy.yaml"
SAMPLES_DIR = _REPO_ROOT / "examples" / "tool_safety" / "samples"


@pytest.fixture
def policy() -> PolicyConfig:
    return PolicyConfig.from_yaml(POLICY_PATH)


@pytest.fixture
def scanner(policy: PolicyConfig) -> SafetyScanner:
    return SafetyScanner(policy=policy)


@pytest.fixture
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture
def policy_path() -> Path:
    return POLICY_PATH
