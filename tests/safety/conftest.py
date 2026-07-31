# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for safety tests; no source fixture is executed."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.safety import SafetyPolicy
from trpc_agent_sdk.safety import SafetyScanner

CANARY = "TEST_SECRET_DO_NOT_LEAK_9f6c2e"


@pytest.fixture()
def policy() -> SafetyPolicy:
    return SafetyPolicy(
        schema_version="1",
        policy_version="test-v1",
        whitelisted_domains=("api.example.com", "localhost", "127.0.0.1", "::1"),
    )


@pytest.fixture()
def scanner(policy: SafetyPolicy) -> SafetyScanner:
    return SafetyScanner(policy)
