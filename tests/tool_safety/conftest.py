# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Shared fixtures for tool safety tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafetyScanner

_REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = _REPO_ROOT / "examples" / "tool_safety" / "tool_safety_policy.yaml"
SAMPLES_DIR = _REPO_ROOT / "examples" / "tool_safety" / "samples"
MANIFEST_PATH = SAMPLES_DIR / "manifest.yaml"


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


@pytest.fixture
def manifest_path() -> Path:
    return MANIFEST_PATH
